"""Traduce los archivos de un `CreateCaseRequest` en sub-tareas planificadas.

No hace I/O (ni S3 ni DynamoDB): la capa API (tanda 2) valida con `head_object`
que `input_key` exista en el bucket del dataset ANTES de llamar a `plan_case`.
"""

from __future__ import annotations

from shared.api import CaseFileIn
from shared.messages import ErrorCode, ErrorInfo, utcnow
from shared.models import SubTaskItem
from shared.routing import (
    OPERATION_POOL,
    Priority,
    UnsupportedFile,
    detect_media_type,
    plan_operations,
)
from shared.states import SubTaskStatus, state_order


def plan_case(
    files: list[CaseFileIn], case_id: str, priority: Priority
) -> tuple[list[SubTaskItem], int]:
    """Sub-tareas planificadas + cuántas nacieron ya fallidas (formato no soportado).

    Los `subtask_id` son consecutivos y globales al caso (`{case_id}-0001`,
    `{case_id}-0002`, ...), sin reiniciar el contador por archivo: así el
    orden en el reporte refleja el orden en que se recibieron los archivos.
    """
    subtasks: list[SubTaskItem] = []
    prefailed_count = 0
    counter = 1

    for file in files:
        try:
            operations = plan_operations(file.input_key, file.operations)
        except UnsupportedFile as exc:
            subtask_id = f"{case_id}-{counter:04d}"
            counter += 1
            # Una sola sub-tarea prefallida por ARCHIVO (no una por operación
            # pedida): no sabemos a qué pool asignarla porque ni el tipo de
            # medio se pudo determinar, así que nunca se encola.
            subtasks.append(
                SubTaskItem(
                    case_id=case_id,
                    subtask_id=subtask_id,
                    input_key=file.input_key,
                    priority=priority,
                    status=SubTaskStatus.FAILED,
                    state_order=state_order(SubTaskStatus.FAILED, attempt=1),
                    attempt=0,
                    error=ErrorInfo(
                        code=ErrorCode.UNSUPPORTED_FORMAT, message=str(exc)
                    ),
                    finished_at=utcnow(),
                    enqueued=False,
                )
            )
            prefailed_count += 1
            continue

        # plan_operations ya validó el tipo de medio, así que nunca es None acá.
        media_type = detect_media_type(file.input_key)
        assert media_type is not None

        for operation in operations:
            subtask_id = f"{case_id}-{counter:04d}"
            counter += 1
            subtasks.append(
                SubTaskItem.planned(
                    case_id=case_id,
                    subtask_id=subtask_id,
                    input_key=file.input_key,
                    media_type=media_type,
                    operation=operation,
                    pool=OPERATION_POOL[operation],
                    priority=priority,
                    params=file.params,
                )
            )

    return subtasks, prefailed_count
