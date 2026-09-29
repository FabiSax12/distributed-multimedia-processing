"""Cierre de una sub-tarea: la mitad "por evento" del barrier.

Cada `ResultMessage` terminal (completed/failed/cancelled) cierra UNA
sub-tarea y descuenta UNO del `pending_count` de su caso. Las dos escrituras
(SubTasks y Cases) van en un único `TransactWriteItems` a propósito: si
solo actualizáramos SubTasks y el proceso muriera antes de tocar Cases, el
caso quedaría con `pending_count` colgado para siempre (el barrier nunca
llegaría a cero); si fuera al revés, `pending_count` bajaría sin que la
sub-tarea reflejara su estado final. Una transacción hace que las dos cosas
pasen juntas o ninguna.
"""

from __future__ import annotations

import logging
from typing import Any

from botocore.exceptions import ClientError

from shared.messages import ResultMessage
from shared.states import SubTaskStatus

from ..repo.cases import CasesRepo
from ..repo.subtasks import SubTasksRepo
from .finalize import finalize

logger = logging.getLogger(__name__)


def close_subtask(
    result: ResultMessage,
    *,
    cases_repo: CasesRepo,
    subtasks_repo: SubTasksRepo,
    dynamodb: Any,
    s3: Any,
    results_bucket: str,
) -> None:
    """Aplica el resultado terminal de una sub-tarea y dispara `finalize` si cierra el caso.

    `s3`/`results_bucket` se necesitan acá (aunque el "cierre" en sí es un
    `TransactWriteItems`) porque llegar a `pending_count == 0` dispara
    `finalize()` en el mismo hilo del consumer, y `finalize` sube el reporte a
    S3 antes de escribir el estado final del caso.
    """
    failed = result.status is SubTaskStatus.FAILED
    cancelled = result.status is SubTaskStatus.CANCELLED

    _warn_if_pool_mismatch(result, subtasks_repo)

    subtask_update = subtasks_repo.build_close_update(result)
    case_update = cases_repo.build_close_update(
        result.case_id, result.subtask_id, failed=failed, cancelled=cancelled
    )

    try:
        dynamodb.transact_write_items(TransactItems=[subtask_update, case_update])
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "TransactionCanceledException":
            raise  # error inesperado (throttling, etc.): que SQS reintente el mensaje

        reasons = exc.response.get("CancellationReasons", [])
        if not any(r.get("Code") == "ConditionalCheckFailed" for r in reasons):
            # p. ej. TransactionConflict: otra escritura concurrente sobre las
            # mismas filas. No es un duplicado conocido, relanzamos para que
            # el consumer NO borre el mensaje y SQS lo reintente.
            raise

        _log_conditional_check_failed(result, reasons, subtasks_repo)
        _maybe_finalize(result.case_id, cases_repo, subtasks_repo, s3, results_bucket)
        return

    _maybe_finalize(result.case_id, cases_repo, subtasks_repo, s3, results_bucket)


def _log_conditional_check_failed(
    result: ResultMessage,
    reasons: list[dict[str, Any]],
    subtasks_repo: SubTasksRepo,
) -> None:
    """Distingue un duplicado real (rutinario) de una referencia inválida (no).

    `TransactItems` en `close_subtask` es `[subtask_update, case_update]`, así
    que `reasons[0]`/`reasons[1]` corresponden en el mismo orden. Si el motivo
    vino del lado de Cases (`closed` ya contiene el `subtask_id`), es
    indudablemente un duplicado real: esa sub-tarea ya pasó por este mismo
    camino antes, así que ambas escrituras ya se aplicaron juntas en una
    transacción previa. Si en cambio el motivo vino solo del lado de SubTasks
    (su `state_order` no avanzó, o directamente no existe), hace falta un
    `GetItem` puntual para saber cuál de las dos cosas es.
    """
    subtask_reason = reasons[0] if len(reasons) > 0 else {}
    case_reason = reasons[1] if len(reasons) > 1 else {}

    if case_reason.get("Code") == "ConditionalCheckFailed":
        logger.debug(
            "resultado duplicado/tardío, se ignora el cierre pero se revisa el barrier",
            extra={"case_id": result.case_id, "subtask_id": result.subtask_id},
        )
        return

    if subtask_reason.get("Code") == "ConditionalCheckFailed":
        existing = subtasks_repo.get(result.case_id, result.subtask_id)
        if existing is None:
            logger.warning(
                "ResultMessage referencia una sub-tarea inexistente, se descarta "
                "el mensaje pero esto puede indicar un bug",
                extra={"case_id": result.case_id, "subtask_id": result.subtask_id},
            )
        else:
            logger.debug(
                "resultado duplicado/tardío, se ignora el cierre pero se revisa el barrier",
                extra={"case_id": result.case_id, "subtask_id": result.subtask_id},
            )
        return

    # No debería pasar (el `any()` en `close_subtask` ya garantizó que algún
    # ítem falló por `ConditionalCheckFailed`), pero por las dudas no lo
    # tratamos como error fatal: ya estamos en la rama "no relanzar".
    logger.debug(
        "resultado duplicado/tardío, se ignora el cierre pero se revisa el barrier",
        extra={"case_id": result.case_id, "subtask_id": result.subtask_id},
    )


def _warn_if_pool_mismatch(result: ResultMessage, subtasks_repo: SubTasksRepo) -> None:
    """Alerta (sin bloquear el cierre) si el worker reportó un pool distinto al planificado."""
    if result.pool is None:
        return
    existing = subtasks_repo.get(result.case_id, result.subtask_id)
    if existing is not None and existing.pool != result.pool:
        logger.warning(
            "el pool reportado no coincide con el planificado, posible worker mal configurado",
            extra={
                "case_id": result.case_id,
                "subtask_id": result.subtask_id,
                "reported_pool": result.pool.value,
                "planned_pool": existing.pool.value,
            },
        )


def _maybe_finalize(
    case_id: str,
    cases_repo: CasesRepo,
    subtasks_repo: SubTasksRepo,
    s3: Any,
    results_bucket: str,
) -> None:
    case = cases_repo.get(case_id, consistent=True)
    if case is not None and case.pending_count == 0:
        finalize(
            case_id,
            cases_repo=cases_repo,
            subtasks_repo=subtasks_repo,
            s3=s3,
            results_bucket=results_bucket,
        )
