"""`consumers/dlq.py`: un mensaje de la DLQ termina como `failed` con `retries_exhausted`."""

from __future__ import annotations

from coordinator.consumers.dlq import build_dlq_handler

from shared.messages import SubTaskMessage
from shared.models import CaseItem, SubTaskItem, result_prefix
from shared.routing import MediaType, Operation, Pool, Priority


def test_dlq_message_closes_subtask_as_failed_retries_exhausted(
    repos, aws, settings
) -> None:
    case_id = "dlqcase"
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
    repos["cases"].put_new(
        CaseItem(case_id=case_id, file_count=1, total=1, pending_count=1)
    )

    message = SubTaskMessage(
        subtask_id=subtask.subtask_id,
        case_id=case_id,
        input_key=subtask.input_key,
        media_type=MediaType.AUDIO,
        operation=Operation.AUDIO_CONVERT,
        priority=Priority.NORMAL,
        output_prefix=result_prefix(case_id, subtask.subtask_id),
    )

    handler = build_dlq_handler(
        cases_repo=repos["cases"],
        subtasks_repo=repos["subtasks"],
        dynamodb=aws["dynamodb"],
        s3=aws["s3"],
        results_bucket=settings.RESULTS_BUCKET,
        dlq_assumed_attempts=settings.DLQ_ASSUMED_ATTEMPTS,
    )

    should_delete = handler({"Body": message.to_body(), "MessageId": "m1"})
    assert should_delete is True

    updated = repos["subtasks"].query_by_case(case_id)[0]
    assert updated.status.value == "failed"
    assert updated.error.code.value == "retries_exhausted"

    case = repos["cases"].get(case_id, consistent=True)
    assert case.status.value == "failed"
    assert case.failed_count == 1
