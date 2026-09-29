"""Acceso a la tabla `Cases` (PK `case_id`).

Todas las escrituras que tocan el barrier (`pending_count`, `closed`, conteo de
fallidas/canceladas) son condicionales para que dos hilos/mensajes duplicados
nunca se pisen. Ver `barrier/close.py` y `barrier/finalize.py` para el porqué
de cada condición.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from typing import Any

from botocore.exceptions import ClientError

from shared.messages import utcnow
from shared.models import CaseItem, from_item, to_item
from shared.states import TERMINAL_CASE, CaseStatus

from ..aws import deserialize_item, serialize_item

# Placeholders reutilizables para "status IN (<estados terminales>)".
_TERMINAL_VALUES: dict[str, dict[str, str]] = {
    f":term{i}": {"S": status.value} for i, status in enumerate(TERMINAL_CASE)
}
_TERMINAL_IN = ", ".join(_TERMINAL_VALUES)


class CaseNotFound(Exception):
    """No existe un caso con ese `case_id`."""


class CaseAlreadyTerminal(Exception):
    """El caso ya llegó a un estado terminal; la operación pedida ya no aplica."""


def _is_conditional_check_failed(exc: ClientError) -> bool:
    return (
        exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException"
    )


class CasesRepo:
    def __init__(self, client: Any, table_name: str) -> None:
        self._client = client
        self._table = table_name

    def put_new(self, case: CaseItem) -> None:
        """PutItem condicionado a que el caso no exista.

        No atrapamos `ConditionalCheckFailedException`: se propaga tal cual
        (como `botocore.exceptions.ClientError`) para que quien llame decida
        qué significa un `case_id` duplicado (no debería pasar, son ULID).
        """
        self._client.put_item(
            TableName=self._table,
            Item=serialize_item(to_item(case)),
            ConditionExpression="attribute_not_exists(case_id)",
        )

    def get(self, case_id: str, consistent: bool = True) -> CaseItem | None:
        resp = self._client.get_item(
            TableName=self._table,
            Key={"case_id": {"S": case_id}},
            ConsistentRead=consistent,
        )
        item = resp.get("Item")
        if item is None:
            return None
        return from_item(CaseItem, deserialize_item(item))

    def update_cancel_requested(self, case_id: str) -> CaseItem:
        """Marca `cancel_requested = true`. 404 si no existe, 409 si ya es terminal.

        Hacemos una lectura previa para poder distinguir "no existe" de "ya
        terminal" con un mensaje claro (la API los traduce a 404/409), y
        además protegemos la escritura con la misma condición: si el caso se
        volvió terminal justo entre la lectura y el update, el
        `ConditionalCheckFailedException` también se traduce a
        `CaseAlreadyTerminal` (la condición es la fuente de verdad, la
        lectura previa es solo para dar un mejor mensaje en el caso común).
        """
        existing = self.get(case_id, consistent=True)
        if existing is None:
            raise CaseNotFound(case_id)
        if existing.status in TERMINAL_CASE:
            raise CaseAlreadyTerminal(case_id)

        try:
            resp = self._client.update_item(
                TableName=self._table,
                Key={"case_id": {"S": case_id}},
                UpdateExpression="SET cancel_requested = :true",
                ConditionExpression=(
                    f"attribute_exists(case_id) AND NOT (#st IN ({_TERMINAL_IN}))"
                ),
                ExpressionAttributeNames={"#st": "status"},
                ExpressionAttributeValues={":true": {"BOOL": True}, **_TERMINAL_VALUES},
                ReturnValues="ALL_NEW",
            )
        except ClientError as exc:
            if _is_conditional_check_failed(exc):
                raise CaseAlreadyTerminal(case_id) from exc
            raise
        return from_item(CaseItem, deserialize_item(resp["Attributes"]))

    def build_close_update(
        self, case_id: str, subtask_id: str, *, failed: bool, cancelled: bool
    ) -> dict[str, Any]:
        """Arma el `Update` de Cases para el `TransactWriteItems` del barrier.

        `barrier/close.py` combina esto con `SubTasksRepo.build_close_update`
        en una sola transacción: las dos tablas se actualizan atómicamente o
        ninguna. `ADD closed :idSet` es un string-set add: idempotente (agregar
        el mismo `subtask_id` dos veces no cambia nada), y la condición
        `NOT contains(closed, :id)` es lo que hace que un resultado duplicado
        (el mismo `subtask_id` ya cerrado antes) falle la transacción en vez
        de restar `pending_count` dos veces.
        """
        return {
            "Update": {
                "TableName": self._table,
                "Key": {"case_id": {"S": case_id}},
                "UpdateExpression": (
                    "ADD pending_count :minus_one, failed_count :f, "
                    "cancelled_count :c, closed :id_set"
                ),
                "ConditionExpression": "attribute_exists(case_id) AND NOT contains(closed, :id)",
                "ExpressionAttributeValues": {
                    ":minus_one": {"N": "-1"},
                    ":f": {"N": "1" if failed else "0"},
                    ":c": {"N": "1" if cancelled else "0"},
                    ":id_set": {"SS": [subtask_id]},
                    ":id": {"S": subtask_id},
                },
            }
        }

    def finalize_write(
        self, case_id: str, status: CaseStatus, finished_at: datetime, report_key: str
    ) -> bool:
        """UpdateItem condicionado a que el caso no sea ya terminal.

        Devuelve `False` (no es un error) si la condición falló: significa que
        otra llamada concurrente a `finalize()` ya cerró el caso primero -
        `barrier/finalize.py` es idempotente a propósito para que el sweeper y
        el barrier normal puedan pisarse sin problema.
        """
        values = serialize_item(
            {
                "status": status.value,
                "finished_at": finished_at.isoformat(),
                "report_key": report_key,
            }
        )
        try:
            self._client.update_item(
                TableName=self._table,
                Key={"case_id": {"S": case_id}},
                UpdateExpression="SET #st = :status, finished_at = :finished_at, report_key = :report_key",
                ConditionExpression=f"attribute_exists(case_id) AND NOT (#st IN ({_TERMINAL_IN}))",
                ExpressionAttributeNames={"#st": "status"},
                ExpressionAttributeValues={
                    ":status": values["status"],
                    ":finished_at": values["finished_at"],
                    ":report_key": values["report_key"],
                    **_TERMINAL_VALUES,
                },
            )
        except ClientError as exc:
            if _is_conditional_check_failed(exc):
                return False
            raise
        return True

    def mark_processing_if_needed(self, case_id: str) -> None:
        """El caso pasa a `processing` la primera vez que una sub-tarea arranca.

        Solo aplica si el caso está `queued` o `retrying`; si ya está en
        `processing` (otro resultado ya lo puso) o es terminal, la condición
        falla y no hacemos nada - es una carrera esperada, no un error.
        `started_at` se fija una sola vez con `if_not_exists`.
        """
        try:
            self._client.update_item(
                TableName=self._table,
                Key={"case_id": {"S": case_id}},
                UpdateExpression=(
                    "SET #st = :processing, "
                    "started_at = if_not_exists(started_at, :now)"
                ),
                ConditionExpression="#st IN (:queued, :retrying)",
                ExpressionAttributeNames={"#st": "status"},
                ExpressionAttributeValues={
                    ":processing": {"S": CaseStatus.PROCESSING.value},
                    ":queued": {"S": CaseStatus.QUEUED.value},
                    ":retrying": {"S": CaseStatus.RETRYING.value},
                    ":now": {"S": utcnow().isoformat()},
                },
            )
        except ClientError as exc:
            if _is_conditional_check_failed(exc):
                return
            raise

    def mark_retrying_if_not_terminal(self, case_id: str) -> None:
        """El caso pasa a `retrying` mientras no sea terminal (misma lógica que arriba)."""
        try:
            self._client.update_item(
                TableName=self._table,
                Key={"case_id": {"S": case_id}},
                UpdateExpression="SET #st = :retrying",
                ConditionExpression=f"attribute_exists(case_id) AND NOT (#st IN ({_TERMINAL_IN}))",
                ExpressionAttributeNames={"#st": "status"},
                ExpressionAttributeValues={
                    ":retrying": {"S": CaseStatus.RETRYING.value},
                    **_TERMINAL_VALUES,
                },
            )
        except ClientError as exc:
            if _is_conditional_check_failed(exc):
                return
            raise

    def scan_non_terminal(self) -> Iterator[CaseItem]:
        """Scan paginado de casos que no llegaron a un estado terminal.

        A la escala de cientos de casos un Scan+filtro es aceptable; en
        producción esto sería un GSI por `status` (Scan es O(tabla completa)
        incluso si filtra después).
        """
        kwargs: dict[str, Any] = {
            "TableName": self._table,
            "FilterExpression": f"NOT (#st IN ({_TERMINAL_IN}))",
            "ExpressionAttributeNames": {"#st": "status"},
            "ExpressionAttributeValues": _TERMINAL_VALUES,
        }
        while True:
            resp = self._client.scan(**kwargs)
            for item in resp.get("Items", []):
                yield from_item(CaseItem, deserialize_item(item))
            last_key = resp.get("LastEvaluatedKey")
            if not last_key:
                return
            kwargs["ExclusiveStartKey"] = last_key

    def list_recent(self, status: str | None, limit: int) -> list[CaseItem]:
        """Casos más recientes, opcionalmente filtrados por `status`.

        Sin un GSI por fecha, hacemos Scan + orden en memoria por `case_id`:
        el `case_id` es un ULID (tiempo + aleatorio, ordenable lexicográficamente
        por creación), así que ordenar strings de mayor a menor da los más
        recientes primero. A la escala de este proyecto (cientos de casos) un
        Scan completo es aceptable; con miles convendría un GSI
        `status -> created_at`.
        """
        kwargs: dict[str, Any] = {"TableName": self._table}
        if status is not None:
            kwargs["FilterExpression"] = "#st = :status"
            kwargs["ExpressionAttributeNames"] = {"#st": "status"}
            kwargs["ExpressionAttributeValues"] = {":status": {"S": status}}

        items: list[CaseItem] = []
        while True:
            resp = self._client.scan(**kwargs)
            items.extend(
                from_item(CaseItem, deserialize_item(item))
                for item in resp.get("Items", [])
            )
            last_key = resp.get("LastEvaluatedKey")
            if not last_key:
                break
            kwargs["ExclusiveStartKey"] = last_key

        items.sort(key=lambda c: c.case_id, reverse=True)
        return items[:limit]
