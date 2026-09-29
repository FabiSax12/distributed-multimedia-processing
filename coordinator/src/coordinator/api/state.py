"""`GET /api/state` y `GET /api/metrics`: exponen lo que `monitor/` ya calculó en
background. Ninguno de los dos toca AWS dentro del request."""

from __future__ import annotations

from fastapi import APIRouter

from shared.api import MetricsResponse, StateResponse

from ..monitor.samples import get_recent_samples
from ..monitor.snapshot import get_latest_snapshot

router = APIRouter(tags=["state"])


@router.get("/state", response_model=StateResponse)
def get_state() -> StateResponse:
    snapshot = get_latest_snapshot()
    if snapshot is not None:
        return snapshot
    # El loop de snapshot todavía no completó su primera vuelta (proceso
    # recién arrancado): un StateResponse vacío es más útil para el panel que
    # un 500.
    return StateResponse(cases=[], workers=[], queues=[], alerts=[])


@router.get("/metrics", response_model=MetricsResponse)
def get_metrics(minutes: int = 30) -> MetricsResponse:
    return MetricsResponse(samples=get_recent_samples(minutes))
