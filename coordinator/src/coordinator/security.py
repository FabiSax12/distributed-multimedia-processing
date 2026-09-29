"""Middleware `X-Origin-Verify`: evita que alguien le pegue directo a la IP
elástica del coordinador saltándose CloudFront.

CloudFront (módulo `frontend`) agrega ese header con un secreto compartido a
toda request que proxea a `/api/*`; el origin (este coordinador) lo verifica.
Ver "Notas de diseño" en `infra/README.md` para el porqué del secreto (evita
un ciclo de dependencia de módulos de Terraform).
"""

from __future__ import annotations

import hmac
import logging

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

logger = logging.getLogger(__name__)

_HEADER = "X-Origin-Verify"


def verify_origin(provided: str | None, secret: str) -> bool:
    """`True` si `provided` coincide con `secret`, comparando con
    `hmac.compare_digest` (evita timing attacks). Si `secret` viene vacío
    (modo local, sin frontend desplegado) siempre es `True`.

    Extraída de `OriginVerifyMiddleware.dispatch` para que `api/ws.py` pueda
    reusar exactamente la misma validación en el handshake del WebSocket, que
    `BaseHTTPMiddleware` no llega a cubrir (ver su docstring).
    """
    if not secret:
        return True
    return hmac.compare_digest(provided or "", secret)


class OriginVerifyMiddleware(BaseHTTPMiddleware):
    """Exige `X-Origin-Verify == secret` en toda ruta bajo `/api/`.

    `/healthz` queda siempre libre (lo usa el health check de systemd/ALB, que
    no pasa por CloudFront). Si `secret` viene vacío (desarrollo local sin
    frontend desplegado) se loguea un warning una sola vez al construir el
    middleware y se deja pasar todo.
    """

    def __init__(self, app, secret: str) -> None:
        super().__init__(app)
        self._secret = secret
        if not secret:
            logger.warning(
                "ORIGIN_VERIFY_SECRET vacío: /api/* queda sin protección "
                "(modo local, no usar así en producción)"
            )

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        if not request.url.path.startswith("/api/"):
            return await call_next(request)

        provided = request.headers.get(_HEADER, "")
        if not verify_origin(provided, self._secret):
            return JSONResponse({"detail": "origin no verificado"}, status_code=403)

        return await call_next(request)
