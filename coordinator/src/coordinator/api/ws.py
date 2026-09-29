"""`WS /api/ws`: empuja el `StateResponse` en vivo (calculado por
`monitor/snapshot.py` y repartido por `monitor/broadcaster.Broadcaster`) en
vez de que el dashboard tenga que hacer polling de `GET /api/state`. El
polling queda como respaldo del lado del cliente si el socket se cae.

Autenticación en el handshake: `OriginVerifyMiddleware` (`security.py`) es un
`BaseHTTPMiddleware`, y Starlette NO lo corre sobre conexiones WebSocket (deja
pasar cualquier scope que no sea `http` sin tocarlo). Por eso acá revalidamos
`X-Origin-Verify` a mano, ANTES de aceptar la conexión (`accept()`), con
`security.verify_origin` — la misma comparación con `hmac.compare_digest` y el
mismo criterio de "secreto vacío = modo local" que ya usa el middleware HTTP.

Formato de mensajes (contrato con el dashboard), dos tipos por el campo
`type`:

    {"type": "state", "data": <StateResponse serializado>}
    {"type": "ping"}

El sobre de `"state"` ya viene armado en `Broadcaster.publish`; acá nunca se
vuelve a parsear/reserializar, solo se reenvía tal cual a cada cliente.
"""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket
from starlette.websockets import WebSocketDisconnect

from ..security import verify_origin
from .deps import SettingsDep

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ws"])

_HEADER = "X-Origin-Verify"
_CLOSE_UNAUTHORIZED = 1008
_CLOSE_TOO_MANY_CLIENTS = 1013
_PING_MESSAGE = json.dumps({"type": "ping"})


@router.websocket("/ws")
async def ws_state(websocket: WebSocket, settings: SettingsDep) -> None:
    provided = websocket.headers.get(_HEADER, "")
    if not verify_origin(provided, settings.ORIGIN_VERIFY_SECRET):
        await websocket.close(code=_CLOSE_UNAUTHORIZED)
        return

    broadcaster = websocket.app.state.broadcaster
    if broadcaster.client_count >= settings.WS_MAX_CLIENTS:
        await websocket.close(code=_CLOSE_TOO_MANY_CLIENTS)
        return

    # El cupo se reserva ACÁ, antes de `accept()`, no después: entre el
    # chequeo de arriba y este `register` no hay ningún `await` de por medio,
    # así que el par chequeo-y-reserva es atómico respecto a otras conexiones
    # entrantes (que solo pueden intercalarse en el `await` de `accept()` de
    # abajo, ya con el cupo de esta conexión reservado). Si quedara
    # `register()` después de `accept()` como antes, varias conexiones
    # concurrentes podrían pasar el chequeo antes de que cualquiera llegue a
    # registrarse, superando `WS_MAX_CLIENTS`.
    queue = broadcaster.register(websocket)
    try:
        await websocket.accept()
        if broadcaster.latest is not None:
            await websocket.send_text(broadcaster.latest)

        send_task = asyncio.create_task(_send_loop(websocket, queue))
        ping_task = asyncio.create_task(
            _ping_loop(websocket, settings.WS_PING_INTERVAL_S)
        )
        recv_task = asyncio.create_task(_recv_loop(websocket))
        pending = {send_task, ping_task, recv_task}

        try:
            await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in pending:
                task.cancel()
            for task in pending:
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    if not _is_expected_disconnect(task.exception()):
                        logger.exception(
                            "una tarea de la conexión WS terminó con un error inesperado"
                        )
    finally:
        broadcaster.unregister(queue)


def _is_expected_disconnect(exc: BaseException | None) -> bool:
    """`True` si `exc` es el resultado esperable de que el cliente haya
    cortado la conexión (un `WebSocketDisconnect`, o el `RuntimeError` que
    tira Starlette al intentar mandar por un socket que ya se cerró) — no una
    falla real que valga la pena loguear."""
    if exc is None:
        return False
    if isinstance(exc, WebSocketDisconnect):
        return True
    return isinstance(exc, RuntimeError) and "close message" in str(exc)


async def _send_loop(websocket: WebSocket, queue: asyncio.Queue[str]) -> None:
    """Reenvía tal cual lo que salga de la cola propia del cliente."""
    while True:
        message = await queue.get()
        await websocket.send_text(message)


async def _ping_loop(websocket: WebSocket, interval_s: float) -> None:
    """CloudFront corta conexiones inactivas; este ping de aplicación evita
    eso y de paso sirve para detectar clientes muertos."""
    while True:
        await asyncio.sleep(interval_s)
        await websocket.send_text(_PING_MESSAGE)


async def _recv_loop(websocket: WebSocket) -> None:
    """El dashboard no manda nada; esto solo existe para detectar
    `WebSocketDisconnect` cuando el cliente cierra."""
    while True:
        await websocket.receive_text()
