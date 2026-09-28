"""Ítems de DynamoDB, reporte consolidado, muestras de carga y claves de S3.

Tablas:
- Cases     PK case_id
- SubTasks  PK case_id, SK subtask_id  (un Query trae todas las sub-tareas del caso)
- Workers   PK worker_id

Atributos del barrier en Cases que NO están en el modelo:
- `closed` (string set con los subtask_id ya contados). No se inicializa:
  DynamoDB no acepta sets vacíos; el primer `ADD closed :idSet` lo crea y
  `NOT contains(closed, :id)` es verdadero mientras no exista.
"""

from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from .messages import ErrorInfo, SubTaskMessage, utcnow
from .routing import MediaType, Operation, Pool, Priority, queue_key
from .states import CaseStatus, SubTaskStatus

TABLE_CASES = "Cases"
TABLE_SUBTASKS = "SubTasks"
TABLE_WORKERS = "Workers"

HEARTBEAT_INTERVAL_S = 5
WORKER_DEAD_AFTER_S = 15


# --------------------------------------------------------------------------- #
# Conversión Pydantic <-> DynamoDB (boto3 resource usa Decimal, no float)
# --------------------------------------------------------------------------- #

M = TypeVar("M", bound=BaseModel)


def _floats_to_decimal(value: Any) -> Any:
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: _floats_to_decimal(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_floats_to_decimal(v) for v in value]
    return value


def to_item(model: BaseModel) -> dict[str, Any]:
    """Modelo -> ítem para put_item. Fechas como ISO 8601 (ordenables), sin None."""
    return _floats_to_decimal(model.model_dump(mode="json", exclude_none=True))


def from_item(cls: type[M], item: dict[str, Any]) -> M:
    return cls.model_validate(item)


class _Item(BaseModel):
    model_config = ConfigDict(extra="ignore")  # ignora `closed` y atributos futuros


# --------------------------------------------------------------------------- #
# Cases
# --------------------------------------------------------------------------- #

class CaseSource(StrEnum):
    MANUAL = "manual"   # enviado desde el panel o la API
    AUTO = "auto"       # agrupado por el generador (carpeta / JSON de metadatos)


class CaseItem(_Item):
    case_id: str
    label: str | None = None
    source: CaseSource = CaseSource.MANUAL
    priority: Priority = Priority.NORMAL
    status: CaseStatus = CaseStatus.QUEUED

    file_count: int = Field(ge=1)
    total: int = Field(ge=1)          # sub-tareas, no archivos
    pending_count: int = Field(ge=0)  # contador del barrier
    failed_count: int = 0
    cancelled_count: int = 0
    cancel_requested: bool = False

    created_at: datetime = Field(default_factory=utcnow)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    report_key: str | None = None


# --------------------------------------------------------------------------- #
# SubTasks
# --------------------------------------------------------------------------- #

class SubTaskItem(_Item):
    case_id: str
    subtask_id: str
    input_key: str

    # None solo en sub-tareas prefallidas (formato no soportado, nunca encoladas).
    media_type: MediaType | None = None
    operation: Operation | None = None
    pool: Pool | None = None
    queue: str | None = None
    priority: Priority = Priority.NORMAL

    status: SubTaskStatus = SubTaskStatus.PENDING
    state_order: int = 0     # condición de escritura: solo se acepta uno mayor
    attempt: int = 0
    enqueued: bool = False   # outbox: False hasta que SendMessage confirmó

    worker_id: str | None = None
    progress: int | None = None
    created_at: datetime = Field(default_factory=utcnow)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    output_keys: list[str] = Field(default_factory=list)
    output_meta: dict[str, Any] = Field(default_factory=dict)
    error: ErrorInfo | None = None
    params: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def planned(
        cls,
        *,
        case_id: str,
        subtask_id: str,
        input_key: str,
        media_type: MediaType,
        operation: Operation,
        pool: Pool,
        priority: Priority,
        params: dict[str, Any] | None = None,
    ) -> "SubTaskItem":
        return cls(
            case_id=case_id,
            subtask_id=subtask_id,
            input_key=input_key,
            media_type=media_type,
            operation=operation,
            pool=pool,
            queue=queue_key(pool, priority),
            priority=priority,
            params=params or {},
        )

    def to_message(self) -> SubTaskMessage:
        if self.operation is None or self.media_type is None:
            raise ValueError("una sub-tarea prefallida no se encola")
        return SubTaskMessage(
            subtask_id=self.subtask_id,
            case_id=self.case_id,
            input_key=self.input_key,
            media_type=self.media_type,
            operation=self.operation,
            priority=self.priority,
            output_prefix=result_prefix(self.case_id, self.subtask_id),
            params=self.params,
        )


# --------------------------------------------------------------------------- #
# Workers (heartbeat: lo escribe cada worker directamente)
# --------------------------------------------------------------------------- #

class WorkerItem(_Item):
    worker_id: str          # p. ej. "video-i-0abc123-4711" (pool-instancia-pid)
    pool: Pool
    instance_type: str
    hostname: str
    concurrency: int = Field(ge=1)
    cpu_percent: float = 0.0
    mem_percent: float = 0.0
    active_subtasks: list[str] = Field(default_factory=list)
    started_at: datetime
    last_seen: datetime

    def is_alive(self, now: datetime | None = None) -> bool:
        now = now or utcnow()
        return now - self.last_seen <= timedelta(seconds=WORKER_DEAD_AFTER_S)


# --------------------------------------------------------------------------- #
# Muestras de carga (evidencia para el informe; se guardan en S3 como JSON Lines)
# --------------------------------------------------------------------------- #

class WorkerLoad(BaseModel):
    pool: Pool
    alive: bool
    cpu_percent: float
    mem_percent: float
    active: int


class LoadSample(BaseModel):
    ts: datetime = Field(default_factory=utcnow)
    queue_visible: dict[str, int]      # por cola lógica
    queue_in_flight: dict[str, int]
    workers: dict[str, WorkerLoad]     # por worker_id
    cases_open: int


# --------------------------------------------------------------------------- #
# Reporte consolidado por caso (results/{case_id}/report.json)
# --------------------------------------------------------------------------- #

class ReportSubTask(BaseModel):
    subtask_id: str
    input_key: str
    media_type: MediaType | None
    operation: Operation | None
    status: SubTaskStatus
    worker_id: str | None
    attempt: int
    started_at: datetime | None
    finished_at: datetime | None
    duration_s: float | None
    output_keys: list[str]
    error: ErrorInfo | None


class ReportGroup(BaseModel):
    """Una fila del resumen: tipo + operación."""

    media_type: MediaType | None   # None = no soportado
    operation: Operation | None
    total: int
    completed: int
    failed: int
    cancelled: int


class CaseReport(BaseModel):
    schema_version: int = 1
    case_id: str
    label: str | None
    status: CaseStatus
    priority: Priority
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime
    duration_s: float
    file_count: int
    subtask_count: int
    groups: list[ReportGroup]
    subtasks: list[ReportSubTask]
    workers_used: list[str]
    summary: str   # "de 55 archivos — 30 audios convertidos, 10 videos…, 2 fallidos por formato"


# --------------------------------------------------------------------------- #
# Claves de S3
# --------------------------------------------------------------------------- #

def upload_key(upload_id: str, filename: str) -> str:
    """Archivos subidos a mano desde el panel (bucket del dataset)."""
    return f"uploads/{upload_id}/{filename}"


def result_prefix(case_id: str, subtask_id: str) -> str:
    return f"results/{case_id}/{subtask_id}/"


def report_key(case_id: str) -> str:
    return f"results/{case_id}/report.json"


def load_samples_key(day: str) -> str:
    """day en formato YYYY-MM-DD."""
    return f"metrics/{day}.jsonl"