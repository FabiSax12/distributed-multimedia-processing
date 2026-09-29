"""Configuración del coordinador, leída de variables de entorno.

En AWS (EC2) las variables las inyecta Terraform vía `user_data`/systemd. En
una laptop, `infra/README.md` genera `../.env.local` (gitignored) en la raíz
del repo con las mismas variables, para correr el coordinador contra recursos
reales de AWS sin desplegar nada. `_repo_root()` busca esa raíz subiendo desde
este archivo (marcador: `.git/` o `infra/`) en vez de asumir una profundidad
fija de directorios, así sigue funcionando si el paquete se reubica.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from shared.models import TABLE_CASES, TABLE_SUBTASKS, TABLE_WORKERS
from shared.routing import ALL_WORK_QUEUES, DLQ, RESULTS_QUEUE

_EXPECTED_QUEUE_KEYS = frozenset({*ALL_WORK_QUEUES, RESULTS_QUEUE, DLQ})


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in (here, *here.parents):
        if (parent / ".git").exists() or (parent / "infra").is_dir():
            return parent
    return here.parents[3]  # coordinator/src/coordinator/config.py -> raíz del repo


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_repo_root() / ".env.local",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    AWS_REGION: str

    # JSON: {"video-alta": "https://sqs..../video-alta", ..., "resultados": "...", "dlq": "..."}
    # Debe traer exactamente las 8 colas lógicas (6 de trabajo + resultados + dlq).
    QUEUE_URLS: dict[str, str]

    TABLE_CASES: str = TABLE_CASES
    TABLE_SUBTASKS: str = TABLE_SUBTASKS
    TABLE_WORKERS: str = TABLE_WORKERS

    DATASET_BUCKET: str
    RESULTS_BUCKET: str

    API_PORT: int = 8000
    ORIGIN_VERIFY_SECRET: str = ""

    RESULTS_CONSUMER_THREADS: int = 4
    PRESIGN_EXPIRES_S: int = 900
    SWEEP_INTERVAL_S: int = 60
    SNAPSHOT_INTERVAL_S: int = 2
    SAMPLE_INTERVAL_S: int = 5
    METRICS_FLUSH_S: int = 60
    DLQ_ASSUMED_ATTEMPTS: int = 3
    RECENT_CASES_IN_STATE: int = 50

    # monitor/balancer.py: mensajes visibles en la cola de alta prioridad de un
    # pool por encima de los cuales se considera "saturado" (ver docstring de
    # `balancer.check_pool_saturation`).
    POOL_SATURATION_QUEUE_THRESHOLD: int = 20
    POOL_SATURATION_CPU_PERCENT: float = 85.0

    @field_validator("QUEUE_URLS", mode="before")
    @classmethod
    def _parse_queue_urls(cls, value: Any) -> Any:
        if isinstance(value, str):
            value = json.loads(value)
        return value

    @field_validator("QUEUE_URLS")
    @classmethod
    def _check_queue_urls(cls, value: dict[str, str]) -> dict[str, str]:
        missing = _EXPECTED_QUEUE_KEYS - value.keys()
        extra = value.keys() - _EXPECTED_QUEUE_KEYS
        if missing:
            raise ValueError(
                f"QUEUE_URLS no tiene las 8 colas esperadas, faltan: {sorted(missing)}"
            )
        if extra:
            raise ValueError(f"QUEUE_URLS trae claves inesperadas: {sorted(extra)}")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # los campos requeridos vienen de env/.env.local
