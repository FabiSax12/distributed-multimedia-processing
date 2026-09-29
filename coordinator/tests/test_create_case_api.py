"""Integración: `POST /api/cases` a través del router real (TestClient), verificando
que escribe sub-tareas + caso con contadores correctos y encola en las colas correctas.

Usa `coordinator.main.app` pero con el `lifespan` reemplazado por uno vacío: no
queremos que los hilos de fondo reales (consumers de SQS con long-polling de
hasta 20s, sweeper, snapshot, samples) arranquen en un test de API — eso es
responsabilidad de `main.py` y no hace falta re-probarlo acá para validar el
endpoint. El middleware, los routers y las escrituras a DynamoDB/SQS sí corren
de verdad, contra el backend de moto de la fixture `aws`.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from shared.routing import Pool, Priority, queue_key


@contextlib.asynccontextmanager
async def _noop_lifespan(app):
    yield


@pytest.fixture
def client(aws: dict) -> Iterator[TestClient]:
    from coordinator.main import app

    app.router.lifespan_context = _noop_lifespan
    with TestClient(app) as test_client:
        yield test_client


def _put_dataset_object(aws: dict, bucket: str, key: str, body: bytes = b"x") -> None:
    aws["s3"].put_object(Bucket=bucket, Key=key, Body=body)


def test_create_case_writes_subtasks_case_and_enqueues(
    client: TestClient, aws: dict, settings
) -> None:
    _put_dataset_object(aws, settings.DATASET_BUCKET, "dataset/a.mp4")
    _put_dataset_object(aws, settings.DATASET_BUCKET, "dataset/b.mp3")

    resp = client.post(
        "/api/cases",
        json={
            "files": [{"input_key": "dataset/a.mp4"}, {"input_key": "dataset/b.mp3"}]
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["file_count"] == 2
    assert body["subtask_count"] == 5  # video: 3 + audio: 2
    assert body["prefailed_count"] == 0
    case_id = body["case_id"]

    detail = client.get(f"/api/cases/{case_id}").json()
    assert detail["case"]["total"] == 5
    assert detail["case"]["pending_count"] == 5
    assert detail["case"]["status"] == "queued"
    assert len(detail["subtasks"]) == 5

    # Cada sub-tarea encolada quedó marcada `enqueued=True` y tiene mensaje en
    # su cola lógica correspondiente.
    assert all(st["enqueued"] for st in detail["subtasks"])

    # b.mp3 sin `operations` explícitas usa DEFAULT_OPERATIONS[AUDIO] =
    # (AUDIO_CONVERT, METADATA): una sub-tarea va al pool audio, la otra al
    # pool metadatos (no las dos al pool audio).
    def _visible(queue_name: str) -> int:
        return int(
            aws["sqs"].get_queue_attributes(
                QueueUrl=settings.QUEUE_URLS[queue_name],
                AttributeNames=["ApproximateNumberOfMessages"],
            )["Attributes"]["ApproximateNumberOfMessages"]
        )

    assert _visible(queue_key(Pool.VIDEO, Priority.NORMAL)) == 3
    assert _visible(queue_key(Pool.AUDIO, Priority.NORMAL)) == 1
    assert _visible(queue_key(Pool.METADATA, Priority.NORMAL)) == 1


def test_create_case_missing_input_key_returns_422(
    client: TestClient, aws: dict, settings
) -> None:
    resp = client.post(
        "/api/cases", json={"files": [{"input_key": "dataset/does-not-exist.mp4"}]}
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["missing"] == ["dataset/does-not-exist.mp4"]


def test_create_case_all_prefailed_closes_immediately_as_failed(
    client: TestClient, aws: dict, settings
) -> None:
    _put_dataset_object(aws, settings.DATASET_BUCKET, "dataset/notes.txt")

    resp = client.post(
        "/api/cases", json={"files": [{"input_key": "dataset/notes.txt"}]}
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["prefailed_count"] == 1
    case_id = body["case_id"]

    detail = client.get(f"/api/cases/{case_id}").json()
    assert detail["case"]["status"] == "failed"
    assert detail["case"]["pending_count"] == 0
    assert detail["case"]["report_key"] is not None

    # El reporte tiene que haberse subido a S3 de verdad.
    report_resp = client.get(f"/api/cases/{case_id}/report")
    assert report_resp.status_code == 200
