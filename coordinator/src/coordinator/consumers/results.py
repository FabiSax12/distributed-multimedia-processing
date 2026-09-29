"""Handler de la cola de resultados: uno solo, compartido por N hilos consumidores.

`RESULTS_CONSUMER_THREADS` instancias de `SqsConsumerThread` corren este mismo
handler contra la misma cola — está bien, SQS reparte los mensajes entre
receivers concurrentes, no hace falta partir la cola.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from shared.messages import ResultMessage
from shared.states import TERMINAL_SUBTASK, SubTaskStatus

from ..barrier.close import close_subtask
from ..repo.cases import CasesRepo
from ..repo.subtasks import SubTasksRepo

logger = logging.getLogger(__name__)


def build_results_handler(
    *,
    cases_repo: CasesRepo,
    subtasks_repo: SubTasksRepo,
    dynamodb: Any,
    s3: Any,
    results_bucket: str,
) -> Callable[[dict[str, Any]], bool]:
    def handle(message: dict[str, Any]) -> bool:
        try:
            result = ResultMessage.from_body(message["Body"])
        except Exception:
            logger.exception(
                "ResultMessage ilegible, se descarta (mensaje envenenado)",
                extra={"message_id": message.get("MessageId")},
            )
            return True

        if result.status not in TERMINAL_SUBTASK:
            return _apply_progress(
                result, cases_repo=cases_repo, subtasks_repo=subtasks_repo
            )

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
            # close_subtask solo relanza para el caso "hay que reintentar"
            # (ver su docstring); cualquier otra cosa la tratamos igual, como
            # falla transitoria: no borrar, que SQS reintente.
            logger.exception(
                "close_subtask falló, se reintenta",
                extra={"case_id": result.case_id, "subtask_id": result.subtask_id},
            )
            return False
        return True

    return handle


def _apply_progress(
    result: ResultMessage, *, cases_repo: CasesRepo, subtasks_repo: SubTasksRepo
) -> bool:
    """Estados no terminales: assigned/running/retrying."""
    updated = subtasks_repo.update_progress(
        result.case_id,
        result.subtask_id,
        status=result.status.value,
        state_order=result.order,
        attempt=result.attempt,
        worker_id=result.worker_id,
        progress=result.progress,
        started_at_if_missing=result.started_at,
    )
    if not updated:
        # `state_order` no avanzó: mensaje viejo o duplicado. Ya está resuelto
        # por una escritura más nueva, así que se borra igual.
        logger.debug(
            "progreso desactualizado/duplicado, se ignora",
            extra={"case_id": result.case_id, "subtask_id": result.subtask_id},
        )
        return True

    # Estas dos escrituras del caso son condicionales (ver repo/cases.py) para
    # nunca pisar un estado terminal con un progreso atrasado.
    if result.status is SubTaskStatus.RETRYING:
        cases_repo.mark_retrying_if_not_terminal(result.case_id)
    else:  # assigned / running
        cases_repo.mark_processing_if_needed(result.case_id)

    return True
