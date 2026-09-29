"""`/api/uploads`, `/api/dataset`, `/api/state`, `/healthz`: cobertura rápida de los
routers que no tienen su propio archivo de test dedicado."""

from __future__ import annotations

import contextlib
import json
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient


@contextlib.asynccontextmanager
async def _noop_lifespan(app):
    yield


@pytest.fixture
def client(aws: dict) -> Iterator[TestClient]:
    from coordinator.main import app

    app.router.lifespan_context = _noop_lifespan
    with TestClient(app) as test_client:
        yield test_client


def test_healthz_ok_without_background_threads(client: TestClient) -> None:
    # Con el lifespan no-op no hay `app.state.threads`: sin hilos que chequear,
    # `all(alive.values())` sobre un dict vacío es `True` -> 200.
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "threads": {}}


def test_state_returns_empty_response_before_first_snapshot(client: TestClient) -> None:
    resp = client.get("/api/state")
    assert resp.status_code == 200
    body = resp.json()
    assert body["cases"] == []
    assert body["workers"] == []
    assert body["queues"] == []
    assert body["alerts"] == []


def test_metrics_returns_empty_list_before_any_sample(client: TestClient) -> None:
    resp = client.get("/api/metrics?minutes=10")
    assert resp.status_code == 200
    assert resp.json() == {"samples": []}


def test_create_upload_urls_sanitizes_filenames(client: TestClient) -> None:
    resp = client.post(
        "/api/uploads", json={"filenames": ["a.mp4", "../../etc/passwd", "b/c.mp3"]}
    )
    assert resp.status_code == 201
    body = resp.json()
    filenames = [u["filename"] for u in body["urls"]]
    assert filenames == ["a.mp4", "passwd", "c.mp3"]
    for entry in body["urls"]:
        assert entry["input_key"] == f"uploads/{body['upload_id']}/{entry['filename']}"


def test_list_dataset_reads_manifest_and_skips_json(
    client: TestClient, aws: dict, settings
) -> None:
    bucket = settings.DATASET_BUCKET
    aws["s3"].put_object(Bucket=bucket, Key="batch1/a.mp4", Body=b"x")
    aws["s3"].put_object(Bucket=bucket, Key="batch1/notes.txt", Body=b"y")
    aws["s3"].put_object(
        Bucket=bucket,
        Key="batch1/manifest.json",
        Body=json.dumps({"batch1/a.mp4": {"source": "camara1"}}).encode(),
    )

    resp = client.get("/api/dataset?prefix=batch1/")
    assert resp.status_code == 200
    entries = {e["input_key"]: e for e in resp.json()}

    assert "batch1/manifest.json" not in entries
    assert entries["batch1/a.mp4"]["media_type"] == "video"
    assert entries["batch1/a.mp4"]["metadata"] == {"source": "camara1"}
    assert entries["batch1/notes.txt"]["media_type"] is None
    assert entries["batch1/notes.txt"]["metadata"] == {}
