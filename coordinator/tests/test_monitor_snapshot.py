"""`monitor/snapshot.py` + `monitor/samples.py`: una vuelta de cada uno, sin loop."""

from __future__ import annotations

import coordinator.monitor.samples as samples_module
import coordinator.monitor.snapshot as snapshot_module
from coordinator.aws import serialize_item
from coordinator.monitor.samples import get_recent_samples, take_sample_once
from coordinator.monitor.snapshot import get_latest_snapshot, run_snapshot_once

from shared.messages import utcnow
from shared.models import CaseItem, WorkerItem, to_item
from shared.routing import Pool


def test_run_snapshot_once_builds_and_caches_state(repos, aws, settings) -> None:
    repos["cases"].put_new(
        CaseItem(case_id="c1", file_count=1, total=1, pending_count=1)
    )

    worker = WorkerItem(
        worker_id="video-host-1",
        pool=Pool.VIDEO,
        instance_type="c7i-flex.large",
        hostname="host",
        concurrency=1,
        cpu_percent=42.0,
        mem_percent=10.0,
        started_at=utcnow(),
        last_seen=utcnow(),
    )
    aws["dynamodb"].put_item(
        TableName=settings.TABLE_WORKERS, Item=serialize_item(to_item(worker))
    )

    run_snapshot_once(
        cases_repo=repos["cases"],
        subtasks_repo=repos["subtasks"],
        workers_repo=repos["workers"],
        sqs=aws["sqs"],
        queue_urls=settings.QUEUE_URLS,
        recent_cases_limit=settings.RECENT_CASES_IN_STATE,
        pool_saturation_queue_threshold=settings.POOL_SATURATION_QUEUE_THRESHOLD,
        pool_saturation_cpu_percent=settings.POOL_SATURATION_CPU_PERCENT,
    )

    snapshot = get_latest_snapshot()
    assert snapshot is not None
    assert len(snapshot.cases) == 1
    assert snapshot.cases[0].case.case_id == "c1"
    assert len(snapshot.workers) == 1
    assert snapshot.workers[0].alive is True
    assert len(snapshot.queues) == 8
    # Sin workers en los pools audio/metadatos -> alertas críticas del balancer.
    assert any(a.pool == "audio" for a in snapshot.alerts)


def test_take_sample_once_noop_without_snapshot(monkeypatch) -> None:
    monkeypatch.setattr(snapshot_module, "_snapshot", None)
    monkeypatch.setattr(samples_module, "_ring", None)
    monkeypatch.setattr(samples_module, "_pending_flush", [])

    take_sample_once()
    assert get_recent_samples(60) == []
