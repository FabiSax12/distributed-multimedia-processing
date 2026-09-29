"""Una pasada de reparación sobre todos los casos abiertos.

Pensada para correr una vez al arrancar el proceso y después en un loop cada
`SWEEP_INTERVAL_S` (el loop y el hilo los arma `main.py` en la tanda 2 — acá
solo va la función que hace UNA pasada). Cubre dos huecos que el barrier
"por evento" (`barrier/close.py`) no puede cerrar solo:

1. Una sub-tarea que quedó con `enqueued=false` porque su `SendMessageBatch`
   falló parcialmente (ver `routing/enqueue.py`) nunca le va a llegar un
   `ResultMessage` a nadie, así que sin el sweeper se quedaría pendiente para
   siempre.
2. Un caso que llegó a `pending_count == 0` pero el proceso murió entre el
   `TransactWriteItems` de `close_subtask` y el `finalize()` que dispara -
   `finalize` es idempotente, así que volver a llamarlo acá es seguro.

A la escala de cientos de casos, un Scan por pasada (una vez por minuto) es
aceptable; en producción esto sería un GSI por `status`/`(status, enqueued)`.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from shared.messages import utcnow
from shared.models import SubTaskItem

from ..repo.cases import CasesRepo
from ..repo.subtasks import SubTasksRepo
from ..routing.enqueue import enqueue_case
from .finalize import finalize

logger = logging.getLogger(__name__)

_ORPHAN_ENQUEUE_AFTER_S = 30
_STALL_ALERT_AFTER = timedelta(minutes=30)


def run_sweep(
    *,
    cases_repo: CasesRepo,
    subtasks_repo: SubTasksRepo,
    sqs: Any,
    queue_urls: dict[str, str],
    s3: Any,
    results_bucket: str,
    now: datetime | None = None,
) -> None:
    now = now or utcnow()
    orphan_cutoff = now - timedelta(seconds=_ORPHAN_ENQUEUE_AFTER_S)

    # Un solo Scan de huérfanos para todo el barrido (no uno por caso): son
    # candidatos de TODA la tabla SubTasks, así que se agrupan por caso una
    # sola vez y se reencolan junto con las demás sub-tareas huérfanas de ese
    # caso, en vez de escanear la tabla completa una vez por cada caso abierto.
    orphans_by_case: dict[str, list[SubTaskItem]] = {}
    for subtask in subtasks_repo.scan_orphan_enqueue_candidates(orphan_cutoff):
        orphans_by_case.setdefault(subtask.case_id, []).append(subtask)

    for case in cases_repo.scan_non_terminal():
        orphans = orphans_by_case.get(case.case_id)
        if orphans:
            enqueue_case(
                orphans, sqs=sqs, queue_urls=queue_urls, subtasks_repo=subtasks_repo
            )
            logger.info(
                "sweeper: reencoladas sub-tareas huérfanas",
                extra={"case_id": case.case_id, "count": len(orphans)},
            )

        # Lectura consistente (no la del Scan, que es eventual) antes de
        # decidir si cerrar el caso: `finalize` no vuelve a chequear
        # `pending_count`, así que la guardia tiene que estar acá.
        fresh = cases_repo.get(case.case_id, consistent=True)
        if fresh is not None and fresh.pending_count == 0:
            finalize(
                fresh.case_id,
                cases_repo=cases_repo,
                subtasks_repo=subtasks_repo,
                s3=s3,
                results_bucket=results_bucket,
            )
            continue

        _warn_if_stalled(
            case_id=case.case_id,
            created_at=case.created_at,
            subtasks_repo=subtasks_repo,
            now=now,
        )


def _warn_if_stalled(
    *, case_id: str, created_at: datetime, subtasks_repo: SubTasksRepo, now: datetime
) -> None:
    subtasks = subtasks_repo.query_by_case(case_id)
    timestamps = [
        ts
        for subtask in subtasks
        for ts in (subtask.finished_at, subtask.started_at, subtask.created_at)
        if ts is not None
    ]
    last_activity = max(timestamps, default=created_at)
    if now - last_activity > _STALL_ALERT_AFTER:
        # TODO(tanda 2): exponer esto como `Alert` en `GET /api/state`
        # (monitor/balancer.py construye `shared.api.Alert`); por ahora
        # alcanza con el log para que aparezca en CloudWatch Insights.
        stall_minutes = int(_STALL_ALERT_AFTER.total_seconds() // 60)
        logger.warning(
            f"caso estancado: ninguna sub-tarea cambió de estado en los últimos {stall_minutes} min",
            extra={"case_id": case_id, "last_activity": last_activity.isoformat()},
        )
