"""`monitor/balancer.py`: reglas puras, sin AWS."""

from __future__ import annotations

from datetime import timedelta

from coordinator.monitor import balancer

from shared.api import QueueDepth, WorkerView
from shared.messages import utcnow
from shared.models import WORKER_DEAD_AFTER_S
from shared.routing import Pool, Priority, queue_key


def _queue(name: str, visible: int = 0, in_flight: int = 0) -> QueueDepth:
    return QueueDepth(queue=name, visible=visible, in_flight=in_flight)


def _worker(
    pool: Pool, *, alive: bool = True, cpu: float = 10.0, worker_id: str = "w1"
) -> WorkerView:
    now = utcnow()
    last_seen = now if alive else now - timedelta(seconds=WORKER_DEAD_AFTER_S * 2)
    return WorkerView(
        worker_id=worker_id,
        pool=pool,
        instance_type="t3.micro",
        hostname="h",
        concurrency=1,
        cpu_percent=cpu,
        mem_percent=10.0,
        active_subtasks=[],
        started_at=now,
        last_seen=last_seen,
        alive=alive,
    )


def _all_queues(**overrides: int) -> list[QueueDepth]:
    """Las 8 colas lógicas, en 0 salvo las que vengan en `overrides` (visible)."""
    names = [queue_key(p, pr) for p in Pool for pr in Priority] + ["resultados", "dlq"]
    return [_queue(name, visible=overrides.get(name, 0)) for name in names]


def test_check_dlq_alerts_when_messages_present() -> None:
    queues = _all_queues(dlq=3)
    alerts = balancer.check_dlq(queues)
    assert len(alerts) == 1
    assert alerts[0].level == "critical"


def test_check_dlq_silent_when_empty() -> None:
    assert balancer.check_dlq(_all_queues()) == []


def test_check_pools_without_workers() -> None:
    workers = [_worker(Pool.VIDEO)]
    alerts = balancer.check_pools_without_workers(workers)
    pools_alerted = {a.pool for a in alerts}
    assert pools_alerted == {"audio", "metadatos"}


def test_check_pool_saturation_by_queue_depth() -> None:
    queues = _all_queues(**{queue_key(Pool.VIDEO, Priority.HIGH): 100})
    alerts = balancer.check_pool_saturation(
        queues, [], queue_threshold=20, cpu_threshold=85.0
    )
    assert any(a.pool == "video" for a in alerts)


def test_check_pool_saturation_by_cpu_only_when_all_workers_hot() -> None:
    queues = _all_queues()
    hot_workers = [
        _worker(Pool.VIDEO, cpu=95.0, worker_id="w1"),
        _worker(Pool.VIDEO, cpu=10.0, worker_id="w2"),
    ]
    alerts = balancer.check_pool_saturation(
        queues, hot_workers, queue_threshold=20, cpu_threshold=85.0
    )
    assert alerts == []  # no todos los workers vivos están calientes

    all_hot = [
        _worker(Pool.VIDEO, cpu=95.0, worker_id="w1"),
        _worker(Pool.VIDEO, cpu=90.0, worker_id="w2"),
    ]
    alerts_hot = balancer.check_pool_saturation(
        queues, all_hot, queue_threshold=20, cpu_threshold=85.0
    )
    assert any(a.pool == "video" for a in alerts_hot)


def test_check_dead_workers_uses_precomputed_alive_flag() -> None:
    workers = [_worker(Pool.AUDIO, alive=False, worker_id="dead1")]
    alerts = balancer.check_dead_workers(workers)
    assert len(alerts) == 1
    assert "dead1" in alerts[0].message
