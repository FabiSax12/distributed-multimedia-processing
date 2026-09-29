"""Fixtures compartidas: entorno de `moto` (SQS + DynamoDB + S3) fresco para cada test.

Nota importante sobre orden de imports: `coordinator.config.get_settings()` es
`@lru_cache` y lee variables de entorno; el fixture `_env` (autouse, session)
las fija UNA vez al principio de la sesión. Ningún módulo de este paquete
importa `coordinator.main` (que llama `get_settings()` al importarse, para
armar el middleware) a nivel de módulo — siempre dentro de una fixture o de un
test, para no correr esa importación antes de que `_env` haya corrido.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from typing import Any

import boto3
import pytest
from moto import mock_aws

from shared.models import TABLE_CASES, TABLE_SUBTASKS, TABLE_WORKERS
from shared.routing import ALL_WORK_QUEUES, DLQ, RESULTS_QUEUE

AWS_REGION = "us-east-1"
DATASET_BUCKET = "test-dataset-bucket"
RESULTS_BUCKET = "test-results-bucket"

_ALL_QUEUE_NAMES: tuple[str, ...] = (*ALL_WORK_QUEUES, RESULTS_QUEUE, DLQ)


def _queue_url(name: str) -> str:
    # Formato determinístico de moto (cuenta dummy fija 123456789012): así
    # podemos fijar QUEUE_URLS en el entorno ANTES de crear las colas.
    return f"https://sqs.{AWS_REGION}.amazonaws.com/123456789012/{name}"


@pytest.fixture(scope="session", autouse=True)
def _env() -> None:
    """Fija TODAS las variables que `Settings` puede leer, explícitamente.

    Importante: si el repo tiene un `.env.local` real (generado por
    `infra/README.md` para correr contra AWS real desde una laptop),
    pydantic-settings lo lee con prioridad *por encima* de los valores por
    default de cada campo — solo una variable de entorno explícita le gana.
    Sin este fixture, los tests terminarían apuntando a las tablas/colas/
    buckets reales de `.env.local` en vez de a los nombres de prueba de acá,
    y fallarían con `ResourceNotFoundException` (la tabla "Cases" de moto no
    es la tabla "dmp-dev-cases" que diría un `.env.local` real).
    """
    os.environ["AWS_REGION"] = AWS_REGION
    os.environ["QUEUE_URLS"] = json.dumps(
        {name: _queue_url(name) for name in _ALL_QUEUE_NAMES}
    )
    os.environ["TABLE_CASES"] = TABLE_CASES
    os.environ["TABLE_SUBTASKS"] = TABLE_SUBTASKS
    os.environ["TABLE_WORKERS"] = TABLE_WORKERS
    os.environ["DATASET_BUCKET"] = DATASET_BUCKET
    os.environ["RESULTS_BUCKET"] = RESULTS_BUCKET
    os.environ["ORIGIN_VERIFY_SECRET"] = ""
    os.environ["API_PORT"] = "8000"


def _create_table(
    dynamodb: Any, table_name: str, hash_key: str, range_key: str | None = None
) -> None:
    key_schema = [{"AttributeName": hash_key, "KeyType": "HASH"}]
    attr_defs = [{"AttributeName": hash_key, "AttributeType": "S"}]
    if range_key:
        key_schema.append({"AttributeName": range_key, "KeyType": "RANGE"})
        attr_defs.append({"AttributeName": range_key, "AttributeType": "S"})
    dynamodb.create_table(
        TableName=table_name,
        KeySchema=key_schema,
        AttributeDefinitions=attr_defs,
        BillingMode="PAY_PER_REQUEST",
    )


@pytest.fixture
def aws(_env: None) -> Iterator[dict[str, Any]]:
    """Backend de moto fresco por test: colas/tablas/buckets recién creados.

    Los clients cacheados de `coordinator.aws` (creados una sola vez, la
    primera vez que algún test los pide) siguen funcionando contra este
    backend nuevo sin problema: moto parchea boto3 a nivel de transporte, no
    por instancia de client, así que un client creado fuera de un
    `mock_aws()` (o dentro de uno anterior ya cerrado) igual habla con el
    backend que esté activo en el momento de la llamada.
    """
    with mock_aws():
        sqs = boto3.client("sqs", region_name=AWS_REGION)
        dynamodb = boto3.client("dynamodb", region_name=AWS_REGION)
        s3 = boto3.client("s3", region_name=AWS_REGION)

        for name in _ALL_QUEUE_NAMES:
            sqs.create_queue(QueueName=name)

        _create_table(dynamodb, TABLE_CASES, "case_id")
        _create_table(dynamodb, TABLE_SUBTASKS, "case_id", "subtask_id")
        _create_table(dynamodb, TABLE_WORKERS, "worker_id")

        s3.create_bucket(Bucket=DATASET_BUCKET)
        s3.create_bucket(Bucket=RESULTS_BUCKET)

        yield {"sqs": sqs, "dynamodb": dynamodb, "s3": s3}


@pytest.fixture
def settings(aws: dict[str, Any]):
    from coordinator.config import get_settings

    return get_settings()


@pytest.fixture
def repos(aws: dict[str, Any], settings):
    from coordinator.repo.cases import CasesRepo
    from coordinator.repo.subtasks import SubTasksRepo
    from coordinator.repo.workers import WorkersRepo

    return {
        "cases": CasesRepo(aws["dynamodb"], settings.TABLE_CASES),
        "subtasks": SubTasksRepo(aws["dynamodb"], settings.TABLE_SUBTASKS),
        "workers": WorkersRepo(aws["dynamodb"], settings.TABLE_WORKERS),
    }
