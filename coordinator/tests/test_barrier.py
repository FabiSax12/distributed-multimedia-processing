"""`barrier/close.py` + `barrier/finalize.py`: duplicados, desorden, concurrencia real,
crash entre pending_count=0 y finalize, y cancelación."""

from __future__ import annotations

import logging
import threading

from coordinator.barrier.close import close_subtask
from coordinator.barrier.finalize import finalize

from shared.messages import ErrorCode, ErrorInfo, ResultMessage, utcnow
from shared.models import CaseItem, SubTaskItem
from shared.routing import MediaType, Operation, Pool, Priority
from shared.states import CaseStatus, SubTaskStatus, state_order


def _make_case_with_subtasks(repos, case_id: str, n: int) -> list[SubTaskItem]:
    subtasks = [
        SubTaskItem.planned(
            case_id=case_id,
            subtask_id=f"{case_id}-{i:04d}",
            input_key=f"dataset/{case_id}-{i}.mp3",
            media_type=MediaType.AUDIO,
            operation=Operation.AUDIO_CONVERT,
            pool=Pool.AUDIO,
            priority=Priority.NORMAL,
        )
        for i in range(1, n + 1)
    ]
    repos["subtasks"].batch_put_planned(subtasks)
    repos["cases"].put_new(
        CaseItem(
            case_id=case_id,
            file_count=n,
            total=n,
            pending_count=n,
        )
    )
    for subtask in subtasks:
        repos["subtasks"].mark_enqueued(case_id, subtask.subtask_id)
    return subtasks


def _completed_result(case_id: str, subtask_id: str, attempt: int = 1) -> ResultMessage:
    return ResultMessage(
        subtask_id=subtask_id,
        case_id=case_id,
        status=SubTaskStatus.COMPLETED,
        attempt=attempt,
        worker_id="w1",
        finished_at=utcnow(),
        output_keys=[f"results/{case_id}/{subtask_id}/out.bin"],
    )


def test_duplicate_terminal_result_counts_once(repos, aws) -> None:
    case_id = "dupcase"
    subtasks = _make_case_with_subtasks(repos, case_id, 2)
    result = _completed_result(case_id, subtasks[0].subtask_id)

    kwargs = {
        "cases_repo": repos["cases"],
        "subtasks_repo": repos["subtasks"],
        "dynamodb": aws["dynamodb"],
        "s3": aws["s3"],
        "results_bucket": "test-results-bucket",
    }
    close_subtask(result, **kwargs)
    close_subtask(result, **kwargs)  # duplicado: mismo ResultMessage otra vez

    case = repos["cases"].get(case_id, consistent=True)
    assert case.pending_count == 1  # solo se descontó una vez


def test_stale_running_after_completed_is_ignored(repos) -> None:
    case_id = "stalecase"
    subtasks = _make_case_with_subtasks(repos, case_id, 1)
    subtask_id = subtasks[0].subtask_id

    # completed en el intento 1 (state_order terminal, el más alto posible)
    updated = repos["subtasks"].update_progress(
        case_id,
        subtask_id,
        status=SubTaskStatus.COMPLETED.value,
        state_order=state_order(SubTaskStatus.COMPLETED, attempt=1),
        attempt=1,
        worker_id="w1",
        progress=None,
        started_at_if_missing=None,
    )
    assert updated is True

    # running atrasado del mismo intento: state_order menor, se ignora
    updated_again = repos["subtasks"].update_progress(
        case_id,
        subtask_id,
        status=SubTaskStatus.RUNNING.value,
        state_order=state_order(SubTaskStatus.RUNNING, attempt=1),
        attempt=1,
        worker_id="w1",
        progress=50,
        started_at_if_missing=None,
    )
    assert updated_again is False

    subtask = repos["subtasks"].query_by_case(case_id)[0]
    assert subtask.status == SubTaskStatus.COMPLETED


def test_concurrent_close_finalizes_case_exactly_once(repos, aws) -> None:
    case_id = "concase"
    subtasks = _make_case_with_subtasks(repos, case_id, 2)

    kwargs = {
        "cases_repo": repos["cases"],
        "subtasks_repo": repos["subtasks"],
        "dynamodb": aws["dynamodb"],
        "s3": aws["s3"],
        "results_bucket": "test-results-bucket",
    }

    results = [_completed_result(case_id, st.subtask_id) for st in subtasks]

    threads = [
        threading.Thread(target=close_subtask, args=(result,), kwargs=kwargs)
        for result in results
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    case = repos["cases"].get(case_id, consistent=True)
    assert case.status == CaseStatus.COMPLETED
    assert case.pending_count == 0

    # Un único reporte en S3 (put_object determinístico a la misma key, no
    # duplicado ni corrupto).
    obj = aws["s3"].get_object(Bucket="test-results-bucket", Key=case.report_key)
    assert obj["Body"].read()  # el reporte tiene contenido


def test_crash_before_finalize_is_repaired_by_duplicate_close(
    repos, aws, monkeypatch
) -> None:
    """Simula que el proceso "murió" justo después de pending_count=0 pero antes
    de llamar a `finalize` (parcheamos `_maybe_finalize` para que no haga nada la
    primera vez). Un segundo `close_subtask` con un resultado duplicado (o el
    sweeper) tiene que reparar el barrier y finalizar el caso."""
    import coordinator.barrier.close as close_module

    case_id = "crashcase"
    subtasks = _make_case_with_subtasks(repos, case_id, 1)
    result = _completed_result(case_id, subtasks[0].subtask_id)

    monkeypatch.setattr(close_module, "_maybe_finalize", lambda *a, **k: None)

    close_subtask(
        result,
        cases_repo=repos["cases"],
        subtasks_repo=repos["subtasks"],
        dynamodb=aws["dynamodb"],
        s3=aws["s3"],
        results_bucket="test-results-bucket",
    )

    case = repos["cases"].get(case_id, consistent=True)
    assert case.pending_count == 0
    assert case.status != CaseStatus.COMPLETED  # todavía no finalizado

    # Ahora sin el monkeypatch: un duplicado del mismo resultado dispara el
    # camino de "resultado tardío", que sí revisa y repara el barrier.
    monkeypatch.undo()
    close_subtask(
        result,
        cases_repo=repos["cases"],
        subtasks_repo=repos["subtasks"],
        dynamodb=aws["dynamodb"],
        s3=aws["s3"],
        results_bucket="test-results-bucket",
    )

    repaired = repos["cases"].get(case_id, consistent=True)
    assert repaired.status == CaseStatus.COMPLETED


def test_cancellation_closes_case_as_cancelled(repos, aws) -> None:
    case_id = "cancelcase"
    subtasks = _make_case_with_subtasks(repos, case_id, 1)
    repos["cases"].update_cancel_requested(case_id)

    result = ResultMessage(
        subtask_id=subtasks[0].subtask_id,
        case_id=case_id,
        status=SubTaskStatus.CANCELLED,
        attempt=1,
        worker_id="w1",
        finished_at=utcnow(),
        error=ErrorInfo(code=ErrorCode.CANCELLED, message="cancelado"),
    )
    close_subtask(
        result,
        cases_repo=repos["cases"],
        subtasks_repo=repos["subtasks"],
        dynamodb=aws["dynamodb"],
        s3=aws["s3"],
        results_bucket="test-results-bucket",
    )

    case = repos["cases"].get(case_id, consistent=True)
    assert case.status == CaseStatus.CANCELLED


def test_concurrent_close_same_subtask_id_counts_once(repos, aws, monkeypatch) -> None:
    """Dos hilos reales cerrando el MISMO `subtask_id` (dos `ResultMessage`
    casi idénticos, como los duplicaría una entrega repetida de SQS Standard)
    al mismo tiempo: el barrier tiene que descontar `pending_count` una sola
    vez y el caso debe terminar cerrado con un único reporte, sin importar
    cuál de los dos hilos ganó la carrera.

    `moto` (a diferencia de DynamoDB real) no serializa correctamente dos
    `TransactWriteItems` concurrentes que compiten por EL MISMO ítem: bajo
    hilos de verdad a veces deja "ganar" a los dos (ver
    https://github.com/getmoto/moto - limitación conocida del emulador, no del
    código del coordinador, que no hace locking propio porque confía en la
    atomicidad real de DynamoDB). Para poder ejercitar la lógica de
    `close_subtask` con hilos genuinamente concurrentes sin heredar ese bug del
    emulador, serializamos acá solo la llamada cruda a `transact_write_items`
    contra el backend de moto con un lock; todo lo demás (arranque
    sincronizado con la barrera, armado de los updates, manejo de la
    excepción, `finalize`) sigue corriendo de verdad en dos hilos.
    """
    case_id = "samesubtaskcase"
    subtasks = _make_case_with_subtasks(repos, case_id, 1)
    result_a = _completed_result(case_id, subtasks[0].subtask_id)
    result_b = _completed_result(case_id, subtasks[0].subtask_id)

    write_lock = threading.Lock()
    original_transact_write_items = aws["dynamodb"].transact_write_items

    def _serialized_transact_write_items(*args, **kwargs):
        with write_lock:
            return original_transact_write_items(*args, **kwargs)

    monkeypatch.setattr(
        aws["dynamodb"], "transact_write_items", _serialized_transact_write_items
    )

    kwargs = {
        "cases_repo": repos["cases"],
        "subtasks_repo": repos["subtasks"],
        "dynamodb": aws["dynamodb"],
        "s3": aws["s3"],
        "results_bucket": "test-results-bucket",
    }

    barrier = threading.Barrier(2)

    def _run(result):
        barrier.wait(timeout=5)
        close_subtask(result, **kwargs)

    threads = [
        threading.Thread(target=_run, args=(result_a,)),
        threading.Thread(target=_run, args=(result_b,)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    case = repos["cases"].get(case_id, consistent=True)
    assert case.pending_count == 0  # se descontó una sola vez, no quedó en -1
    assert case.status == CaseStatus.COMPLETED

    # Un único reporte en la key determinística del caso.
    obj = aws["s3"].get_object(Bucket="test-results-bucket", Key=case.report_key)
    assert obj["Body"].read()


def test_close_subtask_with_nonexistent_subtask_logs_warning_not_debug(
    repos, aws, caplog
) -> None:
    """Un `ResultMessage` que referencia un `subtask_id` que nunca existió para
    ese caso es una referencia inválida (mensaje envenenado / bug de un
    worker), no un duplicado rutinario: tiene que descartarse sin romper nada
    pero logueando a nivel WARNING, no debug."""
    case_id = "ghostcase"
    repos["cases"].put_new(
        CaseItem(case_id=case_id, file_count=1, total=1, pending_count=1)
    )
    result = _completed_result(case_id, f"{case_id}-9999")

    with caplog.at_level(logging.DEBUG, logger="coordinator.barrier.close"):
        close_subtask(
            result,
            cases_repo=repos["cases"],
            subtasks_repo=repos["subtasks"],
            dynamodb=aws["dynamodb"],
            s3=aws["s3"],
            results_bucket="test-results-bucket",
        )

    warning_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("inexistente" in r.message for r in warning_records)
    assert not any(
        r.levelno >= logging.WARNING and "inexistente" not in r.message
        for r in caplog.records
    )

    # La escritura falló: el pending_count del caso no se tocó.
    case = repos["cases"].get(case_id, consistent=True)
    assert case.pending_count == 1


def test_finalize_is_idempotent_when_already_terminal(repos, aws) -> None:
    case_id = "idemcase"
    _make_case_with_subtasks(repos, case_id, 1)
    subtasks = repos["subtasks"].query_by_case(case_id)
    result = _completed_result(case_id, subtasks[0].subtask_id)

    close_subtask(
        result,
        cases_repo=repos["cases"],
        subtasks_repo=repos["subtasks"],
        dynamodb=aws["dynamodb"],
        s3=aws["s3"],
        results_bucket="test-results-bucket",
    )
    # Llamar finalize() de nuevo sobre un caso ya terminal no debe explotar ni
    # cambiar nada.
    status = finalize(
        case_id,
        cases_repo=repos["cases"],
        subtasks_repo=repos["subtasks"],
        s3=aws["s3"],
        results_bucket="test-results-bucket",
    )
    assert status is None
