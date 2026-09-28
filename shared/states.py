"""Estados de sub-tareas y de casos, y la regla para ordenarlos.

La cola de resultados es SQS Standard: puede entregar mensajes duplicados o
fuera de orden. Cada estado de sub-tarea tiene un número de orden
(`state_order`) que solo crece. El coordinador actualiza SubTasks con la
condición `state_order < :nuevo`, así un mensaje atrasado ("running") nunca
pisa uno más reciente ("completed").

El orden depende del intento: el "retrying" del intento 2 es más nuevo que el
"running" del intento 1. Los estados terminales ganan siempre.
"""

from enum import StrEnum


class SubTaskStatus(StrEnum):
    PENDING = "pending"        # pendiente: el mensaje está visible en su cola
    ASSIGNED = "assigned"      # asignado: un worker lo recibió (invisible por visibility timeout)
    RUNNING = "running"        # en ejecución: arrancó ffmpeg / la llamada externa
    RETRYING = "retrying"      # el mensaje volvió a la cola (ApproximateReceiveCount > 1)
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_SUBTASK: frozenset[SubTaskStatus] = frozenset(
    {SubTaskStatus.COMPLETED, SubTaskStatus.FAILED, SubTaskStatus.CANCELLED}
)

# Orden dentro de un mismo intento.
_RANK_IN_ATTEMPT = {
    SubTaskStatus.RETRYING: 0,
    SubTaskStatus.ASSIGNED: 1,
    SubTaskStatus.RUNNING: 2,
}
_TERMINAL_ORDER = 1_000_000


def state_order(status: SubTaskStatus, attempt: int) -> int:
    """Número monótono para la condición de escritura en SubTasks.

    `attempt` es el ApproximateReceiveCount del mensaje (1 en la primera entrega).
    """
    if status in TERMINAL_SUBTASK:
        return _TERMINAL_ORDER
    if status is SubTaskStatus.PENDING:
        return 0
    if attempt < 1:
        raise ValueError("attempt debe ser >= 1 para estados distintos de pending")
    return attempt * 10 + _RANK_IN_ATTEMPT[status]


class CaseStatus(StrEnum):
    QUEUED = "queued"                          # registrado, ninguna sub-tarea arrancó
    PROCESSING = "processing"                  # al menos una sub-tarea arrancó
    RETRYING = "retrying"                      # alguna sub-tarea se está reintentando
    COMPLETED = "completed"                    # todas terminaron bien
    PARTIALLY_COMPLETED = "partially_completed"  # terminó con al menos una fallida
    FAILED = "failed"                          # fallaron todas
    CANCELLED = "cancelled"                    # el operador lo canceló


TERMINAL_CASE: frozenset[CaseStatus] = frozenset(
    {
        CaseStatus.COMPLETED,
        CaseStatus.PARTIALLY_COMPLETED,
        CaseStatus.FAILED,
        CaseStatus.CANCELLED,
    }
)


def final_case_status(total: int, failed_count: int, cancel_requested: bool) -> CaseStatus:
    """Estado agregado que se escribe cuando el barrier llega a pending_count = 0."""
    if cancel_requested:
        return CaseStatus.CANCELLED
    if failed_count == 0:
        return CaseStatus.COMPLETED
    if failed_count >= total:
        return CaseStatus.FAILED
    return CaseStatus.PARTIALLY_COMPLETED