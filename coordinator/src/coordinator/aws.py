"""Clients de boto3 compartidos por todo el coordinador.

Usamos clients de bajo nivel (no `boto3.resource`) para DynamoDB porque son
thread-safe y porque el resto del código combina `shared.models.to_item`/
`from_item` (que trabajan con dicts Python planos) con `TypeSerializer`/
`TypeDeserializer` de `boto3.dynamodb.types` para ir y volver del formato de
atributos de DynamoDB (`{"S": "..."}`, `{"N": "..."}`, etc.). Un solo client
por servicio y por proceso: boto3 los recomienda como thread-safe y crear uno
nuevo por request desperdicia conexiones.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

import boto3
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from botocore.config import Config

from .config import get_settings

_BOTO_CONFIG = Config(retries={"mode": "standard", "max_attempts": 5})

_serializer = TypeSerializer()
_deserializer = TypeDeserializer()


@lru_cache
def sqs_client():
    return boto3.client(
        "sqs", region_name=get_settings().AWS_REGION, config=_BOTO_CONFIG
    )


@lru_cache
def dynamodb_client():
    return boto3.client(
        "dynamodb", region_name=get_settings().AWS_REGION, config=_BOTO_CONFIG
    )


@lru_cache
def s3_client():
    return boto3.client(
        "s3", region_name=get_settings().AWS_REGION, config=_BOTO_CONFIG
    )


def serialize_item(item: dict[str, Any]) -> dict[str, Any]:
    """dict Python (salida de `shared.models.to_item`) -> formato DynamoDB de bajo nivel."""
    return {k: _serializer.serialize(v) for k, v in item.items()}


def deserialize_item(item: dict[str, Any]) -> dict[str, Any]:
    """Formato DynamoDB de bajo nivel -> dict Python (entrada de `shared.models.from_item`)."""
    return {k: _deserializer.deserialize(v) for k, v in item.items()}
