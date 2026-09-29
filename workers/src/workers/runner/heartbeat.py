"""Heartbeat del worker en la tabla `Workers`.

Es la única escritura del worker en DynamoDB (IAM solo le da PutItem/
UpdateItem sobre `Workers`). Cada `HEARTBEAT_INTERVAL_S` reemplaza su ítem
con CPU y memoria de la máquina (psutil) y los `subtask_id` en curso; el
coordinador lo lee en `monitor/snapshot.py` y lo marca caído si pasa
`WORKER_DEAD_AFTER_S` sin reportar.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psutil
from boto3.dynamodb.types import TypeSerializer

from shared.messages import utcnow
from shared.models import HEARTBEAT_INTERVAL_S, WorkerItem, to_item
from shared.routing import Pool

logger = logging.getLogger(__name__)

_serializer = TypeSerializer()


class Heartbeat:
    def __init__(
        self,
        dynamodb: Any,
        table: str,
        *,
        worker_id: str,
        pool: Pool,
        instance_type: str,
        hostname: str,
        concurrency: int,
    ) -> None:
        self._dynamodb = dynamodb
        self._table = table
        self._worker_id = worker_id
        self._pool = pool
        self._instance_type = instance_type
        self._hostname = hostname
        self._concurrency = concurrency
        self._started_at = utcnow()
        self._active: set[str] = set()
        self._lock = threading.Lock()
        # La primera llamada a cpu_percent(None) siempre da 0.0: la
        # descartamos acá para que el primer heartbeat ya traiga un valor real.
        psutil.cpu_percent(interval=None)

    @contextmanager
    def track(self, subtask_id: str) -> Iterator[None]:
        """Marca la sub-tarea como en curso mientras dure el bloque."""
        with self._lock:
            self._active.add(subtask_id)
        try:
            yield
        finally:
            with self._lock:
                self._active.discard(subtask_id)

    def beat(self) -> None:
        with self._lock:
            active = sorted(self._active)
        item = WorkerItem(
            worker_id=self._worker_id,
            pool=self._pool,
            instance_type=self._instance_type,
            hostname=self._hostname,
            concurrency=self._concurrency,
            cpu_percent=psutil.cpu_percent(interval=None),
            mem_percent=psutil.virtual_memory().percent,
            active_subtasks=active,
            started_at=self._started_at,
            last_seen=utcnow(),
        )
        self._dynamodb.put_item(
            TableName=self._table,
            Item={k: _serializer.serialize(v) for k, v in to_item(item).items()},
        )

    def run(
        self, stop_event: threading.Event, interval: float = HEARTBEAT_INTERVAL_S
    ) -> None:
        """Loop del hilo de heartbeat. Un beat fallido se loguea y se reintenta
        en el próximo ciclo: si el hilo muriera, el panel daría por caído a un
        worker que sigue procesando."""
        while not stop_event.is_set():
            try:
                self.beat()
            except Exception:
                logger.exception("heartbeat falló, se reintenta en el próximo ciclo")
            stop_event.wait(interval)
