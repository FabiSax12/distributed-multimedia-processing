"""Mensajes que viajan por SQS.

- SubTaskMessage: coordinador -> colas de trabajo (y lo que cae en la DLQ).
- ResultMessage: worker -> cola de resultados, uno por cada cambio de estado.

Los workers nunca escriben Cases ni SubTasks: solo publican ResultMessage.
El coordinador es el único escritor de ese estado.
"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .routing import MediaType, Operation, Pool, Priority
from .states import SubTaskStatus, state_order

SCHEMA_VERSION = 1


def utcnow() -> datetime:
    return datetime.now(UTC)


class ErrorCode(StrEnum):
    UNSUPPORTED_FORMAT = "unsupported_format"   # tipo u operación no soportados
    CORRUPT_INPUT = "corrupt_input"             # ffprobe no puede leer el archivo
    PROCESSING_ERROR = "processing_error"       # ffmpeg terminó con error
    EXTERNAL_API_ERROR = "external_api_error"   # API de letras / catálogos
    TIMEOUT = "timeout"                         # superó el tiempo máximo de la operación
    RETRIES_EXHAUSTED = "retries_exhausted"     # llegó por la DLQ (worker caído)
    CANCELLED = "cancelled"
    INTERNAL = "internal"


class ErrorInfo(BaseModel):
    code: ErrorCode
    message: str = Field(max_length=2000)


class _Message(BaseModel):
    # extra="ignore": un consumidor viejo tolera campos nuevos.
    model_config = ConfigDict(frozen=True, extra="ignore")

    schema_version: int = SCHEMA_VERSION

    def to_body(self) -> str:
        return self.model_dump_json(exclude_none=True)

    @classmethod
    def from_body(cls, body: str):
        return cls.model_validate_json(body)


class SubTaskMessage(_Message):
    """Cuerpo del mensaje en video-alta, audio-normal, etc."""

    subtask_id: str
    case_id: str
    input_key: str                 # clave en el bucket del dataset
    media_type: MediaType
    operation: Operation
    priority: Priority
    output_prefix: str             # results/{case_id}/{subtask_id}/
    params: dict[str, Any] = Field(default_factory=dict)  # p. ej. {"target_format": "mp4"}
    enqueued_at: datetime = Field(default_factory=utcnow)


class ResultMessage(_Message):
    """Cuerpo del mensaje en la cola de resultados."""

    subtask_id: str
    case_id: str
    status: SubTaskStatus
    attempt: int = Field(ge=1)     # ApproximateReceiveCount del mensaje de trabajo
    worker_id: str | None = None   # None solo cuando viene de la DLQ
    pool: Pool | None = None
    progress: int | None = Field(default=None, ge=0, le=100)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    output_keys: list[str] = Field(default_factory=list)
    output_meta: dict[str, Any] = Field(default_factory=dict)  # duración, códec, tags…
    error: ErrorInfo | None = None
    emitted_at: datetime = Field(default_factory=utcnow)

    @model_validator(mode="after")
    def _check_consistency(self):
        if self.status is SubTaskStatus.FAILED and self.error is None:
            raise ValueError("un resultado failed necesita error")
        if self.status is SubTaskStatus.COMPLETED and self.finished_at is None:
            raise ValueError("un resultado completed necesita finished_at")
        if self.status is SubTaskStatus.PENDING:
            raise ValueError("los workers no reportan pending")
        return self

    @property
    def order(self) -> int:
        return state_order(self.status, self.attempt)


def result_from_dlq(msg: SubTaskMessage, receive_count: int) -> ResultMessage:
    """Lo que el coordinador registra al consumir la DLQ."""
    return ResultMessage(
        subtask_id=msg.subtask_id,
        case_id=msg.case_id,
        status=SubTaskStatus.FAILED,
        attempt=max(receive_count, 1),
        finished_at=utcnow(),
        error=ErrorInfo(
            code=ErrorCode.RETRIES_EXHAUSTED,
            message=f"agotó {receive_count} intentos (worker caído o proceso abortado)",
        ),
    )