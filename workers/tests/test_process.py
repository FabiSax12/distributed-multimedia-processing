"""`runner/process.py::handle_message`: el protocolo completo de una sub-tarea.

Mismo contrato que `coordinator/scripts/fake_worker.py`:
- cancelación consultada antes de empezar (`Cases.cancel_requested`);
- `retrying` si el mensaje ya se entregó antes;
- `assigned` -> `running` (progreso) -> un solo estado terminal;
- el mensaje de trabajo se borra DESPUÉS de publicar el terminal;
- una excepción inesperada no publica terminal ni borra: SQS reintenta y, al
  agotar intentos, la DLQ se la entrega al coordinador.
"""

from __future__ import annotations

from typing import Any

from shared.messages import ErrorCode, ResultMessage
from shared.models import TABLE_CASES, TABLE_WORKERS
from shared.routing import POLL_ORDER, Pool
from shared.states import SubTaskStatus
from workers.ops.base import OpOutput
from workers.runner import process
from workers.runner.heartbeat import Heartbeat
from workers.runner.poller import receive_next
from workers.runner.process import WorkerContext, handle_message

from .conftest import DATASET_BUCKET, QUEUE_URLS, RESULTS_BUCKET, make_task


def _ctx(aws: dict[str, Any], pool: Pool = Pool.AUDIO) -> WorkerContext:
    return WorkerContext(
        sqs=aws["sqs"],
        s3=aws["s3"],
        dynamodb=aws["dynamodb"],
        queue_urls=QUEUE_URLS,
        cases_table=TABLE_CASES,
        dataset_bucket=DATASET_BUCKET,
        results_bucket=RESULTS_BUCKET,
        worker_id="audio-test-1",
        pool=pool,
        heartbeat=Heartbeat(
            aws["dynamodb"],
            TABLE_WORKERS,
            worker_id="audio-test-1",
            pool=pool,
            instance_type="local",
            hostname="test",
            concurrency=1,
        ),
        timeouts={queue: 60 for queue in POLL_ORDER[pool]},
    )


def _enqueue(aws, task, queue: str = "audio-normal", src=None) -> None:
    if src is not None:
        aws["s3"].upload_file(str(src), DATASET_BUCKET, task.input_key)
    aws["sqs"].send_message(QueueUrl=QUEUE_URLS[queue], MessageBody=task.to_body())


def _receive(aws, pool: Pool = Pool.AUDIO):
    received = receive_next(aws["sqs"], QUEUE_URLS, POLL_ORDER[pool], wait_s=0)
    assert received is not None
    return received


def _results(aws) -> list[ResultMessage]:
    out = []
    while True:
        resp = aws["sqs"].receive_message(
            QueueUrl=QUEUE_URLS["resultados"], MaxNumberOfMessages=10
        )
        if not resp.get("Messages"):
            return out
        out.extend(ResultMessage.from_body(m["Body"]) for m in resp["Messages"])


def _in_queue(aws, queue: str) -> int:
    attrs = aws["sqs"].get_queue_attributes(
        QueueUrl=QUEUE_URLS[queue],
        AttributeNames=[
            "ApproximateNumberOfMessages",
            "ApproximateNumberOfMessagesNotVisible",
        ],
    )["Attributes"]
    return int(attrs["ApproximateNumberOfMessages"]) + int(
        attrs["ApproximateNumberOfMessagesNotVisible"]
    )


def test_completed_subtask_uploads_outputs_and_deletes_message(aws, media) -> None:
    task = make_task("audio_convert", "audio", input_key="uploads/u1/cancion.mp3")
    _enqueue(aws, task, src=media["audio"])

    handle_message(_ctx(aws), *_receive(aws))

    results = _results(aws)
    statuses = [r.status for r in results]
    assert statuses[0] is SubTaskStatus.ASSIGNED
    assert statuses[-1] is SubTaskStatus.COMPLETED
    assert set(statuses[1:-1]) == {SubTaskStatus.RUNNING}
    final = results[-1]
    assert final.output_keys == ["results/case-1/st-1/cancion.mp3"]
    assert final.started_at is not None and final.finished_at is not None
    assert (final.worker_id, final.pool, final.attempt) == (
        "audio-test-1",
        Pool.AUDIO,
        1,
    )
    assert final.output_meta["target_format"] == "mp3"
    aws["s3"].head_object(Bucket=RESULTS_BUCKET, Key=final.output_keys[0])
    assert _in_queue(aws, "audio-normal") == 0


def test_running_carries_started_at(aws, media) -> None:
    # SQS estándar puede entregarle al coordinador un `running` antes que el
    # `assigned`; si el `running` no trae `started_at`, el `assigned` se
    # descarta por `state_order` y la sub-tarea queda sin hora de inicio.
    _enqueue(aws, make_task("audio_convert", "audio"), src=media["audio"])

    handle_message(_ctx(aws), *_receive(aws))

    results = _results(aws)
    started = {r.started_at for r in results if r.status is not SubTaskStatus.COMPLETED}
    assert len(started) == 1 and None not in started


def test_cancelled_case_is_reported_without_processing(aws, media, monkeypatch) -> None:
    aws["dynamodb"].put_item(
        TableName=TABLE_CASES,
        Item={"case_id": {"S": "case-1"}, "cancel_requested": {"BOOL": True}},
    )
    monkeypatch.setitem(process.OPERATIONS, "audio_convert", _must_not_run)
    _enqueue(aws, make_task("audio_convert", "audio"), src=media["audio"])

    handle_message(_ctx(aws), *_receive(aws))

    [result] = _results(aws)
    assert result.status is SubTaskStatus.CANCELLED
    assert result.error.code is ErrorCode.CANCELLED
    assert _in_queue(aws, "audio-normal") == 0


def _must_not_run(*args, **kwargs):
    raise AssertionError("la operación no debía correr")


def test_operation_error_is_reported_as_failed(aws, media) -> None:
    _enqueue(aws, make_task("audio_convert", "audio"), src=media["corrupt"])

    handle_message(_ctx(aws), *_receive(aws))

    final = _results(aws)[-1]
    assert final.status is SubTaskStatus.FAILED
    assert final.error.code is ErrorCode.CORRUPT_INPUT
    assert final.finished_at is not None
    assert _in_queue(aws, "audio-normal") == 0


def test_missing_input_is_corrupt_input(aws) -> None:
    _enqueue(aws, make_task("audio_convert", "audio"))  # nunca se subió al dataset

    handle_message(_ctx(aws), *_receive(aws))

    final = _results(aws)[-1]
    assert final.status is SubTaskStatus.FAILED
    assert final.error.code is ErrorCode.CORRUPT_INPUT


def test_unexpected_exception_keeps_message_for_sqs_retry(
    aws, media, monkeypatch
) -> None:
    def boom(*args, **kwargs):
        raise RuntimeError("bug o falla transitoria")

    monkeypatch.setitem(process.OPERATIONS, "audio_convert", boom)
    _enqueue(aws, make_task("audio_convert", "audio"), src=media["audio"])

    handle_message(_ctx(aws), *_receive(aws))

    # Ningún terminal: si el worker publicara `failed`, el reintento de SQS
    # y la DLQ no servirían de nada.
    assert all(r.status not in _TERMINAL for r in _results(aws))
    assert _in_queue(aws, "audio-normal") == 1


_TERMINAL = {SubTaskStatus.COMPLETED, SubTaskStatus.FAILED, SubTaskStatus.CANCELLED}


def test_redelivered_message_reports_retrying_first(aws, media) -> None:
    _enqueue(aws, make_task("audio_convert", "audio"), src=media["audio"])
    _, first = _receive(aws)
    # Simula un worker caído: el mensaje vuelve a ser visible sin borrarse.
    aws["sqs"].change_message_visibility(
        QueueUrl=QUEUE_URLS["audio-normal"],
        ReceiptHandle=first["ReceiptHandle"],
        VisibilityTimeout=0,
    )

    handle_message(_ctx(aws), *_receive(aws))

    results = _results(aws)
    assert (results[0].status, results[0].attempt) == (SubTaskStatus.RETRYING, 2)
    assert results[1].status is SubTaskStatus.ASSIGNED
    assert results[-1].status is SubTaskStatus.COMPLETED


def test_unreadable_message_is_dropped(aws) -> None:
    aws["sqs"].send_message(
        QueueUrl=QUEUE_URLS["audio-normal"], MessageBody="{no es json"
    )

    handle_message(_ctx(aws), *_receive(aws))

    assert _results(aws) == []
    assert _in_queue(aws, "audio-normal") == 0


def test_progress_is_throttled_to_ten_point_steps(aws, media, monkeypatch) -> None:
    def fake_op(task, src, out_dir, *, timeout_s, on_progress):
        for pct in (5, 12, 15, 47, 99):
            on_progress(pct)
        out = out_dir / "x.txt"
        out.write_text("ok")
        return OpOutput([out])

    monkeypatch.setitem(process.OPERATIONS, "audio_convert", fake_op)
    _enqueue(aws, make_task("audio_convert", "audio"), src=media["audio"])

    handle_message(_ctx(aws), *_receive(aws))

    running = [r.progress for r in _results(aws) if r.status is SubTaskStatus.RUNNING]
    assert running == [0, 12, 47, 99]


def test_worker_helping_another_pool_uses_that_queue(aws, media) -> None:
    # metadatos ayuda con audio-normal (POLL_ORDER): procesa y borra de ahí.
    _enqueue(
        aws,
        make_task("image_thumbnail", "image"),
        queue="audio-normal",
        src=media["image"],
    )

    handle_message(_ctx(aws, Pool.METADATA), *_receive(aws, Pool.METADATA))

    final = _results(aws)[-1]
    assert final.status is SubTaskStatus.COMPLETED
    assert final.pool is Pool.METADATA
    assert _in_queue(aws, "audio-normal") == 0


def test_output_meta_never_carries_floats(aws, media, monkeypatch) -> None:
    # El coordinador guarda output_meta tal cual en DynamoDB, que rechaza
    # float ("Use Decimal types instead"): el caso quedaría sin cerrar.
    def fake_op(task, src, out_dir, *, timeout_s, on_progress):
        out = out_dir / "x.txt"
        out.write_text("ok")
        return OpOutput([out], {"ratio": 0.5, "nested": {"fps": 23.976}, "n": 3})

    monkeypatch.setitem(process.OPERATIONS, "audio_convert", fake_op)
    _enqueue(aws, make_task("audio_convert", "audio"), src=media["audio"])

    handle_message(_ctx(aws), *_receive(aws))

    final = _results(aws)[-1]
    assert final.status is SubTaskStatus.COMPLETED
    assert final.output_meta == {"ratio": "0.5", "nested": {"fps": "23.976"}, "n": 3}
