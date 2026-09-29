"""`routing/planner.py`: caso homogéneo, heterogéneo, extensión no soportada,
operación inválida para el tipo. No necesita AWS (plan_case no hace I/O)."""

from __future__ import annotations

from coordinator.routing.planner import plan_case

from shared.api import CaseFileIn
from shared.routing import Operation, Priority
from shared.states import SubTaskStatus


def test_homogeneous_case_video() -> None:
    files = [
        CaseFileIn(input_key="dataset/a.mp4"),
        CaseFileIn(input_key="dataset/b.mp4"),
    ]
    subtasks, prefailed = plan_case(files, "case1", Priority.NORMAL)

    assert prefailed == 0
    # cada video produce 3 operaciones por defecto (convert, audio, thumbnail)
    assert len(subtasks) == 6
    assert all(st.status == SubTaskStatus.PENDING for st in subtasks)
    assert all(st.queue is not None for st in subtasks)
    assert {st.operation for st in subtasks} == {
        Operation.VIDEO_CONVERT,
        Operation.AUDIO_EXTRACT,
        Operation.VIDEO_THUMBNAIL,
    }


def test_heterogeneous_case() -> None:
    files = [
        CaseFileIn(input_key="dataset/a.mp4"),
        CaseFileIn(input_key="dataset/b.mp3"),
        CaseFileIn(input_key="dataset/c.png"),
    ]
    subtasks, prefailed = plan_case(files, "case2", Priority.NORMAL)

    assert prefailed == 0
    # video (3) + audio (2: convert+metadata) + image (1: thumbnail)
    assert len(subtasks) == 6
    pools = {st.pool for st in subtasks}
    assert len(pools) == 3

    # subtask_id consecutivo y global, no reiniciado por archivo
    ids = [st.subtask_id for st in subtasks]
    assert ids == [f"case2-{i:04d}" for i in range(1, 7)]


def test_unsupported_extension_prefails_without_enqueueing() -> None:
    files = [CaseFileIn(input_key="dataset/notes.txt")]
    subtasks, prefailed = plan_case(files, "case3", Priority.NORMAL)

    assert prefailed == 1
    assert len(subtasks) == 1
    subtask = subtasks[0]
    assert subtask.status == SubTaskStatus.FAILED
    assert subtask.enqueued is False
    assert subtask.queue is None
    assert subtask.media_type is None
    assert subtask.error is not None
    assert subtask.error.code.value == "unsupported_format"


def test_invalid_operation_for_media_type_prefails() -> None:
    # LYRICS solo aplica a audio, no a video.
    files = [CaseFileIn(input_key="dataset/a.mp4", operations=[Operation.LYRICS])]
    subtasks, prefailed = plan_case(files, "case4", Priority.NORMAL)

    assert prefailed == 1
    assert subtasks[0].status == SubTaskStatus.FAILED


def test_mixed_case_counts_prefailed_and_valid_separately() -> None:
    files = [
        CaseFileIn(input_key="dataset/a.mp4"),
        CaseFileIn(input_key="dataset/bad.txt"),
    ]
    subtasks, prefailed = plan_case(files, "case5", Priority.NORMAL)

    assert prefailed == 1
    assert len(subtasks) == 4  # 3 del video + 1 prefallida
