"""Acceso a la tabla `SubTasks` (PK `case_id`, SK `subtask_id`).

Todo el estado de una sub-tarea lo escribe el coordinador; los workers nunca
tocan esta tabla directamente, solo publican `ResultMessage` (ver
`shared/messages.py`). La condición `state_order < :so` en las escrituras de
progreso/cierre es la defensa contra el desorden/duplicación de SQS Standard
(ver `shared/states.py`).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from datetime import datetime
from typing import Any

from botocore.exceptions import ClientError

from shared.messages import ResultMessage
from shared.models import SubTaskItem, from_item, to_item

from ..aws import deserialize_item, serialize_item

logger = logging.getLogger(__name__)

_BATCH_SIZE = 25
_BATCH_RETRY_MAX = 5


def _is_conditional_check_failed(exc: ClientError) -> bool:
    return (
        exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException"
    )


class SubTasksRepo:
    def __init__(self, client: Any, table_name: str) -> None:
        self._client = client
        self._table = table_name

    def batch_put_planned(self, items: list[SubTaskItem]) -> None:
        """Escribe las sub-tareas planificadas de un caso, de a 25 (límite de BatchWriteItem).

        `UnprocessedItems` (throttling parcial del lote) se reintenta con un
        backoff simple; si después de `_BATCH_RETRY_MAX` intentos todavía
        queda algo sin escribir, lo tratamos como error real (no debería pasar
        con `PAY_PER_REQUEST` salvo un pico extremo).
        """
        for start in range(0, len(items), _BATCH_SIZE):
            chunk = items[start : start + _BATCH_SIZE]
            requests = [
                {"PutRequest": {"Item": serialize_item(to_item(item))}}
                for item in chunk
            ]
            self._write_batch_with_retry({self._table: requests})

    def _write_batch_with_retry(
        self, request_items: dict[str, list[dict[str, Any]]]
    ) -> None:
        attempt = 0
        while request_items:
            resp = self._client.batch_write_item(RequestItems=request_items)
            request_items = resp.get("UnprocessedItems") or {}
            if not request_items:
                return
            attempt += 1
            if attempt > _BATCH_RETRY_MAX:
                raise RuntimeError(
                    f"BatchWriteItem: quedaron ítems sin escribir tras {_BATCH_RETRY_MAX} reintentos"
                )
            pending = sum(len(v) for v in request_items.values())
            logger.warning(
                "BatchWriteItem dejó ítems sin procesar, reintentando",
                extra={"attempt": attempt, "pending_items": pending},
            )
            time.sleep(0.1 * (2**attempt))

    def get(self, case_id: str, subtask_id: str) -> SubTaskItem | None:
        """GetItem puntual de una sub-tarea específica, sin condición.

        Lo usa `barrier/close.py` para distinguir, cuando el
        `TransactWriteItems` del cierre falla por `ConditionalCheckFailed` del
        lado de SubTasks, un duplicado real (la sub-tarea existe, solo que ya
        tiene un `state_order` más nuevo) de una referencia inválida (no
        existe ninguna sub-tarea con ese id para ese caso), y para comparar el
        `pool` reportado por un worker contra el planificado.
        """
        resp = self._client.get_item(
            TableName=self._table,
            Key={"case_id": {"S": case_id}, "subtask_id": {"S": subtask_id}},
        )
        item = resp.get("Item")
        if item is None:
            return None
        return from_item(SubTaskItem, deserialize_item(item))

    def mark_enqueued(self, case_id: str, subtask_id: str) -> None:
        self._client.update_item(
            TableName=self._table,
            Key={"case_id": {"S": case_id}, "subtask_id": {"S": subtask_id}},
            UpdateExpression="SET enqueued = :true",
            ExpressionAttributeValues={":true": {"BOOL": True}},
        )

    def update_progress(
        self,
        case_id: str,
        subtask_id: str,
        *,
        status: str,
        state_order: int,
        attempt: int,
        worker_id: str | None,
        progress: int | None,
        started_at_if_missing: datetime | None,
    ) -> bool:
        """Actualiza un estado NO terminal (assigned/running/retrying).

        Condicionado a `state_order < :so`: si un mensaje atrasado o duplicado
        llega después de uno más nuevo, la condición falla y devolvemos
        `False` - es el caso esperado, no una excepción (SQS puede entregar
        `running` después de `retrying` del mismo intento por reordenamiento).
        `started_at` se fija una sola vez con `if_not_exists`.
        """
        names = {"#st": "status", "#so": "state_order"}
        values: dict[str, Any] = {
            ":st": {"S": status},
            ":so": {"N": str(state_order)},
            ":at": {"N": str(attempt)},
        }
        set_parts = ["#st = :st", "#so = :so", "attempt = :at"]

        if worker_id is not None:
            values[":wi"] = {"S": worker_id}
            set_parts.append("worker_id = :wi")
        if progress is not None:
            values[":pr"] = {"N": str(progress)}
            set_parts.append("progress = :pr")
        if started_at_if_missing is not None:
            values[":sa"] = {"S": started_at_if_missing.isoformat()}
            set_parts.append("started_at = if_not_exists(started_at, :sa)")

        try:
            self._client.update_item(
                TableName=self._table,
                Key={"case_id": {"S": case_id}, "subtask_id": {"S": subtask_id}},
                UpdateExpression="SET " + ", ".join(set_parts),
                ConditionExpression="attribute_exists(subtask_id) AND #so < :so",
                ExpressionAttributeNames=names,
                ExpressionAttributeValues=values,
            )
        except ClientError as exc:
            if _is_conditional_check_failed(exc):
                return False
            raise
        return True

    def build_close_update(self, result: ResultMessage) -> dict[str, Any]:
        """Arma el `Update` de SubTasks para el `TransactWriteItems` del barrier.

        Mismo criterio que `CasesRepo.build_close_update`: `barrier/close.py`
        combina ambos en una sola transacción. `#so` se usa tanto en la
        condición (`state_order` actual < nuevo) como en el SET (nuevo valor)
        porque son la misma sustitución: la condición se evalúa sobre el valor
        *antes* del update.
        """
        raw: dict[str, Any] = {
            "status": result.status.value,
            "attempt": result.attempt,
            "output_keys": result.output_keys,
            "output_meta": result.output_meta,
        }
        if result.worker_id is not None:
            raw["worker_id"] = result.worker_id
        if result.error is not None:
            raw["error"] = result.error.model_dump(mode="json")
        if result.finished_at is not None:
            raw["finished_at"] = result.finished_at.isoformat()

        serialized = serialize_item(raw)
        names = {f"#{k}": k for k in raw} | {"#so": "state_order"}
        values = {f":{k}": v for k, v in serialized.items()}
        values[":so"] = {"N": str(result.order)}

        set_expr = ", ".join(f"#{k} = :{k}" for k in raw) + ", #so = :so"

        return {
            "Update": {
                "TableName": self._table,
                "Key": {
                    "case_id": {"S": result.case_id},
                    "subtask_id": {"S": result.subtask_id},
                },
                "UpdateExpression": f"SET {set_expr}",
                "ConditionExpression": "attribute_exists(subtask_id) AND #so < :so",
                "ExpressionAttributeNames": names,
                "ExpressionAttributeValues": values,
            }
        }

    def query_by_case(
        self, case_id: str, projection: list[str] | None = None
    ) -> list[SubTaskItem]:
        """Todas las sub-tareas de un caso (Query, no Scan: usa la PK).

        Si pasás `projection`, asegurate de incluir los campos requeridos por
        `SubTaskItem` (case_id, subtask_id, input_key, priority) o
        `from_item` va a fallar al validar - `projection` es para el caso en
        que el caller sabe exactamente qué necesita, no para "traer menos y
        usarlo igual como si fuera completo".
        """
        kwargs: dict[str, Any] = {
            "TableName": self._table,
            "KeyConditionExpression": "case_id = :case_id",
            "ExpressionAttributeValues": {":case_id": {"S": case_id}},
        }
        if projection:
            names = {f"#p{i}": field for i, field in enumerate(projection)}
            kwargs["ProjectionExpression"] = ", ".join(names)
            kwargs["ExpressionAttributeNames"] = names

        items: list[SubTaskItem] = []
        while True:
            resp = self._client.query(**kwargs)
            items.extend(
                from_item(SubTaskItem, deserialize_item(item))
                for item in resp.get("Items", [])
            )
            last_key = resp.get("LastEvaluatedKey")
            if not last_key:
                return items
            kwargs["ExclusiveStartKey"] = last_key

    def scan_orphan_enqueue_candidates(
        self, older_than: datetime
    ) -> Iterator[SubTaskItem]:
        """Sub-tareas `pending`, no encoladas todavía, creadas antes de `older_than`.

        Esto es lo que reencola el sweeper (`barrier/sweeper.py`) cuando un
        `SendMessageBatch` falló parcialmente y dejó una sub-tarea con
        `enqueued=false` colgada. Scan con FilterExpression: en producción
        sería un GSI por `(status, enqueued)` para no barrer toda la tabla.
        """
        kwargs: dict[str, Any] = {
            "TableName": self._table,
            "FilterExpression": (
                "#st = :pending AND enqueued = :false AND created_at < :older_than"
            ),
            "ExpressionAttributeNames": {"#st": "status"},
            "ExpressionAttributeValues": {
                ":pending": {"S": "pending"},
                ":false": {"BOOL": False},
                ":older_than": {"S": older_than.isoformat()},
            },
        }
        while True:
            resp = self._client.scan(**kwargs)
            for item in resp.get("Items", []):
                yield from_item(SubTaskItem, deserialize_item(item))
            last_key = resp.get("LastEvaluatedKey")
            if not last_key:
                return
            kwargs["ExclusiveStartKey"] = last_key
