"""Fixtures compartidas: moto (SQS + DynamoDB + S3) fresco por test y archivos
multimedia reales generados con ffmpeg una vez por sesión.

Igual que en `coordinator/tests/conftest.py`, `_env` fija TODAS las variables
que `workers.config.Settings` puede leer, para que un `.env.local` real en la
raíz del repo nunca se cuele en los tests.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import boto3
import pytest
from moto import mock_aws

from shared.models import TABLE_CASES, TABLE_WORKERS
from shared.routing import ALL_WORK_QUEUES, DLQ, RESULTS_QUEUE

AWS_REGION = "us-east-1"
DATASET_BUCKET = "test-dataset-bucket"
RESULTS_BUCKET = "test-results-bucket"

_ALL_QUEUE_NAMES: tuple[str, ...] = (*ALL_WORK_QUEUES, RESULTS_QUEUE, DLQ)

# Mismos visibility timeouts que infra/modules/queues/variables.tf.
VISIBILITY_TIMEOUTS = {"video": 900, "audio": 300, "metadatos": 120}


def queue_url(name: str) -> str:
    # Formato determinístico de moto (cuenta dummy 123456789012).
    return f"https://sqs.{AWS_REGION}.amazonaws.com/123456789012/{name}"


QUEUE_URLS = {name: queue_url(name) for name in _ALL_QUEUE_NAMES}


@pytest.fixture(scope="session", autouse=True)
def _env() -> None:
    os.environ["POOL"] = "video"
    os.environ["AWS_REGION"] = AWS_REGION
    os.environ["AWS_DEFAULT_REGION"] = AWS_REGION
    os.environ["QUEUE_URLS"] = json.dumps(QUEUE_URLS)
    os.environ["TABLE_CASES"] = TABLE_CASES
    os.environ["TABLE_WORKERS"] = TABLE_WORKERS
    os.environ["DATASET_BUCKET"] = DATASET_BUCKET
    os.environ["RESULTS_BUCKET"] = RESULTS_BUCKET
    os.environ.pop("WORKER_CONCURRENCY", None)
    # Que ningún endpoint local (p. ej. moto server de local_e2e.py) se cuele.
    os.environ.pop("AWS_ENDPOINT_URL", None)


@pytest.fixture
def aws(_env: None) -> Iterator[dict[str, Any]]:
    with mock_aws():
        sqs = boto3.client("sqs", region_name=AWS_REGION)
        dynamodb = boto3.client("dynamodb", region_name=AWS_REGION)
        s3 = boto3.client("s3", region_name=AWS_REGION)

        for name in _ALL_QUEUE_NAMES:
            pool = name.split("-")[0]
            attrs = {}
            if pool in VISIBILITY_TIMEOUTS:
                attrs["VisibilityTimeout"] = str(VISIBILITY_TIMEOUTS[pool])
            sqs.create_queue(QueueName=name, Attributes=attrs)

        for table, key in ((TABLE_CASES, "case_id"), (TABLE_WORKERS, "worker_id")):
            dynamodb.create_table(
                TableName=table,
                KeySchema=[{"AttributeName": key, "KeyType": "HASH"}],
                AttributeDefinitions=[{"AttributeName": key, "AttributeType": "S"}],
                BillingMode="PAY_PER_REQUEST",
            )

        s3.create_bucket(Bucket=DATASET_BUCKET)
        s3.create_bucket(Bucket=RESULTS_BUCKET)

        yield {"sqs": sqs, "dynamodb": dynamodb, "s3": s3}


def _ffmpeg(*args: str) -> None:
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True
    )


@pytest.fixture(scope="session")
def media(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """Archivos chicos pero reales (2 s) para ejercitar ffmpeg de verdad."""
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg/ffprobe no están instalados")

    root = tmp_path_factory.mktemp("media")
    files = {
        "video": root / "clip.mp4",
        "silent_video": root / "mudo.mp4",
        "audio": root / "Queen - Bohemian Rhapsody.mp3",
        "image": root / "foto.png",
        "corrupt": root / "roto.mp4",
    }
    _ffmpeg(
        "-f", "lavfi", "-i", "testsrc=duration=2:size=320x240:rate=15",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
        str(files["video"]),
    )  # fmt: skip
    _ffmpeg(
        "-f", "lavfi", "-i", "testsrc=duration=2:size=320x240:rate=15",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(files["silent_video"]),
    )  # fmt: skip
    _ffmpeg(
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
        "-metadata", "artist=Queen", "-metadata", "title=Bohemian Rhapsody",
        "-metadata", "genre=Rock", "-c:a", "libmp3lame", str(files["audio"]),
    )  # fmt: skip
    _ffmpeg(
        "-f", "lavfi", "-i", "testsrc=size=1280x720", "-frames:v", "1",
        str(files["image"]),
    )  # fmt: skip
    files["corrupt"].write_bytes(b"esto no es un video")
    return files
