"""`config.py`: el worker valida al arrancar que tiene las colas que va a consumir.

Un `QUEUE_URLS` incompleto tiene que fallar al construir `Settings`, no en el
primer `receive_message` minutos después.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from shared.routing import POLL_ORDER, RESULTS_QUEUE, Pool
from workers.config import Settings

from .conftest import QUEUE_URLS


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


def test_reads_env_and_parses_queue_urls_json() -> None:
    settings = _settings()

    assert settings.POOL is Pool.VIDEO
    assert settings.QUEUE_URLS == QUEUE_URLS


def test_missing_queue_from_poll_order_fails_fast() -> None:
    urls = dict(QUEUE_URLS)
    del urls["audio-normal"]  # video ayuda a audio-normal (POLL_ORDER)

    with pytest.raises(ValidationError, match="audio-normal"):
        _settings(QUEUE_URLS=json.dumps(urls))


def test_missing_results_queue_fails_fast() -> None:
    urls = {k: v for k, v in QUEUE_URLS.items() if k != RESULTS_QUEUE}

    with pytest.raises(ValidationError, match=RESULTS_QUEUE):
        # JSON (como en /etc/dmp.env): un dict se mezclaría con el del entorno.
        _settings(QUEUE_URLS=json.dumps(urls))


def test_unknown_pool_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _settings(POOL="gpu")


@pytest.mark.parametrize(
    ("pool", "expected"),
    [(Pool.VIDEO, 2), (Pool.AUDIO, 2), (Pool.METADATA, 4)],
)
def test_default_concurrency_depends_on_pool(pool: Pool, expected: int) -> None:
    # metadatos espera red, no CPU: más hilos por máquina.
    assert _settings(POOL=pool.value).concurrency == expected


def test_concurrency_override() -> None:
    assert _settings(WORKER_CONCURRENCY=5).concurrency == 5


def test_poll_order_comes_from_shared() -> None:
    settings = _settings(POOL="metadatos")

    assert settings.poll_order == POLL_ORDER[Pool.METADATA]
