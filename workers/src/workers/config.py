"""Configuración del worker, leída de variables de entorno.

En EC2 las escribe el `user_data` de Terraform en `/etc/dmp.env` (incluido
`POOL`). En una laptop se reutiliza el `.env.local` de la raíz del repo (el
mismo que usa el coordinador) y se exporta `POOL` a mano: `.env.local` no lo
trae porque es por instancia.

No se importa `coordinator.config`: el worker no debe arrastrar FastAPI ni
uvicorn a las máquinas worker solo para leer variables de entorno.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from shared.models import TABLE_CASES, TABLE_WORKERS
from shared.routing import POLL_ORDER, RESULTS_QUEUE, Pool

# Hilos consumidores por proceso. Video y audio están ligados a CPU (ffmpeg en
# instancias de 2 vCPU); metadatos pasa casi todo el tiempo esperando APIs
# externas, así que un t3.micro aguanta más sub-tareas a la vez.
DEFAULT_CONCURRENCY: dict[Pool, int] = {Pool.VIDEO: 2, Pool.AUDIO: 2, Pool.METADATA: 4}


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in (here, *here.parents):
        if (parent / ".git").exists() or (parent / "infra").is_dir():
            return parent
    return here.parents[3]  # workers/src/workers/config.py -> raíz del repo


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_repo_root() / ".env.local",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    POOL: Pool
    AWS_REGION: str

    # Mismo JSON que el coordinador (las 8 colas lógicas). El worker solo exige
    # las de su POLL_ORDER más `resultados`.
    QUEUE_URLS: dict[str, str]

    TABLE_CASES: str = TABLE_CASES
    TABLE_WORKERS: str = TABLE_WORKERS

    DATASET_BUCKET: str
    RESULTS_BUCKET: str

    WORKER_CONCURRENCY: int | None = Field(default=None, ge=1)

    @field_validator("QUEUE_URLS", mode="before")
    @classmethod
    def _parse_queue_urls(cls, value: Any) -> Any:
        if isinstance(value, str):
            value = json.loads(value)
        return value

    @model_validator(mode="after")
    def _check_queue_urls(self) -> Settings:
        needed = {*POLL_ORDER[self.POOL], RESULTS_QUEUE}
        missing = needed - self.QUEUE_URLS.keys()
        if missing:
            raise ValueError(
                f"QUEUE_URLS no trae las colas del pool {self.POOL.value}: {sorted(missing)}"
            )
        return self

    @property
    def concurrency(self) -> int:
        return self.WORKER_CONCURRENCY or DEFAULT_CONCURRENCY[self.POOL]

    @property
    def poll_order(self) -> tuple[str, ...]:
        return POLL_ORDER[self.POOL]


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # los campos requeridos vienen de env/.env.local
