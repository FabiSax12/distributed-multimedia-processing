"""Publica las sub-tareas planificadas en sus colas de trabajo (SQS)."""

from __future__ import annotations

import logging
from typing import Any

from shared.models import SubTaskItem

from ..repo.subtasks import SubTasksRepo

logger = logging.getLogger(__name__)

_SEND_BATCH_SIZE = 10


def enqueue_case(
    subtasks: list[SubTaskItem],
    *,
    sqs: Any,
    queue_urls: dict[str, str],
    subtasks_repo: SubTasksRepo,
) -> None:
    """Agrupa por cola, manda `SendMessageBatch` de a 10 y marca `enqueued=true`.

    Asume que el `PutItem` del caso y el `BatchWriteItem` de sus sub-tareas YA
    se hicieron (patrón outbox: primero se persiste la intención en DynamoDB,
    después se publica en SQS). Una sub-tarea que caiga en `Failed` dentro de
    un lote NO se reintenta acá: queda con `enqueued=false` y es
    `barrier/sweeper.py` quien la vuelve a encolar más tarde. Así ningún caso
    queda colgado para siempre por un error transitorio de SendMessageBatch.
    Las sub-tareas prefallidas (sin `queue`, formato no soportado) se ignoran:
    nunca se encolan.
    """
    by_queue: dict[str, list[SubTaskItem]] = {}
    for subtask in subtasks:
        if subtask.queue is None:
            continue
        by_queue.setdefault(subtask.queue, []).append(subtask)

    for queue_name, queued_subtasks in by_queue.items():
        queue_url = queue_urls[queue_name]
        for start in range(0, len(queued_subtasks), _SEND_BATCH_SIZE):
            chunk = queued_subtasks[start : start + _SEND_BATCH_SIZE]
            _send_and_mark(sqs, subtasks_repo, queue_url, chunk)


def _send_and_mark(
    sqs: Any,
    subtasks_repo: SubTasksRepo,
    queue_url: str,
    chunk: list[SubTaskItem],
) -> None:
    by_id = {subtask.subtask_id: subtask for subtask in chunk}
    entries = [
        {
            "Id": subtask.subtask_id,
            "MessageBody": subtask.to_message().to_body(),
            "MessageAttributes": {
                "case_id": {"DataType": "String", "StringValue": subtask.case_id},
                "subtask_id": {"DataType": "String", "StringValue": subtask.subtask_id},
            },
        }
        for subtask in chunk
    ]

    resp = sqs.send_message_batch(QueueUrl=queue_url, Entries=entries)

    for successful in resp.get("Successful", []):
        subtask = by_id[successful["Id"]]
        subtasks_repo.mark_enqueued(subtask.case_id, subtask.subtask_id)

    for failed in resp.get("Failed", []):
        subtask = by_id[failed["Id"]]
        logger.warning(
            "no se pudo encolar sub-tarea, queda para el sweeper",
            extra={
                "case_id": subtask.case_id,
                "subtask_id": subtask.subtask_id,
                "sqs_error_code": failed.get("Code"),
            },
        )
