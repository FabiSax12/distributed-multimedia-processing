"""`consumers/results.py::build_results_handler`: sin cobertura previa.

Casos cubiertos: body ilegible, progreso no-terminal válido, progreso
no-terminal desactualizado (se descarta sin tocar el caso), resultado
terminal (delega a `close_subtask`), y el caso en que `close_subtask` lanza
una excepción que sí debe reintentarse (el handler no debe borrar el mensaje).
"""

from __future__ import annotations

from coordinator.consumers import results as results_module
from coordinator.consumers.results import build_results_handler

from shared.messages import ResultMessage, utcnow
from shared.models import CaseItem, SubTaskItem
from shared.routing import MediaType, Operation, Pool, Priority
from shared.states import CaseStatus, SubTaskStatus


def _make_case_with_subtask(repos, case_id: str) -> SubTaskItem:
    subtask = SubTaskItem.planned(
        case_id=case_id,
        subtask_id=f"{case_id}-0001",
        input_key=f"dataset/{case_id}.mp3",
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
    return subtask


def _handler(repos, aws, settings):
    return build_results_handler(
        cases_repo=repos["cases"],
        subtasks_repo=repos["subtasks"],
        dynamodb=aws["dynamodb"],
        s3=aws["s3"],
        results_bucket=settings.RESULTS_BUCKET,
    )


def test_malformed_body_is_deleted_without_raising(repos, aws, settings) -> None:
    handle = _handler(repos, aws, settings)

    should_delete = handle({"Body": "{not valid json", "MessageId": "m1"})

    assert should_delete is True


def test_valid_non_terminal_result_updates_progress(repos, aws, settings) -> None:
    case_id = "progresscase"
    subtask = _make_case_with_subtask(repos, case_id)
    handle = _handler(repos, aws, settings)

    result = ResultMessage(
        subtask_id=subtask.subtask_id,
        case_id=case_id,
        status=SubTaskStatus.RUNNING,
        attempt=1,
        worker_id="w1",
        progress=10,
    )

    should_delete = handle({"Body": result.to_body(), "MessageId": "m2"})

    assert should_delete is True
    updated = repos["subtasks"].query_by_case(case_id)[0]
    assert updated.status == SubTaskStatus.RUNNING
    assert updated.progress == 10
    case = repos["cases"].get(case_id, consistent=True)
    assert case.status == CaseStatus.PROCESSING


def test_stale_non_terminal_result_is_discarded_without_touching_case(
    repos, aws, settings
) -> None:
    case_id = "staleprogresscase"
    subtask = _make_case_with_subtask(repos, case_id)
    handle = _handler(repos, aws, settings)

    fresh = ResultMessage(
        subtask_id=subtask.subtask_id,
        case_id=case_id,
        status=SubTaskStatus.RUNNING,
        attempt=1,
        worker_id="w1",
        progress=50,
    )
    assert handle({"Body": fresh.to_body(), "MessageId": "m3"}) is True

    # `ASSIGNED` del mismo intento tiene un state_order MENOR que `RUNNING`: es
    # un mensaje atrasado/duplicado, `update_progress` devuelve False.
    stale = ResultMessage(
        subtask_id=subtask.subtask_id,
        case_id=case_id,
        status=SubTaskStatus.ASSIGNED,
        attempt=1,
        worker_id="w1",
    )
    should_delete = handle({"Body": stale.to_body(), "MessageId": "m4"})

    assert should_delete is True  # se descarta igual, no se reintenta
    updated = repos["subtasks"].query_by_case(case_id)[0]
    assert updated.status == SubTaskStatus.RUNNING  # no lo pisó el atrasado


def test_terminal_result_delegates_to_close_subtask(repos, aws, settings) -> None:
    case_id = "terminalcase"
    subtask = _make_case_with_subtask(repos, case_id)
    handle = _handler(repos, aws, settings)

    result = ResultMessage(
        subtask_id=subtask.subtask_id,
        case_id=case_id,
        status=SubTaskStatus.COMPLETED,
        attempt=1,
        worker_id="w1",
        finished_at=utcnow(),
        output_keys=[f"results/{case_id}/{subtask.subtask_id}/out.bin"],
    )

    should_delete = handle({"Body": result.to_body(), "MessageId": "m5"})

    assert should_delete is True
    case = repos["cases"].get(case_id, consistent=True)
    assert case.pending_count == 0
    assert case.status == CaseStatus.COMPLETED


def test_close_subtask_retryable_exception_keeps_message(
    repos, aws, settings, monkeypatch
) -> None:
    case_id = "retrycase"
    subtask = _make_case_with_subtask(repos, case_id)
    handle = _handler(repos, aws, settings)

    def _boom(*args, **kwargs):
        raise RuntimeError("falla transitoria simulada")

    monkeypatch.setattr(results_module, "close_subtask", _boom)

    result = ResultMessage(
        subtask_id=subtask.subtask_id,
        case_id=case_id,
        status=SubTaskStatus.COMPLETED,
        attempt=1,
        worker_id="w1",
        finished_at=utcnow(),
    )

    should_delete = handle({"Body": result.to_body(), "MessageId": "m6"})

    assert should_delete is False  # no se borra: que SQS reintente
