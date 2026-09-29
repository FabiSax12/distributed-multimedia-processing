"""`shared.states.final_case_status` (ya existe, solo se testea) y el texto del
`summary` de `reports/builder.build_report`."""

from __future__ import annotations

from datetime import timedelta

from coordinator.reports.builder import build_report

from shared.messages import ErrorCode, ErrorInfo, utcnow
from shared.models import CaseItem, SubTaskItem
from shared.routing import MediaType, Operation, Pool
from shared.states import CaseStatus, SubTaskStatus, final_case_status


def test_final_case_status_all_completed() -> None:
    assert (
        final_case_status(total=5, failed_count=0, cancel_requested=False)
        == CaseStatus.COMPLETED
    )


def test_final_case_status_partial_failure() -> None:
    assert (
        final_case_status(total=5, failed_count=2, cancel_requested=False)
        == CaseStatus.PARTIALLY_COMPLETED
    )


def test_final_case_status_all_failed() -> None:
    assert (
        final_case_status(total=3, failed_count=3, cancel_requested=False)
        == CaseStatus.FAILED
    )


def test_final_case_status_cancel_wins_over_failures() -> None:
    assert (
        final_case_status(total=3, failed_count=3, cancel_requested=True)
        == CaseStatus.CANCELLED
    )


def _subtask(
    subtask_id: str,
    *,
    operation: Operation | None,
    media_type: MediaType | None,
    status: SubTaskStatus,
    error: ErrorInfo | None = None,
) -> SubTaskItem:
    return SubTaskItem(
        case_id="case1",
        subtask_id=subtask_id,
        input_key=f"dataset/{subtask_id}.mp4",
        media_type=media_type,
        operation=operation,
        pool=Pool.VIDEO if media_type else None,
        status=status,
        error=error,
        finished_at=utcnow()
        if status in {SubTaskStatus.COMPLETED, SubTaskStatus.FAILED}
        else None,
    )


def test_build_report_summary_text() -> None:
    case = CaseItem(
        case_id="case1",
        file_count=3,
        total=3,
        pending_count=0,
        failed_count=1,
        created_at=utcnow() - timedelta(minutes=5),
    )
    subtasks = [
        _subtask(
            "s1",
            operation=Operation.AUDIO_CONVERT,
            media_type=MediaType.AUDIO,
            status=SubTaskStatus.COMPLETED,
        ),
        _subtask(
            "s2",
            operation=Operation.AUDIO_CONVERT,
            media_type=MediaType.AUDIO,
            status=SubTaskStatus.COMPLETED,
        ),
        _subtask(
            "s3",
            operation=None,
            media_type=None,
            status=SubTaskStatus.FAILED,
            error=ErrorInfo(
                code=ErrorCode.UNSUPPORTED_FORMAT,
                message="formato no soportado: s3.xyz",
            ),
        ),
    ]

    report = build_report(
        case, subtasks, status=CaseStatus.PARTIALLY_COMPLETED, finished_at=utcnow()
    )

    assert "de 3 archivos" in report.summary
    assert "2 audios convertidos" in report.summary
    assert "1 fallido por formato no soportado" in report.summary
    assert report.subtask_count == 3
    assert len(report.groups) == 2  # (audio, audio_convert) y (None, None)


def test_build_report_summary_empty_when_no_terminal_results() -> None:
    case = CaseItem(case_id="case2", file_count=1, total=1, pending_count=1)
    subtasks = [
        _subtask(
            "s1",
            operation=Operation.AUDIO_CONVERT,
            media_type=MediaType.AUDIO,
            status=SubTaskStatus.PENDING,
        )
    ]
    report = build_report(
        case, subtasks, status=CaseStatus.PROCESSING, finished_at=utcnow()
    )
    assert "ningún resultado registrado todavía" in report.summary
