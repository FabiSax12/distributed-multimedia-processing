"""Handler de la DLQ: sub-tareas que agotaron los reintentos de SQS.

Si un mensaje de una cola de trabajo agota `maxReceiveCount` (worker caído a
mitad de proceso, excepción no controlada, etc.), SQS lo mueve acá. El
coordinador lo consume y lo registra como `failed` vía
`shared.messages.result_from_dlq`, exactamente como si el worker hubiera
reportado el fallo él mismo.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from shared.messages import SubTaskMessage, result_from_dlq

from ..barrier.close import close_subtask
from ..repo.cases import CasesRepo
from ..repo.subtasks import SubTasksRepo

logger = logging.getLogger(__name__)


def build_dlq_handler(
    *,
    cases_repo: CasesRepo,
    subtasks_repo: SubTasksRepo,
    dynamodb: Any,
    s3: Any,
    results_bucket: str,
    dlq_assumed_attempts: int,
) -> Callable[[dict[str, Any]], bool]:
    def handle(message: dict[str, Any]) -> bool:
        try:
            subtask_message = SubTaskMessage.from_body(message["Body"])
        except Exception:
            logger.exception(
                "SubTaskMessage ilegible en la DLQ, se descarta",
                extra={"message_id": message.get("MessageId")},
            )
            return True

        result = result_from_dlq(subtask_message, dlq_assumed_attempts)
        try:
            close_subtask(
                result,
                cases_repo=cases_repo,
                subtasks_repo=subtasks_repo,
                dynamodb=dynamodb,
                s3=s3,
                results_bucket=results_bucket,
            )
        except Exception:
            logger.exception(
                "close_subtask falló para un mensaje de la DLQ, se reintenta",
                extra={"case_id": result.case_id, "subtask_id": result.subtask_id},
            )
            return False
        return True

    return handle
