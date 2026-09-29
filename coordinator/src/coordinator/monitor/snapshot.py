"""Arma un `StateResponse` completo cada `SNAPSHOT_INTERVAL_S` y lo cachea en memoria.

`api/state.py` (`GET /api/state`) solo lee `get_latest_snapshot()`: nunca toca
AWS dentro del request, para que el panel pueda hacer polling agresivo (cada
2s) sin generar carga proporcional de Scans/GetQueueAttributes. Este módulo
expone una función de "una vuelta" (`run_snapshot_once`) y quien la use arma
el loop (mismo patrón que `barrier/sweeper.run_sweep`, ver su docstring).
"""

from __future__ import annotations

import logging
import threading
from collections import Counter
from typing import Any

from shared.api import CaseSummary, QueueDepth, StateResponse, WorkerView
from shared.messages import utcnow
from shared.routing import ALL_WORK_QUEUES, DLQ, RESULTS_QUEUE
from shared.states import TERMINAL_CASE

from ..repo.cases import CasesRepo
from ..repo.subtasks import SubTasksRepo
from ..repo.workers import WorkersRepo
from . import balancer

logger = logging.getLogger(__name__)

_ALL_QUEUES: tuple[str, ...] = (*ALL_WORK_QUEUES, RESULTS_QUEUE, DLQ)

# Campos mínimos que `SubTasksRepo.query_by_case` necesita para poder validar
# el `SubTaskItem` resultante (ver su docstring), más `status` que es lo único
# que en verdad usamos acá para armar `by_status`.
_SUBTASK_STATUS_PROJECTION = [
    "case_id",
    "subtask_id",
    "input_key",
    "priority",
    "status",
]

_snapshot: StateResponse | None = None
_lock = threading.Lock()


def get_latest_snapshot() -> StateResponse | None:
    """Última vuelta exitosa, o `None` si el proceso todavía no completó ninguna."""
    with _lock:
        return _snapshot


def run_snapshot_once(
    *,
    cases_repo: CasesRepo,
    subtasks_repo: SubTasksRepo,
    workers_repo: WorkersRepo,
    sqs: Any,
    queue_urls: dict[str, str],
    recent_cases_limit: int,
    pool_saturation_queue_threshold: int,
    pool_saturation_cpu_percent: float,
) -> None:
    """Una vuelta: junta casos/workers/colas/alertas y reemplaza el snapshot global.

    Si algo falla (throttling, cola inexistente, etc.) se loguea y se conserva
    el snapshot anterior — una vuelta fallida no debe tirar abajo el hilo que
    la llama en loop, ni dejar `/api/state` sin datos.
    """
    try:
        snapshot = _build_snapshot(
            cases_repo=cases_repo,
            subtasks_repo=subtasks_repo,
            workers_repo=workers_repo,
            sqs=sqs,
            queue_urls=queue_urls,
            recent_cases_limit=recent_cases_limit,
            pool_saturation_queue_threshold=pool_saturation_queue_threshold,
            pool_saturation_cpu_percent=pool_saturation_cpu_percent,
        )
    except Exception:
        logger.exception("run_snapshot_once falló, se conserva el snapshot anterior")
        return

    global _snapshot
    with _lock:
        _snapshot = snapshot


def _build_snapshot(
    *,
    cases_repo: CasesRepo,
    subtasks_repo: SubTasksRepo,
    workers_repo: WorkersRepo,
    sqs: Any,
    queue_urls: dict[str, str],
    recent_cases_limit: int,
    pool_saturation_queue_threshold: int,
    pool_saturation_cpu_percent: float,
) -> StateResponse:
    now = utcnow()

    cases = list(cases_repo.scan_non_terminal())
    cases.extend(_recent_terminal_cases(cases_repo, recent_cases_limit))
    case_summaries = [
        CaseSummary(case=case, by_status=_by_status(subtasks_repo, case.case_id))
        for case in cases
    ]

    workers = [
        WorkerView(**worker.model_dump(), alive=worker.is_alive(now=now))
        for worker in workers_repo.scan_all()
    ]

    queues = [_queue_depth(sqs, queue_urls, name) for name in _ALL_QUEUES]

    alerts = balancer.build_alerts(
        queues,
        workers,
        queue_threshold=pool_saturation_queue_threshold,
        cpu_threshold=pool_saturation_cpu_percent,
    )

    return StateResponse(
        generated_at=now,
        cases=case_summaries,
        workers=workers,
        queues=queues,
        alerts=alerts,
    )


def _recent_terminal_cases(cases_repo: CasesRepo, limit: int) -> list[Any]:
    """Los `limit` casos terminados más recientes, entre todos los estados terminales.

    `CasesRepo.list_recent` solo filtra por UN status a la vez, así que
    juntamos los candidatos de cada estado terminal y volvemos a ordenar/
    recortar acá. A la escala de este proyecto ya es un Scan completo por
    status (ver docstring de `list_recent`), así que esto es simplemente
    varios de esos Scans en vez de uno.
    """
    candidates = [
        case
        for status in TERMINAL_CASE
        for case in cases_repo.list_recent(status.value, limit)
    ]
    candidates.sort(key=lambda c: c.case_id, reverse=True)
    return candidates[:limit]


def _by_status(subtasks_repo: SubTasksRepo, case_id: str) -> dict[str, int]:
    subtasks = subtasks_repo.query_by_case(
        case_id, projection=_SUBTASK_STATUS_PROJECTION
    )
    return dict(Counter(subtask.status.value for subtask in subtasks))


def _queue_depth(sqs: Any, queue_urls: dict[str, str], name: str) -> QueueDepth:
    resp = sqs.get_queue_attributes(
        QueueUrl=queue_urls[name],
        AttributeNames=[
            "ApproximateNumberOfMessages",
            "ApproximateNumberOfMessagesNotVisible",
        ],
    )
    attrs = resp.get("Attributes", {})
    return QueueDepth(
        queue=name,
        visible=int(attrs.get("ApproximateNumberOfMessages", 0)),
        in_flight=int(attrs.get("ApproximateNumberOfMessagesNotVisible", 0)),
    )


def run_snapshot_loop(
    stop_event: threading.Event,
    interval: float,
    **kwargs: Any,
) -> None:
    """Loop que corre `run_snapshot_once` cada `interval` segundos hasta `stop_event`.

    `main.py` lo lanza en un hilo propio. `stop_event.wait(interval)` en vez de
    `time.sleep` para que el shutdown no tenga que esperar el intervalo completo.
    """
    while not stop_event.is_set():
        run_snapshot_once(**kwargs)
        stop_event.wait(interval)
