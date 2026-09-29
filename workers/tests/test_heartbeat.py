"""`runner/heartbeat.py`: el único ítem de DynamoDB que escribe el worker.

El coordinador arma con esto la carga por worker (CPU, memoria, sub-tareas en
curso) y marca caído a quien pase `WORKER_DEAD_AFTER_S` sin reportar.
"""

from __future__ import annotations

import threading

from boto3.dynamodb.types import TypeDeserializer

from shared.models import TABLE_WORKERS, WorkerItem, from_item
from shared.routing import Pool
from workers.runner.heartbeat import Heartbeat

_deserializer = TypeDeserializer()


def _heartbeat(aws) -> Heartbeat:
    return Heartbeat(
        aws["dynamodb"],
        TABLE_WORKERS,
        worker_id="video-i-0abc",
        pool=Pool.VIDEO,
        instance_type="c7i-flex.large",
        hostname="ip-10-0-0-1",
        concurrency=2,
    )


def _read(aws, worker_id: str) -> WorkerItem:
    raw = aws["dynamodb"].get_item(
        TableName=TABLE_WORKERS, Key={"worker_id": {"S": worker_id}}
    )["Item"]
    return from_item(WorkerItem, {k: _deserializer.deserialize(v) for k, v in raw.items()})


def test_beat_writes_worker_item(aws) -> None:
    _heartbeat(aws).beat()

    item = _read(aws, "video-i-0abc")
    assert item.pool is Pool.VIDEO
    assert item.concurrency == 2
    assert 0 <= item.cpu_percent <= 100
    assert 0 < item.mem_percent <= 100
    assert item.is_alive()


def test_active_subtasks_follow_track(aws) -> None:
    heartbeat = _heartbeat(aws)

    with heartbeat.track("st-1"), heartbeat.track("st-2"):
        heartbeat.beat()
        assert _read(aws, "video-i-0abc").active_subtasks == ["st-1", "st-2"]

    heartbeat.beat()
    assert _read(aws, "video-i-0abc").active_subtasks == []


def test_run_survives_a_failed_beat_and_stops_on_event(aws, monkeypatch) -> None:
    heartbeat = _heartbeat(aws)
    calls = []

    def flaky_beat() -> None:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("DynamoDB throttling")
        stop.set()

    monkeypatch.setattr(heartbeat, "beat", flaky_beat)
    stop = threading.Event()

    heartbeat.run(stop, interval=0.01)

    # El primer fallo no mata el hilo: el segundo beat corrió y lo detuvo.
    assert len(calls) == 2
