"""`Broadcaster`: puente entre los hilos de fondo y el event loop de asyncio
que sirve `WS /api/ws` (ver `api/ws.py`).

`monitor/snapshot.py` llama a `publish()` desde el hilo `snapshot-loop` (ver
`main.py`), nunca desde una corrutina. `publish()` es thread-safe pero NUNCA
manda nada a un WebSocket directamente — eso violaría el modelo de
concurrencia de asyncio (los objetos `WebSocket`/`asyncio.Queue` no son
thread-safe). En cambio arma el mensaje una sola vez y agenda su entrega real
en el loop con `loop.call_soon_threadsafe(...)`; solo `_deliver`, que corre
dentro del loop, toca las colas de los clientes.

Por cliente conectado hay una `asyncio.Queue(maxsize=1)`: un cliente lento (la
pestaña del dashboard en segundo plano, una conexión con latencia alta) nunca
acumula un backlog de estados viejos. Si llega un estado nuevo antes de que el
cliente haya leído el anterior, el viejo se descarta y se deja el nuevo — el
cliente siempre termina viendo el último estado, nunca uno atrasado.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

from fastapi import WebSocket
from shared.api import StateResponse

logger = logging.getLogger(__name__)

_ENVELOPE_TYPE = "state"


def _offer(queue: asyncio.Queue[str], message: str) -> None:
    """Entrega sin bloquear nunca: si la cola (tamaño 1) está llena, tira lo
    viejo y deja lo nuevo."""
    if queue.full():
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            pass
    try:
        queue.put_nowait(message)
    except asyncio.QueueFull:
        # Esto corre siempre en el loop (un solo hilo a la vez la toca), así
        # que no debería pasar entre el `full()` de arriba y este punto; por
        # las dudas, descartamos en vez de romper la entrega a otros clientes.
        logger.debug("cola de cliente WS llena tras vaciarla, se descarta el mensaje")


class Broadcaster:
    """Registro de clientes WS conectados + último estado publicado."""

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._clients: dict[asyncio.Queue[str], WebSocket] = {}
        self._lock = threading.Lock()
        self._last_comparable: dict[str, Any] | None = None
        self.latest: str | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Se llama una vez desde el `lifespan`, con `asyncio.get_running_loop()`,
        antes de arrancar los hilos de fondo (el hilo de snapshot necesita esta
        referencia lista para poder llamar a `publish`)."""
        self._loop = loop

    def publish(self, state: StateResponse) -> None:
        """Thread-safe: pensada para llamarse desde el hilo de snapshot.

        Solo publica si el contenido cambió respecto al último envío exitoso,
        ignorando campos que se regeneran solos en cada vuelta del snapshot
        aunque nada relevante haya cambiado: `generated_at` siempre, y además
        `alerts[].ts` (una alerta que sigue activa se re-emite con un `ts`
        nuevo en cada vuelta) y `workers[].last_seen` (cambia con cada
        heartbeat de un worker vivo). Sin excluir estos dos últimos, apenas
        hay una alerta activa o un worker conectado la comparación siempre da
        "cambió" y la deduplicación queda rota en el caso real.
        """
        comparable = state.model_dump(
            mode="json",
            exclude={
                "generated_at": True,
                "alerts": {"__all__": {"ts"}},
                "workers": {"__all__": {"last_seen"}},
            },
        )
        with self._lock:
            if comparable == self._last_comparable:
                return
            self._last_comparable = comparable

        # Serializado UNA sola vez acá. Cada cliente recibe este mismo texto
        # armado, sin que nadie vuelva a parsear/reserializar por conexión.
        state_json = state.model_dump_json()
        message = f'{{"type": "{_ENVELOPE_TYPE}", "data": {state_json}}}'

        loop = self._loop
        if loop is None:
            # No debería pasar en producción (el lifespan liga el loop antes
            # de arrancar el hilo de snapshot); igual guardamos el mensaje
            # para que el próximo cliente que conecte no se quede sin nada.
            self.latest = message
            return
        loop.call_soon_threadsafe(self._deliver, message)

    def _deliver(self, message: str) -> None:
        """Corre en el loop: guarda `latest` y ofrece el mensaje a cada cliente."""
        self.latest = message
        with self._lock:
            queues = list(self._clients)
        for queue in queues:
            _offer(queue, message)

    def register(self, websocket: WebSocket) -> asyncio.Queue[str]:
        """Registra un cliente nuevo y le devuelve su cola dedicada (tamaño 1)."""
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=1)
        with self._lock:
            self._clients[queue] = websocket
        return queue

    def unregister(self, queue: asyncio.Queue[str]) -> None:
        with self._lock:
            self._clients.pop(queue, None)

    @property
    def client_count(self) -> int:
        """Para `GET /healthz`."""
        with self._lock:
            return len(self._clients)

    async def close_all(self, code: int = 1001) -> None:
        """Cierra todas las conexiones abiertas. Usado en el shutdown del lifespan."""
        with self._lock:
            websockets = list(self._clients.values())
        for websocket in websockets:
            try:
                await websocket.close(code=code)
            except Exception:
                logger.debug(
                    "no se pudo cerrar un websocket durante el shutdown", exc_info=True
                )
