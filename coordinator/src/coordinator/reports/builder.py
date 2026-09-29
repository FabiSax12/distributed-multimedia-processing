"""Arma el `CaseReport` consolidado que `barrier/finalize.py` sube a S3."""

from __future__ import annotations

from datetime import datetime

from shared.messages import ErrorCode
from shared.models import (
    CaseItem,
    CaseReport,
    ReportGroup,
    ReportSubTask,
    SubTaskItem,
)
from shared.routing import MediaType, Operation
from shared.states import CaseStatus, SubTaskStatus

_OPERATION_LABELS: dict[Operation, str] = {
    Operation.VIDEO_CONVERT: "videos convertidos",
    Operation.AUDIO_EXTRACT: "audios extraídos",
    Operation.VIDEO_THUMBNAIL: "miniaturas de video generadas",
    Operation.AUDIO_CONVERT: "audios convertidos",
    Operation.IMAGE_THUMBNAIL: "miniaturas de imagen generadas",
    Operation.METADATA: "metadatos extraídos",
    Operation.LYRICS: "letras obtenidas",
    Operation.CLASSIFY: "archivos clasificados",
}

_ERROR_LABELS: dict[ErrorCode, str] = {
    ErrorCode.UNSUPPORTED_FORMAT: "formato no soportado",
    ErrorCode.CORRUPT_INPUT: "archivo corrupto",
    ErrorCode.PROCESSING_ERROR: "error de procesamiento",
    ErrorCode.EXTERNAL_API_ERROR: "error de API externa",
    ErrorCode.TIMEOUT: "tiempo de procesamiento agotado",
    ErrorCode.RETRIES_EXHAUSTED: "reintentos agotados",
    ErrorCode.CANCELLED: "cancelación",
    ErrorCode.INTERNAL: "error interno del worker",
}


def build_report(
    case: CaseItem,
    subtasks: list[SubTaskItem],
    *,
    status: CaseStatus,
    finished_at: datetime,
) -> CaseReport:
    """Construye el reporte consolidado de un caso.

    `status` y `finished_at` se reciben explícitos en vez de leerlos de
    `case.status`/`case.finished_at`: en el momento en que `finalize()` llama
    a esto, todavía no escribió el estado final en DynamoDB (el reporte se
    sube a S3 ANTES que el estado del caso, ver docstring de
    `barrier/finalize.py`), así que `case` todavía trae el estado previo
    (`processing`, `retrying`, ...).
    """
    groups = _build_groups(subtasks)
    report_subtasks = [_build_subtask_report(subtask) for subtask in subtasks]
    workers_used = sorted(
        {subtask.worker_id for subtask in subtasks if subtask.worker_id}
    )
    duration_s = (finished_at - case.created_at).total_seconds()
    summary = _build_summary(case, subtasks)

    return CaseReport(
        case_id=case.case_id,
        label=case.label,
        status=status,
        priority=case.priority,
        created_at=case.created_at,
        started_at=case.started_at,
        finished_at=finished_at,
        duration_s=duration_s,
        file_count=case.file_count,
        subtask_count=len(subtasks),
        groups=groups,
        subtasks=report_subtasks,
        workers_used=workers_used,
        summary=summary,
    )


def _build_groups(subtasks: list[SubTaskItem]) -> list[ReportGroup]:
    """Agrupa por (media_type, operation); las prefallidas caen en (None, None)."""
    buckets: dict[tuple[MediaType | None, Operation | None], dict[str, int]] = {}
    for subtask in subtasks:
        key = (subtask.media_type, subtask.operation)
        bucket = buckets.setdefault(
            key, {"total": 0, "completed": 0, "failed": 0, "cancelled": 0}
        )
        bucket["total"] += 1
        if subtask.status is SubTaskStatus.COMPLETED:
            bucket["completed"] += 1
        elif subtask.status is SubTaskStatus.FAILED:
            bucket["failed"] += 1
        elif subtask.status is SubTaskStatus.CANCELLED:
            bucket["cancelled"] += 1

    return [
        ReportGroup(media_type=media_type, operation=operation, **counts)
        for (media_type, operation), counts in buckets.items()
    ]


def _build_subtask_report(subtask: SubTaskItem) -> ReportSubTask:
    duration_s = None
    if subtask.started_at is not None and subtask.finished_at is not None:
        duration_s = (subtask.finished_at - subtask.started_at).total_seconds()

    return ReportSubTask(
        subtask_id=subtask.subtask_id,
        input_key=subtask.input_key,
        media_type=subtask.media_type,
        operation=subtask.operation,
        status=subtask.status,
        worker_id=subtask.worker_id,
        attempt=subtask.attempt,
        started_at=subtask.started_at,
        finished_at=subtask.finished_at,
        duration_s=duration_s,
        output_keys=subtask.output_keys,
        error=subtask.error,
    )


def _build_summary(case: CaseItem, subtasks: list[SubTaskItem]) -> str:
    """Texto en español, p. ej.:

    'de 55 archivos — 30 audios convertidos, 10 videos convertidos,
    15 miniaturas de video generadas, 2 fallidos por formato no soportado'
    """
    completed_counts: dict[Operation, int] = {}
    error_counts: dict[ErrorCode, int] = {}

    for subtask in subtasks:
        if subtask.status is SubTaskStatus.COMPLETED and subtask.operation is not None:
            completed_counts[subtask.operation] = (
                completed_counts.get(subtask.operation, 0) + 1
            )
        elif subtask.status is SubTaskStatus.FAILED and subtask.error is not None:
            error_counts[subtask.error.code] = (
                error_counts.get(subtask.error.code, 0) + 1
            )

    parts = [
        f"{count} {_OPERATION_LABELS[op]}" for op, count in completed_counts.items()
    ]
    parts += [
        f"{count} fallido{'s' if count != 1 else ''} por {_ERROR_LABELS[code]}"
        for code, count in error_counts.items()
    ]

    if not parts:
        return f"de {case.file_count} archivos, ningún resultado registrado todavía"

    return f"de {case.file_count} archivos — " + ", ".join(parts)
