"""`WS /api/ws` (`api/ws.py`) + `monitor/broadcaster.Broadcaster`.

App mínima propia (mismo patrón que `test_security.py`): no usamos la
`coordinator.main.app` real, que arranca hilos de fondo contra AWS. Acá solo
armamos una FastAPI mínima con el router de WS y un `Broadcaster` propio,
inyectando `Settings` vía `app.dependency_overrides` en vez de variables de
entorno reales.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import threading
from collections.abc import AsyncIterator
from datetime import timedelta
from types import SimpleNamespace

import pytest
from coordinator.api import ws as ws_router
from coordinator.config import get_settings
from coordinator.monitor.broadcaster import Broadcaster
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from shared.api import Alert, CaseSummary, QueueDepth, StateResponse, WorkerView
from shared.messages import utcnow
from shared.models import CaseItem
from shared.routing import Pool
from starlette.websockets import WebSocketDisconnect

_HEADER = "X-Origin-Verify"


def _settings(
    *, secret: str = "", ping_interval_s: float = 9999.0, max_clients: int = 20
) -> SimpleNamespace:
    return SimpleNamespace(
        ORIGIN_VERIFY_SECRET=secret,
        WS_PING_INTERVAL_S=ping_interval_s,
        WS_MAX_CLIENTS=max_clients,
    )


def _build_app(settings: SimpleNamespace) -> tuple[FastAPI, Broadcaster]:
    broadcaster = Broadcaster()

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        broadcaster.bind_loop(asyncio.get_running_loop())
        app.state.broadcaster = broadcaster
        yield

    app = FastAPI(lifespan=lifespan)
    app.include_router(ws_router.router, prefix="/api")
    app.dependency_overrides[get_settings] = lambda: settings
    return app, broadcaster


def _state(case_id: str = "c1") -> StateResponse:
    return StateResponse(
        cases=[
            CaseSummary(
                case=CaseItem(case_id=case_id, file_count=1, total=1, pending_count=1),
                by_status={"pending": 1},
            )
        ],
        workers=[],
        queues=[QueueDepth(queue="video-alta", visible=0, in_flight=0)],
        alerts=[],
    )


def _case_id_of(message: dict) -> str:
    return message["data"]["cases"][0]["case"]["case_id"]


# ---- Autenticación en el handshake ----------------------------------------- #


def test_rejects_connection_without_header_when_secret_configured() -> None:
    app, _broadcaster = _build_app(_settings(secret="s3cr3t"))
    with (
        TestClient(app) as client,
        pytest.raises(WebSocketDisconnect) as exc_info,
        client.websocket_connect("/api/ws"),
    ):
        pass
    assert exc_info.value.code == 1008


def test_rejects_connection_with_wrong_header_value_when_secret_configured() -> None:
    # Header presente pero con un valor que no matchea el secreto: debe
    # rechazarse igual que la ausencia de header (código 1008), no confundirse
    # con un caso "autenticado".
    app, _broadcaster = _build_app(_settings(secret="s3cr3t"))
    with (
        TestClient(app) as client,
        pytest.raises(WebSocketDisconnect) as exc_info,
        client.websocket_connect("/api/ws", headers={_HEADER: "valor-incorrecto"}),
    ):
        pass
    assert exc_info.value.code == 1008


def test_accepts_connection_with_correct_header_when_secret_configured() -> None:
    app, broadcaster = _build_app(_settings(secret="s3cr3t"))
    with (
        TestClient(app) as client,
        client.websocket_connect("/api/ws", headers={_HEADER: "s3cr3t"}),
    ):
        assert broadcaster.client_count == 1


def test_empty_secret_lets_connection_through() -> None:
    app, broadcaster = _build_app(_settings(secret=""))
    with TestClient(app) as client, client.websocket_connect("/api/ws"):
        assert broadcaster.client_count == 1


def test_exceeding_max_clients_is_rejected_with_1013() -> None:
    app, broadcaster = _build_app(_settings(secret="", max_clients=1))
    with TestClient(app) as client, client.websocket_connect("/api/ws"):
        assert broadcaster.client_count == 1
        with (
            pytest.raises(WebSocketDisconnect) as exc_info,
            client.websocket_connect("/api/ws"),
        ):
            pass
        assert exc_info.value.code == 1013


def test_rejected_connection_never_calls_accept_and_never_exceeds_max_clients(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regresión del TOCTOU: el chequeo de `WS_MAX_CLIENTS` y la reserva del
    cupo (`broadcaster.register`) no deben tener ningún `await` en el medio.
    Lo verificamos indirectamente: la conexión que excede el límite tiene que
    rechazarse ANTES de llamar `websocket.accept()`, y el conteo de clientes
    nunca debe superar el máximo, ni siquiera momentáneamente."""
    accept_calls: list[WebSocket] = []
    original_accept = WebSocket.accept

    async def _tracking_accept(
        self: WebSocket, *args: object, **kwargs: object
    ) -> None:
        accept_calls.append(self)
        await original_accept(self, *args, **kwargs)

    monkeypatch.setattr(WebSocket, "accept", _tracking_accept)

    app, broadcaster = _build_app(_settings(secret="", max_clients=1))
    with TestClient(app) as client, client.websocket_connect("/api/ws"):
        assert broadcaster.client_count == 1
        assert len(accept_calls) == 1

        with (
            pytest.raises(WebSocketDisconnect) as exc_info,
            client.websocket_connect("/api/ws"),
        ):
            pass

        assert exc_info.value.code == 1013
        # La conexión rechazada nunca llegó a `accept()`.
        assert len(accept_calls) == 1
        # El límite nunca se superó, ni siquiera momentáneamente.
        assert broadcaster.client_count == 1


# ---- Estado inicial al conectar ---------------------------------------------- #


def test_new_client_receives_last_published_state_immediately() -> None:
    app, broadcaster = _build_app(_settings(secret=""))
    with TestClient(app) as client:
        with client.websocket_connect("/api/ws") as warmup_ws:
            broadcaster.publish(_state("c1"))
            warmup_ws.receive_json()  # aseguramos que `latest` ya quedó seteado

        with client.websocket_connect("/api/ws") as ws:
            message = ws.receive_json()
            assert message["type"] == "state"
            assert _case_id_of(message) == "c1"


def test_new_client_gets_nothing_immediately_when_no_state_published_yet() -> None:
    app, _broadcaster = _build_app(_settings(secret="", ping_interval_s=0.05))
    with TestClient(app) as client, client.websocket_connect("/api/ws") as ws:
        # Sin estado previo, lo primero que le llega es el próximo ping,
        # no un mensaje de tipo "state".
        message = ws.receive_json()
        assert message == {"type": "ping"}


# ---- Puente hilo -> loop ------------------------------------------------------ #


def test_publish_from_real_thread_reaches_two_connected_clients() -> None:
    app, broadcaster = _build_app(_settings(secret=""))
    state = _state("c1")

    with (
        TestClient(app) as client,
        client.websocket_connect("/api/ws") as ws1,
        client.websocket_connect("/api/ws") as ws2,
    ):
        thread = threading.Thread(target=broadcaster.publish, args=(state,))
        thread.start()
        thread.join()

        msg1 = ws1.receive_json()
        msg2 = ws2.receive_json()

    assert msg1["type"] == "state"
    assert msg2["type"] == "state"
    assert _case_id_of(msg1) == "c1"
    assert _case_id_of(msg2) == "c1"


# ---- Deduplicación ignorando generated_at -------------------------------------- #


def test_publishing_same_content_with_different_generated_at_sends_once() -> None:
    app, broadcaster = _build_app(_settings(secret=""))

    state1 = _state("c1")
    state_same_content = state1.model_copy(
        update={"generated_at": state1.generated_at + timedelta(seconds=5)}
    )

    with TestClient(app) as client, client.websocket_connect("/api/ws") as ws:
        broadcaster.publish(state1)
        first = ws.receive_json()
        assert _case_id_of(first) == "c1"

        # Mismo contenido salvo `generated_at`: no debe generar un envío.
        broadcaster.publish(state_same_content)

        # Confirmamos el descarte a nivel del broadcaster (sin más
        # publish de por medio, que enmascararía el resultado via el
        # "se queda con lo último" de la cola tamaño 1).
        assert broadcaster.latest is not None
        assert first == json.loads(broadcaster.latest)


def test_publishing_same_content_with_live_worker_and_alert_sends_once() -> None:
    """Regresión: con un `WorkerView` y una `Alert` en el estado, `last_seen`
    y `ts` se regeneran en cada vuelta del snapshot aunque nada relevante haya
    cambiado (el worker sigue vivo, la alerta sigue activa). Si la
    deduplicación no ignora esos dos campos (además de `generated_at`), esto
    dispara un mensaje por vuelta aunque el contenido "real" sea idéntico."""
    app, broadcaster = _build_app(_settings(secret=""))

    worker = WorkerView(
        worker_id="video-i-0abc123-4711",
        pool=Pool.VIDEO,
        instance_type="c7i-flex.large",
        hostname="host1",
        concurrency=2,
        started_at=utcnow(),
        last_seen=utcnow(),
        alive=True,
    )
    alert = Alert(level="warning", pool="video", message="cola alta")

    state1 = _state("c1").model_copy(update={"workers": [worker], "alerts": [alert]})

    state2 = state1.model_copy(
        update={
            "generated_at": state1.generated_at + timedelta(seconds=5),
            "workers": [
                worker.model_copy(
                    update={"last_seen": worker.last_seen + timedelta(seconds=5)}
                )
            ],
            "alerts": [
                alert.model_copy(update={"ts": alert.ts + timedelta(seconds=5)})
            ],
        }
    )

    # Contamos las entregas reales (`_deliver`) en vez de fijarnos solo en
    # `broadcaster.latest`: como `publish()` agenda la entrega con
    # `call_soon_threadsafe`, comparar `latest` justo después de publicar sin
    # forzar al loop a procesar la cola de callbacks pendientes es una carrera
    # que puede dar un falso verde aunque la deduplicación esté rota (el
    # `_deliver` de `state2` simplemente no llegó a correr todavía). Envolver
    # `_deliver` y forzar una entrega de "marcador" después nos da un punto de
    # sincronización determinístico: para cuando el cliente recibe el
    # marcador, cualquier `_deliver` agendado antes (el de `state2`, si la
    # deduplicación falló) ya corrió, porque `call_soon_threadsafe` preserva
    # el orden FIFO de los callbacks en el loop.
    delivered: list[str] = []
    original_deliver = broadcaster._deliver

    def _tracking_deliver(message: str) -> None:
        delivered.append(message)
        original_deliver(message)

    broadcaster._deliver = _tracking_deliver

    with TestClient(app) as client, client.websocket_connect("/api/ws") as ws:
        broadcaster.publish(state1)
        first = ws.receive_json()
        assert _case_id_of(first) == "c1"

        # Mismo contenido "real", solo cambiaron `generated_at`/`last_seen`/`ts`:
        # no debe generar un envío.
        broadcaster.publish(state2)

        broadcaster.publish(_state("marker"))
        assert _case_id_of(ws.receive_json()) == "marker"

    # Solo debieron correr dos entregas reales: la de `state1` y la del
    # marcador. Si la deduplicación no ignora `last_seen`/`ts`, `state2`
    # dispara una tercera.
    assert len(delivered) == 2


# ---- Cliente lento no bloquea a los demás -------------------------------------- #
#
# `TestClient` transporta los WS por un stream en memoria SIN límite de
# tamaño: el `_send_loop` interno de una conexión siempre puede drenar su
# cola apenas llega un mensaje, aunque el test nunca haya leído nada todavía
# (la "lentitud" real pasa en el socket, no en nuestra cola). Para probar el
# contrato de la cola tamaño 1 (que es lo que protege a un cliente
# genuinamente lento — uno cuyo `websocket.send_text` está bloqueado
# esperando que el otro lado drene el socket) hay que ejercitarlo a nivel del
# `Broadcaster`: un cliente registrado cuya cola nunca se lee.


class _FakeWebSocket:
    async def close(self, code: int = 1000) -> None:
        return None


def test_slow_client_queue_never_accumulates_backlog() -> None:
    app, broadcaster = _build_app(_settings(secret=""))

    with TestClient(app) as client:
        # Cliente "lento": registrado en el broadcaster, pero nada consume su
        # cola (a diferencia de una conexión real de `websocket_connect`, que
        # arranca su propio `_send_loop`).
        slow_queue = broadcaster.register(_FakeWebSocket())

        with client.websocket_connect("/api/ws") as fast_ws:
            broadcaster.publish(_state("c1"))
            assert _case_id_of(fast_ws.receive_json()) == "c1"

            # Dos publicaciones más sin que nadie lea la cola del lento.
            broadcaster.publish(_state("c2"))
            broadcaster.publish(_state("c3"))

            # El rápido las sigue recibiendo sin ningún bloqueo. `_send_loop`
            # corre en paralelo a estos `publish`, así que c2 puede llegar
            # antes de que c3 se haya publicado: hay que leer hasta ver c3,
            # no asumir que el próximo mensaje ya es el último.
            last_case_id = None
            for _ in range(3):
                last_case_id = _case_id_of(fast_ws.receive_json())
                if last_case_id == "c3":
                    break
            assert last_case_id == "c3"

        # El lento nunca acumuló backlog: su cola (tamaño 1) tiene un único
        # elemento, y es el último estado publicado, no uno atrasado.
        assert slow_queue.qsize() == 1
        message = json.loads(slow_queue.get_nowait())
        assert _case_id_of(message) == "c3"


# ---- Conteo de clientes ------------------------------------------------------- #


def test_client_count_increases_and_decreases_with_connection_lifecycle() -> None:
    app, broadcaster = _build_app(_settings(secret=""))
    with TestClient(app) as client:
        assert broadcaster.client_count == 0
        with client.websocket_connect("/api/ws"):
            assert broadcaster.client_count == 1
        assert broadcaster.client_count == 0


# ---- Excepciones inesperadas en las tareas de la conexión ---------------------- #


def test_unexpected_exception_in_connection_task_is_logged_and_still_cleans_up(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Si `_send_loop`/`_ping_loop`/`_recv_loop` levanta una excepción real (no
    una cancelación ni una desconexión normal del cliente), tiene que quedar
    logueada — hoy `contextlib.suppress(asyncio.CancelledError, Exception)` la
    traga en silencio. La limpieza (desregistrar al cliente) debe seguir
    ocurriendo igual."""

    async def _boom_ping_loop(websocket: WebSocket, interval_s: float) -> None:
        raise ValueError("fallo inesperado simulado")

    monkeypatch.setattr(ws_router, "_ping_loop", _boom_ping_loop)

    app, broadcaster = _build_app(_settings(secret=""))

    with (
        caplog.at_level(logging.ERROR, logger="coordinator.api.ws"),
        TestClient(app) as client,
        client.websocket_connect("/api/ws"),
    ):
        pass

    assert "fallo inesperado simulado" in caplog.text
    # La limpieza normal (desregistrar la cola) tiene que seguir pasando.
    assert broadcaster.client_count == 0


def test_normal_disconnect_is_not_logged_as_an_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Contraparte del test anterior: un cierre normal del cliente (lo que
    dispara `WebSocketDisconnect` en `_recv_loop`) no debe generar ningún log
    de error — solo las excepciones inesperadas ameritan ruido."""
    app, broadcaster = _build_app(_settings(secret=""))

    with (
        caplog.at_level(logging.ERROR, logger="coordinator.api.ws"),
        TestClient(app) as client,
        client.websocket_connect("/api/ws"),
    ):
        pass

    assert caplog.text == ""
    assert broadcaster.client_count == 0


# ---- Shutdown: `close_all` con clientes conectados ------------------------------ #


def test_close_all_closes_connected_client_and_resets_client_count() -> None:
    """El `lifespan` real llama `broadcaster.close_all(...)` al apagar el
    coordinador (ver `main.py`). Los tests existentes usan un `lifespan` de
    prueba vacío que nunca ejercita esa ruta; acá armamos un `Broadcaster`
    real, conectamos un cliente, y llamamos `close_all` directamente."""
    app, broadcaster = _build_app(_settings(secret=""))

    with TestClient(app) as client:
        with client.websocket_connect("/api/ws") as ws:
            assert broadcaster.client_count == 1

            # `close_all` es una corrutina que debe correr en el loop de la
            # app (el mismo que `bind_loop` guardó); la agendamos desde este
            # hilo de test tal como lo haría el `lifespan` real al apagarse.
            future = asyncio.run_coroutine_threadsafe(
                broadcaster.close_all(code=1001), broadcaster._loop
            )
            future.result(timeout=5)

            with pytest.raises(WebSocketDisconnect) as exc_info:
                ws.receive_text()
            assert exc_info.value.code == 1001

        assert broadcaster.client_count == 0
