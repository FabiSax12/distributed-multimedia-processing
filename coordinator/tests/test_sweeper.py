"""`barrier/sweeper.py`: reencola sub-tareas huérfanas y repara casos varados en
pending_count == 0."""

from __future__ import annotations

from datetime import timedelta

from coordinator.barrier.sweeper import run_sweep

from shared.models import CaseItem, SubTaskItem
from shared.routing import MediaType, Operation, Pool, Priority, queue_key
from shared.states import CaseStatus


def test_sweeper_reenqueues_orphan_subtasks(repos, aws, settings) -> None:
    case_id = "orphancase"
    subtask = SubTaskItem.planned(
        case_id=case_id,
        subtask_id=f"{case_id}-0001",
        input_key="dataset/a.mp3",
        media_type=MediaType.AUDIO,
        operation=Operation.AUDIO_CONVERT,
        pool=Pool.AUDIO,
        priority=Priority.NORMAL,
    )
    repos["subtasks"].batch_put_planned([subtask])
    repos["cases"].put_new(
        CaseItem(case_id=case_id, file_count=1, total=1, pending_count=1)
    )
    # nunca se llamó mark_enqueued: enqueued sigue en False (huérfana), y
    # created_at ya "pasó" el umbral de huérfano usando un `now` futuro.

    run_sweep(
        cases_repo=repos["cases"],
        subtasks_repo=repos["subtasks"],
        sqs=aws["sqs"],
        queue_urls=settings.QUEUE_URLS,
        s3=aws["s3"],
        results_bucket=settings.RESULTS_BUCKET,
        now=subtask.created_at + timedelta(seconds=60),
    )

    queue_url = settings.QUEUE_URLS[queue_key(Pool.AUDIO, Priority.NORMAL)]
    depth = aws["sqs"].get_queue_attributes(
        QueueUrl=queue_url, AttributeNames=["ApproximateNumberOfMessages"]
    )["Attributes"]["ApproximateNumberOfMessages"]
    assert depth == "1"

    refreshed = repos["subtasks"].query_by_case(case_id)[0]
    assert refreshed.enqueued is True


def test_sweeper_finalizes_case_stuck_at_zero_pending(repos, aws, settings) -> None:
    case_id = "stuckcase"
    subtask = SubTaskItem.planned(
        case_id=case_id,
        subtask_id=f"{case_id}-0001",
        input_key="dataset/a.mp3",
        media_type=MediaType.AUDIO,
        operation=Operation.AUDIO_CONVERT,
        pool=Pool.AUDIO,
        priority=Priority.NORMAL,
    )
    repos["subtasks"].batch_put_planned([subtask])
    repos["subtasks"].mark_enqueued(case_id, subtask.subtask_id)
    # pending_count ya en 0 pero el caso nunca fue "finalizado" (simula el
    # crash entre el TransactWriteItems de close_subtask y finalize()).
    repos["cases"].put_new(
        CaseItem(case_id=case_id, file_count=1, total=1, pending_count=0)
    )

    run_sweep(
        cases_repo=repos["cases"],
        subtasks_repo=repos["subtasks"],
        sqs=aws["sqs"],
        queue_urls=settings.QUEUE_URLS,
        s3=aws["s3"],
        results_bucket=settings.RESULTS_BUCKET,
    )

    case = repos["cases"].get(case_id, consistent=True)
    assert case.status in {
        CaseStatus.COMPLETED,
        CaseStatus.PARTIALLY_COMPLETED,
        CaseStatus.FAILED,
    }
    assert case.report_key is not None
