"""`security.OriginVerifyMiddleware`, aislado en una app mínima propia (no la
singleton de `coordinator.main.app`, cuyo middleware ya quedó armado con el
secreto que tenía `ORIGIN_VERIFY_SECRET` en el momento del primer import de
ese módulo dentro de la sesión de tests — ver docstring de `conftest.py`)."""

from __future__ import annotations

from coordinator.security import OriginVerifyMiddleware, verify_origin
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _build_app(secret: str) -> FastAPI:
    app = FastAPI()
    app.add_middleware(OriginVerifyMiddleware, secret=secret)

    @app.get("/api/ping")
    def ping() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/healthz")
    def healthz() -> dict[str, bool]:
        return {"ok": True}

    return app


def test_rejects_without_header_when_secret_set() -> None:
    client = TestClient(_build_app("s3cr3t"))
    resp = client.get("/api/ping")
    assert resp.status_code == 403


def test_accepts_with_correct_header() -> None:
    client = TestClient(_build_app("s3cr3t"))
    resp = client.get("/api/ping", headers={"X-Origin-Verify": "s3cr3t"})
    assert resp.status_code == 200


def test_healthz_is_never_protected() -> None:
    client = TestClient(_build_app("s3cr3t"))
    resp = client.get("/healthz")
    assert resp.status_code == 200


def test_empty_secret_lets_everything_through() -> None:
    client = TestClient(_build_app(""))
    resp = client.get("/api/ping")
    assert resp.status_code == 200


# `verify_origin` es la lógica que `OriginVerifyMiddleware.dispatch` usa por
# dentro, extraída para que `api/ws.py` la reutilice en el handshake del
# WebSocket (donde este `BaseHTTPMiddleware` no corre, ver su docstring).


def test_verify_origin_true_when_secret_empty() -> None:
    assert verify_origin("cualquier-cosa", "") is True
    assert verify_origin(None, "") is True


def test_verify_origin_true_when_header_matches_secret() -> None:
    assert verify_origin("s3cr3t", "s3cr3t") is True


def test_verify_origin_false_when_header_mismatches_secret() -> None:
    assert verify_origin("otro-valor", "s3cr3t") is False


def test_verify_origin_false_when_header_missing() -> None:
    assert verify_origin(None, "s3cr3t") is False
    assert verify_origin("", "s3cr3t") is False
