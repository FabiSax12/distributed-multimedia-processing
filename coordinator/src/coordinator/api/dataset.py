"""`GET /api/dataset`: archivos disponibles en el bucket del dataset (los usa el panel
para armar un caso, y el generador de carga de un compañero para descubrir qué subir)."""

from __future__ import annotations

import json
import threading
import time
from typing import Any

from botocore.exceptions import ClientError
from fastapi import APIRouter

from shared.api import DatasetEntry
from shared.routing import detect_media_type

from .deps import S3Dep, SettingsDep

router = APIRouter(tags=["dataset"])

_MANIFEST_TTL_S = 60.0
_manifest_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_manifest_lock = threading.Lock()


@router.get("/dataset", response_model=list[DatasetEntry])
def list_dataset(
    settings: SettingsDep,
    s3: S3Dep,
    prefix: str = "",
) -> list[DatasetEntry]:
    manifest = _get_manifest(s3, settings.DATASET_BUCKET, prefix)

    entries: list[DatasetEntry] = []
    kwargs: dict[str, Any] = {"Bucket": settings.DATASET_BUCKET, "Prefix": prefix}
    while True:
        resp = s3.list_objects_v2(**kwargs)
        for obj in resp.get("Contents", []):
            key = obj["Key"]
            if key.endswith(".json"):
                continue  # manifest.json y similares no son entradas multimedia
            entries.append(
                DatasetEntry(
                    input_key=key,
                    size_bytes=obj["Size"],
                    media_type=detect_media_type(key),
                    metadata=manifest.get(key, {}),
                )
            )
        if not resp.get("IsTruncated"):
            break
        kwargs["ContinuationToken"] = resp["NextContinuationToken"]

    return entries


def _get_manifest(s3: Any, bucket: str, prefix: str) -> dict[str, Any]:
    """`{prefix}manifest.json` cacheado `_MANIFEST_TTL_S` segundos, por prefijo.

    Convención del dataset: un `manifest.json` opcional por prefijo, dict de
    `input_key -> metadata`. Si no existe, las entradas de ese prefijo
    simplemente no traen `metadata`.
    """
    now = time.monotonic()
    with _manifest_lock:
        cached = _manifest_cache.get(prefix)
        if cached is not None and now - cached[0] < _MANIFEST_TTL_S:
            return cached[1]

    manifest = _load_manifest(s3, bucket, prefix)
    with _manifest_lock:
        _manifest_cache[prefix] = (now, manifest)
    return manifest


def _load_manifest(s3: Any, bucket: str, prefix: str) -> dict[str, Any]:
    key = f"{prefix}manifest.json"
    try:
        resp = s3.get_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")
        if code in ("NoSuchKey", "404"):
            return {}
        raise
    return json.loads(resp["Body"].read())
