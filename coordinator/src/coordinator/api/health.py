"""`GET /healthz`: liveness de los hilos de fondo.

Vive fuera de `/api/*` (montada en la raíz por `main.py`) para que el health
check de systemd/un balanceador le pegue directo sin pasar por
`OriginVerifyMiddleware` ni por CloudFront (ver `security.py`).
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter(tags=["health"])


@router.get("/healthz")
def healthz(request: Request) -> JSONResponse:
    """`app.state.threads` lo puebla `main.py` (nombre -> `threading.Thread`).

    `ws_clients` viene de `app.state.broadcaster.client_count` (ver
    `monitor/broadcaster.py`): cuántos clientes WS de `/api/ws` hay conectados
    ahora mismo.
    """
    threads: dict[str, object] = getattr(request.app.state, "threads", {})
    alive = {name: thread.is_alive() for name, thread in threads.items()}
    ok = all(alive.values())

    broadcaster = getattr(request.app.state, "broadcaster", None)
    ws_clients = broadcaster.client_count if broadcaster is not None else 0

    return JSONResponse(
        {"ok": ok, "threads": alive, "ws_clients": ws_clients},
        status_code=200 if ok else 503,
    )
