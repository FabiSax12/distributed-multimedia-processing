#!/usr/bin/env python3
"""Simula un worker real contra AWS, usando SOLO `shared/` + boto3.

Este script es, a propósito, el "protocolo" que el worker real (otro
componente, todavía no escrito) va a tener que implementar: consumir sus
colas en el orden de `shared.routing.POLL_ORDER`, publicar `ResultMessage`s
para cada cambio de estado, escribir su propio heartbeat en `Workers`, y
nunca tocar `Cases`/`SubTasks` directamente. No importa nada de
`coordinator.src` salvo `coordinator.config` (para no reinventar cómo se leen
las variables de entorno / `.env.local`).

Uso:
    uv run --package coordinator python coordinator/scripts/fake_worker.py \\
        --pool video --fail-rate 0.1 --crash-rate 0.05
"""

from __future__ import annotations

import argparse
import logging
import os
import random
import socket
import time

import boto3
from coordinator.config import get_settings

from shared.messages import ErrorCode, ErrorInfo, ResultMessage, SubTaskMessage, utcnow
from shared.models import HEARTBEAT_INTERVAL_S, WorkerItem, to_item
from shared.routing import POLL_ORDER, RESULTS_QUEUE, Pool
from shared.states import SubTaskStatus

logger = logging.getLogger("fake_worker")

_RECEIVE_WAIT_S = 3
_RUNNING_PROGRESS_STEPS = (25, 60)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pool", required=True, choices=[p.value for p in Pool])
    parser.add_argument(
        "--fail-rate",
        type=float,
        default=0.0,
        help="Probabilidad (0-1) de reportar 'failed' en vez de 'completed'.",
    )
    parser.add_argument(
        "--crash-rate",
        type=float,
        default=0.0,
        help=(
            "Probabilidad (0-1) de NO borrar el mensaje de trabajo tras procesarlo, "
            "simulando una caída del worker (ejercita reintentos/DLQ)."
        ),
    )
    args = parser.parse_args()
    for flag, value in (
        ("--fail-rate", args.fail_rate),
        ("--crash-rate", args.crash_rate),
    ):
        if not 0.0 <= value <= 1.0:
            parser.error(f"{flag} debe estar entre 0 y 1")
    return args


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    args = parse_args()
    settings = get_settings()

    sqs = boto3.client("sqs", region_name=settings.AWS_REGION)
    s3 = boto3.client("s3", region_name=settings.AWS_REGION)
    workers_table = boto3.resource("dynamodb", region_name=settings.AWS_REGION).Table(
        settings.TABLE_WORKERS
    )
    cases_table = boto3.resource("dynamodb", region_name=settings.AWS_REGION).Table(
        settings.TABLE_CASES
    )

    pool = Pool(args.pool)
    worker_id = f"{pool.value}-{socket.gethostname()}-{os.getpid()}"
    started_at = utcnow()
    poll_order = POLL_ORDER[pool]
    results_queue_url = settings.QUEUE_URLS[RESULTS_QUEUE]

    def heartbeat() -> None:
        item = WorkerItem(
            worker_id=worker_id,
            pool=pool,
            instance_type="fake-worker",
            hostname=socket.gethostname(),
            concurrency=1,
            cpu_percent=round(random.uniform(10, 60), 1),
            mem_percent=round(random.uniform(10, 60), 1),
            active_subtasks=[],
            started_at=started_at,
            last_seen=utcnow(),
        )
        workers_table.put_item(Item=to_item(item))

    heartbeat()
    last_heartbeat = time.monotonic()
    logger.info("fake_worker arrancó worker_id=%s pool=%s", worker_id, pool.value)

    while True:
        if time.monotonic() - last_heartbeat >= HEARTBEAT_INTERVAL_S:
            heartbeat()
            last_heartbeat = time.monotonic()

        received = _receive_next(sqs, settings.QUEUE_URLS, poll_order)
        if received is None:
            continue
        queue_name, raw_message = received

        _process_message(
            raw_message,
            queue_url=settings.QUEUE_URLS[queue_name],
            sqs=sqs,
            s3=s3,
            cases_table=cases_table,
            results_queue_url=results_queue_url,
            results_bucket=settings.RESULTS_BUCKET,
            worker_id=worker_id,
            pool=pool,
            fail_rate=args.fail_rate,
            crash_rate=args.crash_rate,
        )


def _receive_next(sqs, queue_urls: dict[str, str], poll_order: tuple[str, ...]):
    for queue_name in poll_order:
        resp = sqs.receive_message(
            QueueUrl=queue_urls[queue_name],
            WaitTimeSeconds=_RECEIVE_WAIT_S,
            MaxNumberOfMessages=1,
            AttributeNames=["ApproximateReceiveCount"],
        )
        messages = resp.get("Messages", [])
        if messages:
            return queue_name, messages[0]
    return None


def _is_cancelled(cases_table, case_id: str) -> bool:
    resp = cases_table.get_item(Key={"case_id": case_id}, ConsistentRead=True)
    item = resp.get("Item")
    return bool(item and item.get("cancel_requested"))


def _publish(sqs, queue_url: str, result: ResultMessage) -> None:
    sqs.send_message(QueueUrl=queue_url, MessageBody=result.to_body())


def _process_message(
    raw_message,
    *,
    queue_url: str,
    sqs,
    s3,
    cases_table,
    results_queue_url: str,
    results_bucket: str,
    worker_id: str,
    pool: Pool,
    fail_rate: float,
    crash_rate: float,
) -> None:
    receipt_handle = raw_message["ReceiptHandle"]
    attempt = int(raw_message.get("Attributes", {}).get("ApproximateReceiveCount", "1"))

    try:
        task = SubTaskMessage.from_body(raw_message["Body"])
    except Exception:
        logger.exception("SubTaskMessage ilegible, se descarta")
        sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt_handle)
        return

    if _is_cancelled(cases_table, task.case_id):
        _publish(
            sqs,
            results_queue_url,
            ResultMessage(
                subtask_id=task.subtask_id,
                case_id=task.case_id,
                status=SubTaskStatus.CANCELLED,
                attempt=attempt,
                worker_id=worker_id,
                pool=pool,
                finished_at=utcnow(),
                error=ErrorInfo(
                    code=ErrorCode.CANCELLED, message="caso cancelado antes de procesar"
                ),
            ),
        )
        sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt_handle)
        return

    _publish(
        sqs,
        results_queue_url,
        ResultMessage(
            subtask_id=task.subtask_id,
            case_id=task.case_id,
            status=SubTaskStatus.ASSIGNED,
            attempt=attempt,
            worker_id=worker_id,
            pool=pool,
            started_at=utcnow(),
        ),
    )

    for progress in _RUNNING_PROGRESS_STEPS:
        time.sleep(random.uniform(0.2, 0.8))
        _publish(
            sqs,
            results_queue_url,
            ResultMessage(
                subtask_id=task.subtask_id,
                case_id=task.case_id,
                status=SubTaskStatus.RUNNING,
                attempt=attempt,
                worker_id=worker_id,
                pool=pool,
                progress=progress,
            ),
        )

    time.sleep(random.uniform(0.2, 0.8))

    if random.random() < fail_rate:
        result = ResultMessage(
            subtask_id=task.subtask_id,
            case_id=task.case_id,
            status=SubTaskStatus.FAILED,
            attempt=attempt,
            worker_id=worker_id,
            pool=pool,
            finished_at=utcnow(),
            error=ErrorInfo(
                code=ErrorCode.PROCESSING_ERROR, message="fake_worker: fallo simulado"
            ),
        )
    else:
        output_key = f"{task.output_prefix}output.bin"
        s3.put_object(
            Bucket=results_bucket,
            Key=output_key,
            Body=b"fake-output",
            ContentType="application/octet-stream",
        )
        result = ResultMessage(
            subtask_id=task.subtask_id,
            case_id=task.case_id,
            status=SubTaskStatus.COMPLETED,
            attempt=attempt,
            worker_id=worker_id,
            pool=pool,
            finished_at=utcnow(),
            output_keys=[output_key],
        )

    _publish(sqs, results_queue_url, result)

    if random.random() < crash_rate:
        logger.warning(
            "crash simulado tras procesar subtask_id=%s: el mensaje NO se borra "
            "(va a reaparecer y eventualmente caer en la DLQ)",
            task.subtask_id,
        )
        return

    sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt_handle)


if __name__ == "__main__":
    main()
