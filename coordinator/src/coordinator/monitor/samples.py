"""Ring buffer de `LoadSample` en memoria + flush periódico a S3.

`GET /api/metrics` lee el ring buffer (`get_recent_samples`), nunca S3: el
histórico completo (para análisis posterior/el informe del curso) vive en
`shared.models.load_samples_chunk_key` en el bucket de resultados, pero el
panel solo necesita las últimas ~2h.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from datetime import timedelta
from typing import Any

from botocore.exceptions import ClientError

from shared.api import StateResponse
from shared.messages import utcnow
from shared.models import LoadSample, WorkerLoad, load_samples_chunk_key
from shared.states import TERMINAL_CASE

from ..config import get_settings
from .snapshot import get_latest_snapshot

logger = logging.getLogger(__name__)

_ring: deque[LoadSample] | None = None
_pending_flush: list[LoadSample] = []
_lock = threading.Lock()


def _ring_maxlen() -> int:
    """~2 horas de histórico en memoria (el histórico completo vive en S3)."""
    interval = max(get_settings().SAMPLE_INTERVAL_S, 1)
    return max(1, (2 * 60 * 60) // interval)


def _ensure_ring() -> deque[LoadSample]:
    global _ring
    if _ring is None:
        _ring = deque(maxlen=_ring_maxlen())
    return _ring


def take_sample_once() -> None:
    """Una muestra a partir del último snapshot. No-op si todavía no hay ninguno."""
    snapshot = get_latest_snapshot()
    if snapshot is None:
        return
    sample = _build_sample(snapshot)
    with _lock:
        _ensure_ring().append(sample)
        _pending_flush.append(sample)


def _build_sample(snapshot: StateResponse) -> LoadSample:
    queue_visible = {q.queue: q.visible for q in snapshot.queues}
    queue_in_flight = {q.queue: q.in_flight for q in snapshot.queues}
    workers = {
        worker.worker_id: WorkerLoad(
            pool=worker.pool,
            alive=worker.alive,
            cpu_percent=worker.cpu_percent,
            mem_percent=worker.mem_percent,
            active=len(worker.active_subtasks),
        )
        for worker in snapshot.workers
    }
    cases_open = sum(
        1 for summary in snapshot.cases if summary.case.status not in TERMINAL_CASE
    )
    return LoadSample(
        ts=snapshot.generated_at,
        queue_visible=queue_visible,
        queue_in_flight=queue_in_flight,
        workers=workers,
        cases_open=cases_open,
    )


def get_recent_samples(minutes: int) -> list[LoadSample]:
    """Muestras del ring buffer de los últimos `minutes` minutos, en orden."""
    cutoff = utcnow() - timedelta(minutes=minutes)
    with _lock:
        ring = _ring if _ring is not None else ()
        return [sample for sample in ring if sample.ts >= cutoff]


def flush_samples_once(s3: Any, results_bucket: str) -> None:
    """Sube a S3 las muestras acumuladas desde el último flush exitoso.

    Si algo falla, las muestras pendientes vuelven a la cola en vez de
    perderse (a costa de poder duplicar alguna línea en el chunk si una parte
    de un flush anterior sí llegó a escribirse — aceptable para métricas).
    """
    with _lock:
        pending = list(_pending_flush)
        _pending_flush.clear()
    if not pending:
        return
    try:
        _flush_pending(s3, results_bucket, pending)
    except Exception:
        logger.exception(
            "flush de métricas a S3 falló, se reintenta en el próximo ciclo"
        )
        with _lock:
            _pending_flush[:0] = pending


def _flush_pending(s3: Any, results_bucket: str, pending: list[LoadSample]) -> None:
    by_chunk: dict[str, list[LoadSample]] = {}
    for sample in pending:
        by_chunk.setdefault(load_samples_chunk_key(sample.ts), []).append(sample)

    for key, samples in by_chunk.items():
        existing_lines = _read_existing_lines(s3, results_bucket, key)
        new_lines = [sample.model_dump_json() for sample in samples]
        body = "\n".join(existing_lines + new_lines) + "\n"
        s3.put_object(
            Bucket=results_bucket,
            Key=key,
            Body=body.encode("utf-8"),
            ContentType="application/x-ndjson",
        )


def _read_existing_lines(s3: Any, results_bucket: str, key: str) -> list[str]:
    """El chunk del minuto puede ya traer líneas de un flush anterior en el mismo minuto."""
    try:
        resp = s3.get_object(Bucket=results_bucket, Key=key)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")
        if code in ("NoSuchKey", "404"):
            return []
        raise
    body = resp["Body"].read().decode("utf-8")
    return [line for line in body.splitlines() if line]


def run_samples_loop(
    stop_event: threading.Event,
    sample_interval: float,
    flush_interval: float,
    *,
    s3: Any,
    results_bucket: str,
) -> None:
    """Un solo hilo: muestrea cada `sample_interval` y flushea cada `flush_interval`.

    Igual que `monitor/snapshot.py::run_snapshot_once`, una falla en una sola
    vuelta se loguea y se descarta en vez de matar el hilo para siempre.
    """
    last_flush = time.monotonic()
    while not stop_event.is_set():
        try:
            take_sample_once()
        except Exception:
            logger.exception("take_sample_once falló, se reintenta en el próximo ciclo")
        if time.monotonic() - last_flush >= flush_interval:
            flush_samples_once(s3, results_bucket)
            last_flush = time.monotonic()
        stop_event.wait(sample_interval)
