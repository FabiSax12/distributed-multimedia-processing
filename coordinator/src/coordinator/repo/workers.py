"""Acceso a la tabla `Workers` (PK `worker_id`). Cada worker escribe su propio
heartbeat directamente (fuera del alcance de esta tanda); el coordinador solo
lee para `monitor/` (tanda 2) y para la vista `/api/state`.
"""

from __future__ import annotations

from typing import Any

from shared.models import WorkerItem, from_item

from ..aws import deserialize_item


class WorkersRepo:
    def __init__(self, client: Any, table_name: str) -> None:
        self._client = client
        self._table = table_name

    def scan_all(self) -> list[WorkerItem]:
        """Todos los workers registrados (vivos o no; `WorkerItem.is_alive()` decide).

        La tabla es chica (un ítem por instancia EC2 de worker), así que un
        Scan sin paginar es más que suficiente.
        """
        items: list[WorkerItem] = []
        kwargs: dict[str, Any] = {"TableName": self._table}
        while True:
            resp = self._client.scan(**kwargs)
            items.extend(
                from_item(WorkerItem, deserialize_item(item))
                for item in resp.get("Items", [])
            )
            last_key = resp.get("LastEvaluatedKey")
            if not last_key:
                return items
            kwargs["ExclusiveStartKey"] = last_key
