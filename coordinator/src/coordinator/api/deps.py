"""Dependencias de FastAPI compartidas por los routers de `api/`.

Antes, `cases.py`/`uploads.py`/`dataset.py` reconstruían sus repos/clients a
mano dentro de cada handler (factories privadas tipo `_cases_repo()`, o
llamando directo a `..aws.s3_client()`). Este módulo centraliza eso en
funciones de dependencia para que los routers los reciban vía
`fastapi.Depends()`, como el resto de una app FastAPI idiomática. Las
funciones de acá siguen usando los singletons cacheados de `..aws`/
`..config` — lo único que cambia es CÓMO llegan al handler.

Se exponen como alias `Annotated[T, Depends(...)]` (el estilo recomendado por
FastAPI) en vez de `= Depends(...)` como default de parámetro: además de ser
más corto en cada router, evita que `ruff` (regla B008) marque cada uso como
"llamada a función en un default de argumento" — acá no hay ningún default,
`Depends(...)` vive en la anotación de tipo, no en el valor por default.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends

from ..aws import dynamodb_client, s3_client, sqs_client
from ..config import Settings, get_settings
from ..repo.cases import CasesRepo
from ..repo.subtasks import SubTasksRepo
from ..repo.workers import WorkersRepo


def get_s3() -> Any:
    return s3_client()


def get_sqs() -> Any:
    return sqs_client()


def get_dynamodb() -> Any:
    return dynamodb_client()


SettingsDep = Annotated[Settings, Depends(get_settings)]
S3Dep = Annotated[Any, Depends(get_s3)]
SqsDep = Annotated[Any, Depends(get_sqs)]
DynamoDbDep = Annotated[Any, Depends(get_dynamodb)]


def get_cases_repo(settings: SettingsDep) -> CasesRepo:
    return CasesRepo(dynamodb_client(), settings.TABLE_CASES)


def get_subtasks_repo(settings: SettingsDep) -> SubTasksRepo:
    return SubTasksRepo(dynamodb_client(), settings.TABLE_SUBTASKS)


def get_workers_repo(settings: SettingsDep) -> WorkersRepo:
    return WorkersRepo(dynamodb_client(), settings.TABLE_WORKERS)


CasesRepoDep = Annotated[CasesRepo, Depends(get_cases_repo)]
SubTasksRepoDep = Annotated[SubTasksRepo, Depends(get_subtasks_repo)]
WorkersRepoDep = Annotated[WorkersRepo, Depends(get_workers_repo)]
