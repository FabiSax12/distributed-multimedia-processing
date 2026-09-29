"""Tipos comunes a todas las operaciones.

Una operación recibe el `SubTaskMessage`, el archivo de entrada ya descargado
y un directorio de salida vacío, y devuelve los archivos a subir más un dict
de metadatos que viaja en `ResultMessage.output_meta`. No toca AWS: el runner
descarga, sube y reporta.

Los errores esperables (archivo corrupto, formato pedido que no existe, API
externa caída) se lanzan como `OpError` con su `ErrorCode`: el runner los
reporta como `failed` y borra el mensaje. Cualquier otra excepción es un bug o
una falla transitoria y se deja reintentar por SQS.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from shared.messages import ErrorCode, SubTaskMessage


@dataclass(frozen=True)
class OpOutput:
    files: list[Path]
    meta: dict[str, Any] = field(default_factory=dict)


class OpError(Exception):
    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message[-2000:]  # ErrorInfo.message tiene max_length=2000


class Operation(Protocol):
    def __call__(
        self,
        task: SubTaskMessage,
        src: Path,
        out_dir: Path,
        *,
        timeout_s: float,
        on_progress: Callable[[int], None],
    ) -> OpOutput: ...


def output_name(task: SubTaskMessage, extension: str) -> str:
    """`uploads/u1/Mi clip.mov` + `mp4` -> `Mi clip.mp4`."""
    return f"{Path(task.input_key).stem}.{extension}"


def choose(params: dict[str, Any], key: str, default: str, allowed: set[str]) -> str:
    value = str(params.get(key, default)).lower().lstrip(".")
    if value not in allowed:
        raise OpError(
            ErrorCode.UNSUPPORTED_FORMAT,
            f"{key}={value!r} no soportado, opciones: {sorted(allowed)}",
        )
    return value
