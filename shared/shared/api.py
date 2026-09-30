"""Contratos de la API REST del coordinador (/api/*).

FastAPI genera el OpenAPI a partir de estos modelos; el dashboard saca sus
tipos de ahí con `openapi-typescript`, así los contratos se escriben una vez.

Endpoints previstos:
  GET    /api/dataset              -> list[DatasetEntry]
  POST   /api/uploads              UploadUrlRequest -> UploadUrlResponse
  POST   /api/cases                CreateCaseRequest -> CreateCaseResponse
  GET    /api/cases/{case_id}      -> CaseDetailResponse
  DELETE /api/cases/{case_id}      -> CaseItem (cancel_requested = true)
  GET    /api/cases/{case_id}/report -> ReportUrlResponse
  GET    /api/cases/{case_id}/outputs -> CaseOutputsResponse
  GET    /api/state                -> StateResponse   (polling cada 2 s)
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from .messages import utcnow
from .models import CaseItem, CaseSource, LoadSample, SubTaskItem, WorkerItem
from .routing import MediaType, Operation, Priority

# ---- Dataset y subida manual ---------------------------------------------- #

class DatasetEntry(BaseModel):
    input_key: str
    size_bytes: int
    media_type: MediaType | None          # None = extensión no soportada
    metadata: dict[str, Any] = Field(default_factory=dict)  # del JSON del dataset


class UploadUrlRequest(BaseModel):
    filenames: list[str] = Field(min_length=1, max_length=200)


class UploadUrl(BaseModel):
    filename: str
    input_key: str
    url: str                  # PUT prefirmado
    expires_in: int


class UploadUrlResponse(BaseModel):
    upload_id: str
    urls: list[UploadUrl]


# ---- Casos ------------------------------------------------------------------ #

class CaseFileIn(BaseModel):
    input_key: str
    operations: list[Operation] | None = None   # None = el coordinador decide
    params: dict[str, Any] = Field(default_factory=dict)


class CreateCaseRequest(BaseModel):
    files: list[CaseFileIn] = Field(min_length=1, max_length=1000)
    priority: Priority = Priority.NORMAL
    label: str | None = Field(default=None, max_length=200)
    source: CaseSource = CaseSource.MANUAL


class CreateCaseResponse(BaseModel):
    case_id: str
    file_count: int
    subtask_count: int
    prefailed_count: int      # sub-tareas creadas ya fallidas (formato no soportado)


class CaseDetailResponse(BaseModel):
    case: CaseItem
    subtasks: list[SubTaskItem]


class ReportUrlResponse(BaseModel):
    case_id: str
    url: str                  # GET prefirmado del report.json
    expires_in: int


class SubTaskOutputUrl(BaseModel):
    key: str
    url: str                  # GET prefirmado del archivo de salida
    expires_in: int


class SubTaskOutputs(BaseModel):
    subtask_id: str
    outputs: list[SubTaskOutputUrl]


class CaseOutputsResponse(BaseModel):
    """URLs prefirmadas de los `output_keys` de cada sub-tarea del caso.

    A diferencia de `ReportUrlResponse`, no exige que el caso haya terminado:
    una sub-tarea puede tener `output_keys` mientras el caso sigue en curso.
    Solo incluye sub-tareas con al menos un `output_key`.
    """

    case_id: str
    subtasks: list[SubTaskOutputs]


# ---- Estado del sistema ----------------------------------------------------- #

class QueueDepth(BaseModel):
    queue: str                # nombre lógico: "video-alta", "resultados", "dlq"
    visible: int
    in_flight: int


class WorkerView(WorkerItem):
    alive: bool


class CaseSummary(BaseModel):
    """Caso + conteo de sub-tareas por estado, para la vista general del panel."""

    case: CaseItem
    by_status: dict[str, int]         # {"pending": 3, "running": 2, ...}


class Alert(BaseModel):
    """Alerta operativa (p. ej. caso estancado, cola desbordada) para el panel."""

    level: str  # "info" | "warning" | "critical"
    pool: str  # pool afectado, o "-" si no aplica a un pool en particular
    message: str
    ts: datetime = Field(default_factory=utcnow)


class StateResponse(BaseModel):
    generated_at: datetime = Field(default_factory=utcnow)
    cases: list[CaseSummary]
    workers: list[WorkerView]
    queues: list[QueueDepth]
    alerts: list[Alert] = Field(default_factory=list)


# ---- Métricas ----------------------------------------------------------------- #


class MetricsResponse(BaseModel):
    samples: list[LoadSample]
