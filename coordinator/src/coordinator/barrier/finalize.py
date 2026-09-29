"""Cierre de un CASO completo: la mitad "de una sola vez" del barrier.

Se dispara cuando `pending_count` llega a 0 (desde `barrier/close.py`) o
cuando el sweeper encuentra un caso que quedó en cero sin cerrarse (el
proceso murió entre el `TransactWriteItems` y esta función). Es IDEMPOTENTE:
puede correr en paralelo desde varios hilos/llamadas para el mismo caso sin
corromper nada, porque el único efecto que no se puede repetir sin daño
(`finalize_write`) está protegido por una condición atómica.
"""

from __future__ import annotations

import logging
from typing import Any

from shared.messages import utcnow
from shared.models import report_key
from shared.states import TERMINAL_CASE, CaseStatus, final_case_status

from ..repo.cases import CasesRepo
from ..repo.subtasks import SubTasksRepo
from ..reports.builder import build_report

logger = logging.getLogger(__name__)


def finalize(
    case_id: str,
    *,
    cases_repo: CasesRepo,
    subtasks_repo: SubTasksRepo,
    s3: Any,
    results_bucket: str,
) -> CaseStatus | None:
    case = cases_repo.get(case_id, consistent=True)
    if case is None or case.status in TERMINAL_CASE:
        return None

    subtasks = subtasks_repo.query_by_case(case_id)
    status = final_case_status(case.total, case.failed_count, case.cancel_requested)
    finished_at = utcnow()

    report = build_report(case, subtasks, status=status, finished_at=finished_at)
    key = report_key(case_id)

    # El reporte se sube ANTES de escribir el estado final del caso, a
    # propósito: si el proceso muere entre las dos escrituras, el caso sigue
    # sin ser terminal y el sweeper (u otro `close_subtask` duplicado) va a
    # volver a llamar `finalize`, que va a sobrescribir el mismo reporte en la
    # misma clave determinística (no hay daño en pisarlo con el mismo
    # contenido) y esta vez sí va a lograr escribir el estado final.
    s3.put_object(
        Bucket=results_bucket,
        Key=key,
        Body=report.model_dump_json().encode("utf-8"),
        ContentType="application/json",
    )

    wrote = cases_repo.finalize_write(
        case_id, status, finished_at=finished_at, report_key=key
    )
    if not wrote:
        # Otro hilo/llamada ganó la carrera y ya dejó el caso en terminal.
        logger.debug(
            "finalize: otro proceso ya cerró el caso", extra={"case_id": case_id}
        )
        return None

    logger.info(
        "caso finalizado",
        extra={"case_id": case_id, "status": status.value, "report_key": key},
    )
    return status
