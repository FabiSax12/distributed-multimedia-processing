"""`/api/cases`: crear casos, listarlos, verlos en detalle, cancelarlos y pedir su reporte.

Todos los endpoints que llaman a boto3/DynamoDB se declaran `def` (no
`async def`) a propósito: FastAPI los corre en su threadpool en vez de en el
loop de asyncio, así una llamada bloqueante a boto3 no traba el resto de la
API mientras espera respuesta de AWS.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from botocore.exceptions import ClientError
from fastapi import APIRouter, HTTPException
from shared.api import (
    CaseDetailResponse,
    CaseOutputsResponse,
    CreateCaseRequest,
    CreateCaseResponse,
    ReportUrlResponse,
    SubTaskOutputs,
    SubTaskOutputUrl,
)
from shared.models import CaseItem, report_key
from shared.states import TERMINAL_CASE, CaseStatus
from ulid import ULID

from ..barrier.finalize import finalize
from ..repo.cases import CaseAlreadyTerminal, CaseNotFound
from ..routing.enqueue import enqueue_case
from ..routing.planner import plan_case
from .deps import CasesRepoDep, S3Dep, SettingsDep, SqsDep, SubTasksRepoDep

logger = logging.getLogger(__name__)

router = APIRouter(tags=["cases"])

_HEAD_OBJECT_NOT_FOUND_CODES = {"404", "NoSuchKey", "NotFound"}


@router.post("/cases", response_model=CreateCaseResponse, status_code=201)
def create_case(
    payload: CreateCaseRequest,
    settings: SettingsDep,
    s3: S3Dep,
    sqs: SqsDep,
    cases_repo: CasesRepoDep,
    subtasks_repo: SubTasksRepoDep,
) -> CreateCaseResponse:
    input_keys = [f.input_key for f in payload.files]
    missing = _missing_input_keys(s3, settings.DATASET_BUCKET, input_keys)
    if missing:
        raise HTTPException(status_code=422, detail={"missing": missing})

    case_id = str(ULID())
    subtasks, prefailed_count = plan_case(payload.files, case_id, payload.priority)
    total = len(subtasks)
    pending_count = total - prefailed_count

    # Outbox: sub-tareas (BatchWriteItem) -> caso (PutItem) -> encolado (SQS).
    # Si el proceso muere entre el BatchWriteItem y el PutItem del caso, quedan
    # sub-tareas huérfanas en DynamoDB sin un `Cases` que las referencie; como
    # nunca llegan a encolarse (el PutItem del caso, que pasa después, no se
    # ejecutó), nadie las procesa ni reporta resultados sobre ellas — quedan
    # como basura inerte hasta una limpieza manual. Aceptable: el cliente
    # reintenta el POST con un `case_id` nuevo.
    subtasks_repo.batch_put_planned(subtasks)
    cases_repo.put_new(
        CaseItem(
            case_id=case_id,
            label=payload.label,
            source=payload.source,
            priority=payload.priority,
            file_count=len(payload.files),
            total=total,
            pending_count=pending_count,
            failed_count=prefailed_count,
        )
    )

    try:
        enqueue_case(
            subtasks,
            sqs=sqs,
            queue_urls=settings.QUEUE_URLS,
            subtasks_repo=subtasks_repo,
        )
    except Exception as exc:
        # El caso y sus sub-tareas ya están persistidos en DynamoDB (outbox de
        # arriba); si el encolado falla POR COMPLETO (no el caso "Failed"
        # parcial que `enqueue_case` ya maneja sola, sino una excepción que se
        # propaga, p. ej. SQS caído), el cliente no debe perder el `case_id`:
        # el barrido periódico (`barrier/sweeper.py`) va a reencolar las
        # sub-tareas que hayan quedado `enqueued=false`.
        logger.exception(
            "enqueue_case falló completamente para el caso %s, el barrido de "
            "recuperación lo va a reintentar",
            case_id,
        )
        raise HTTPException(
            status_code=500,
            detail={
                "case_id": case_id,
                "message": (
                    "el caso se creó pero no se pudo encolar completamente; "
                    "el barrido de recuperación lo va a reintentar"
                ),
            },
        ) from exc

    if pending_count == 0:
        # Todas las sub-tareas nacieron prefallidas (formatos no soportados):
        # ningún worker va a mandar un ResultMessage que dispare el barrier,
        # así que cerramos el caso acá mismo, en el mismo request.
        finalize(
            case_id,
            cases_repo=cases_repo,
            subtasks_repo=subtasks_repo,
            s3=s3,
            results_bucket=settings.RESULTS_BUCKET,
        )

    return CreateCaseResponse(
        case_id=case_id,
        file_count=len(payload.files),
        subtask_count=total,
        prefailed_count=prefailed_count,
    )


def _missing_input_keys(s3: Any, bucket: str, input_keys: list[str]) -> list[str]:
    """`head_object` de cada `input_key`, en paralelo (I/O-bound, no CPU-bound)."""

    def _check(key: str) -> str | None:
        try:
            s3.head_object(Bucket=bucket, Key=key)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code in _HEAD_OBJECT_NOT_FOUND_CODES:
                return key
            raise
        return None

    with ThreadPoolExecutor(max_workers=min(16, len(input_keys))) as pool:
        results = list(pool.map(_check, input_keys))
    return [key for key in results if key is not None]


@router.get("/cases", response_model=list[CaseItem])
def list_cases(
    cases_repo: CasesRepoDep,
    status: CaseStatus | None = None,
    limit: int = 50,
) -> list[CaseItem]:
    return cases_repo.list_recent(status.value if status else None, limit)


@router.get("/cases/{case_id}", response_model=CaseDetailResponse)
def get_case(
    case_id: str,
    cases_repo: CasesRepoDep,
    subtasks_repo: SubTasksRepoDep,
) -> CaseDetailResponse:
    case = cases_repo.get(case_id, consistent=True)
    if case is None:
        raise HTTPException(status_code=404, detail="caso no encontrado")
    subtasks = subtasks_repo.query_by_case(case_id)
    return CaseDetailResponse(case=case, subtasks=subtasks)


@router.delete("/cases/{case_id}", response_model=CaseItem)
def cancel_case(case_id: str, cases_repo: CasesRepoDep) -> CaseItem:
    try:
        return cases_repo.update_cancel_requested(case_id)
    except CaseNotFound as exc:
        raise HTTPException(status_code=404, detail="caso no encontrado") from exc
    except CaseAlreadyTerminal as exc:
        raise HTTPException(
            status_code=409, detail="el caso ya llegó a un estado terminal"
        ) from exc


@router.get("/cases/{case_id}/report", response_model=ReportUrlResponse)
def get_case_report(
    case_id: str,
    settings: SettingsDep,
    s3: S3Dep,
    cases_repo: CasesRepoDep,
) -> ReportUrlResponse:
    case = cases_repo.get(case_id, consistent=True)
    if case is None:
        raise HTTPException(status_code=404, detail="caso no encontrado")
    if case.status not in TERMINAL_CASE:
        raise HTTPException(status_code=409, detail="el caso todavía no terminó")

    url = s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.RESULTS_BUCKET, "Key": report_key(case_id)},
        ExpiresIn=settings.PRESIGN_EXPIRES_S,
    )
    return ReportUrlResponse(
        case_id=case_id, url=url, expires_in=settings.PRESIGN_EXPIRES_S
    )


@router.get("/cases/{case_id}/outputs", response_model=CaseOutputsResponse)
def get_case_outputs(
    case_id: str,
    settings: SettingsDep,
    s3: S3Dep,
    cases_repo: CasesRepoDep,
    subtasks_repo: SubTasksRepoDep,
) -> CaseOutputsResponse:
    """Presigna los `output_keys` de cada sub-tarea, sin exigir que el caso haya terminado.

    A diferencia de `get_case_report`, una sub-tarea individual puede tener
    salidas listas mientras otras del mismo caso siguen procesándose.
    """
    case = cases_repo.get(case_id, consistent=True)
    if case is None:
        raise HTTPException(status_code=404, detail="caso no encontrado")

    subtasks = subtasks_repo.query_by_case(case_id)
    result = [
        SubTaskOutputs(
            subtask_id=s.subtask_id,
            outputs=[
                SubTaskOutputUrl(
                    key=k,
                    url=s3.generate_presigned_url(
                        "get_object",
                        Params={"Bucket": settings.RESULTS_BUCKET, "Key": k},
                        ExpiresIn=settings.PRESIGN_EXPIRES_S,
                    ),
                    expires_in=settings.PRESIGN_EXPIRES_S,
                )
                for k in s.output_keys
            ],
        )
        for s in subtasks
        if s.output_keys
    ]
    return CaseOutputsResponse(case_id=case_id, subtasks=result)
