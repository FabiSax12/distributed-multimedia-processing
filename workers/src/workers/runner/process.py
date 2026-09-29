"""Procesa un mensaje de trabajo de punta a punta y lo reporta al coordinador.

Protocolo (el mismo de `coordinator/scripts/fake_worker.py`):

1. `SubTaskMessage` ilegible -> se borra (mensaje envenenado, reintentarlo no
   lo arregla).
2. Caso cancelado (`Cases.cancel_requested`, lectura consistente) -> se
   reporta `cancelled` sin procesar y se borra.
3. `ApproximateReceiveCount > 1` -> `retrying` (otro worker se cayó o venció
   el visibility timeout).
4. `assigned` -> descarga del dataset -> `running` con progreso -> operación
   -> subida a `results/{case}/{subtask}/` -> `completed`.
5. Error esperable (`OpError`) -> `failed` con su `ErrorCode`.
6. El mensaje se borra DESPUÉS de publicar el terminal. Si el worker se cae
   entre las dos cosas, SQS lo reentrega y el barrier del coordinador ignora el
   terminal duplicado (`closed` en Cases).
7. Cualquier otra excepción (S3 caído, bug) -> ni terminal ni borrado: SQS lo
   reintenta hasta `maxReceiveCount` y la DLQ se lo entrega al coordinador.

El worker nunca escribe Cases ni SubTasks: todo sale por la cola `resultados`.
"""

from __future__ import annotations

import logging
import mimetypes
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from botocore.exceptions import ClientError

from shared.messages import ErrorCode, ErrorInfo, ResultMessage, SubTaskMessage, utcnow
from shared.routing import RESULTS_QUEUE, Pool
from shared.states import SubTaskStatus

from ..ops import OPERATIONS
from ..ops.base import OpError
from .heartbeat import Heartbeat
from .poller import receive_next

logger = logging.getLogger(__name__)

# Cada cuántos puntos de progreso se publica un `running`: suficiente para el
# panel sin inundar la cola de resultados con un mensaje por segundo.
_PROGRESS_STEP = 10

_IDLE_ON_ERROR_S = 5


@dataclass(frozen=True)
class WorkerContext:
    sqs: Any
    s3: Any
    dynamodb: Any
    queue_urls: dict[str, str]
    cases_table: str
    dataset_bucket: str
    results_bucket: str
    worker_id: str
    pool: Pool
    heartbeat: Heartbeat
    timeouts: dict[str, float]  # cola de origen -> tope de la operación

    def publish(self, result: ResultMessage) -> None:
        self.sqs.send_message(
            QueueUrl=self.queue_urls[RESULTS_QUEUE], MessageBody=result.to_body()
        )

    def result(
        self, task: SubTaskMessage, attempt: int, **fields: Any
    ) -> ResultMessage:
        return ResultMessage(
            subtask_id=task.subtask_id,
            case_id=task.case_id,
            attempt=attempt,
            worker_id=self.worker_id,
            pool=self.pool,
            **fields,
        )


def handle_message(ctx: WorkerContext, queue_name: str, raw: dict[str, Any]) -> None:
    queue_url = ctx.queue_urls[queue_name]
    receipt = raw["ReceiptHandle"]
    attempt = int(raw.get("Attributes", {}).get("ApproximateReceiveCount", "1"))

    def delete() -> None:
        ctx.sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt)

    try:
        task = SubTaskMessage.from_body(raw["Body"])
    except Exception:
        logger.exception(
            "SubTaskMessage ilegible, se descarta", extra={"queue": queue_name}
        )
        delete()
        return

    log = {"case_id": task.case_id, "subtask_id": task.subtask_id, "queue": queue_name}

    if _is_cancelled(ctx, task.case_id):
        ctx.publish(
            ctx.result(
                task,
                attempt,
                status=SubTaskStatus.CANCELLED,
                finished_at=utcnow(),
                error=ErrorInfo(
                    code=ErrorCode.CANCELLED, message="caso cancelado antes de procesar"
                ),
            )
        )
        delete()
        logger.info("sub-tarea cancelada sin procesar", extra=log)
        return

    if attempt > 1:
        ctx.publish(ctx.result(task, attempt, status=SubTaskStatus.RETRYING))
    started_at = utcnow()
    ctx.publish(
        ctx.result(task, attempt, status=SubTaskStatus.ASSIGNED, started_at=started_at)
    )

    try:
        with ctx.heartbeat.track(task.subtask_id):
            output_keys, output_meta = _run(ctx, task, queue_name, attempt)
        terminal = ctx.result(
            task,
            attempt,
            status=SubTaskStatus.COMPLETED,
            started_at=started_at,
            finished_at=utcnow(),
            output_keys=output_keys,
            output_meta=output_meta,
        )
    except OpError as exc:
        terminal = ctx.result(
            task,
            attempt,
            status=SubTaskStatus.FAILED,
            started_at=started_at,
            finished_at=utcnow(),
            error=ErrorInfo(code=exc.code, message=exc.message),
        )
    except Exception:
        logger.exception(
            "falla inesperada: no se borra el mensaje, SQS lo reintenta",
            extra={**log, "attempt": attempt},
        )
        return

    ctx.publish(terminal)
    delete()
    logger.info(
        "sub-tarea terminada",
        extra={**log, "status": terminal.status.value, "attempt": attempt},
    )


def _run(
    ctx: WorkerContext, task: SubTaskMessage, queue_name: str, attempt: int
) -> tuple[list[str], dict[str, Any]]:
    with tempfile.TemporaryDirectory(prefix="dmp-") as tmp:
        src = Path(tmp) / "in" / Path(task.input_key).name
        out_dir = Path(tmp) / "out"
        src.parent.mkdir()
        out_dir.mkdir()
        _download(ctx, task.input_key, src)

        last = -_PROGRESS_STEP

        def on_progress(pct: int) -> None:
            nonlocal last
            if pct >= last + _PROGRESS_STEP:
                last = pct
                ctx.publish(
                    ctx.result(
                        task, attempt, status=SubTaskStatus.RUNNING, progress=pct
                    )
                )

        on_progress(0)
        output = OPERATIONS[task.operation](
            task,
            src,
            out_dir,
            timeout_s=ctx.timeouts[queue_name],
            on_progress=on_progress,
        )

        keys = []
        for path in output.files:
            key = f"{task.output_prefix}{path.name}"
            content_type = (
                mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            )
            ctx.s3.upload_file(
                str(path),
                ctx.results_bucket,
                key,
                ExtraArgs={"ContentType": content_type},
            )
            keys.append(key)
        return keys, output.meta


def _download(ctx: WorkerContext, key: str, dest: Path) -> None:
    try:
        ctx.s3.download_file(ctx.dataset_bucket, key, str(dest))
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey"}:
            raise OpError(
                ErrorCode.CORRUPT_INPUT, f"{key} no existe en el dataset"
            ) from exc
        raise


def _is_cancelled(ctx: WorkerContext, case_id: str) -> bool:
    item = ctx.dynamodb.get_item(
        TableName=ctx.cases_table,
        Key={"case_id": {"S": case_id}},
        ConsistentRead=True,
        ProjectionExpression="cancel_requested",
    ).get("Item", {})
    return item.get("cancel_requested", {}).get("BOOL", False)


def consume(
    ctx: WorkerContext, poll_order: tuple[str, ...], stop_event: threading.Event
) -> None:
    """Loop de un hilo consumidor. Termina la sub-tarea en curso antes de salir
    cuando `stop_event` se activa (systemd stop)."""
    while not stop_event.is_set():
        try:
            received = receive_next(ctx.sqs, ctx.queue_urls, poll_order)
            if received is not None:
                handle_message(ctx, *received)
        except Exception:
            logger.exception("vuelta del consumidor falló, se reintenta")
            stop_event.wait(_IDLE_ON_ERROR_S)
