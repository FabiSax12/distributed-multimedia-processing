"""Routing por tipo: archivo -> tipo de medio -> operación(es) -> pool -> cola.

Esta es la tabla que conviene mostrar en el documento de arquitectura.

Un archivo puede producir varias sub-tareas (un video: conversión + audio +
miniatura), así que el N del barrier es el número de sub-tareas, no de archivos.

Archivos con extensión desconocida u operación no válida para su tipo: el
coordinador crea la sub-tarea ya en `failed` (sin encolarla) con
UNSUPPORTED_FORMAT, y arranca el caso con pending_count = N - prefallidas y
failed_count = prefallidas. Así aparecen en el reporte ("2 fallidos por formato
no soportado") sin gastar un worker.
"""

from enum import StrEnum
from pathlib import PurePosixPath


class MediaType(StrEnum):
    VIDEO = "video"
    AUDIO = "audio"
    IMAGE = "image"


class Operation(StrEnum):
    VIDEO_CONVERT = "video_convert"      # conversión de formato de video
    AUDIO_EXTRACT = "audio_extract"      # extracción de audio desde video
    VIDEO_THUMBNAIL = "video_thumbnail"  # portada / miniatura desde un frame
    AUDIO_CONVERT = "audio_convert"      # transcodificación de audio
    IMAGE_THUMBNAIL = "image_thumbnail"  # miniatura de imagen
    METADATA = "metadata"                # ffprobe + catálogos externos
    LYRICS = "lyrics"                    # letras desde API externa
    CLASSIFY = "classify"                # clasificación / organización del resultado


class Pool(StrEnum):
    VIDEO = "video"          # c7i-flex.large · ffmpeg, cómputo intensivo
    AUDIO = "audio"          # m7i-flex.large · ffmpeg liviano, imágenes
    METADATA = "metadatos"   # t3.micro · ligado a red, no a CPU


class Priority(StrEnum):
    HIGH = "alta"
    NORMAL = "normal"


EXTENSIONS: dict[str, MediaType] = {
    ".mp4": MediaType.VIDEO,
    ".mkv": MediaType.VIDEO,
    ".mov": MediaType.VIDEO,
    ".avi": MediaType.VIDEO,
    ".webm": MediaType.VIDEO,
    ".mp3": MediaType.AUDIO,
    ".wav": MediaType.AUDIO,
    ".flac": MediaType.AUDIO,
    ".ogg": MediaType.AUDIO,
    ".m4a": MediaType.AUDIO,
    ".aac": MediaType.AUDIO,
    ".jpg": MediaType.IMAGE,
    ".jpeg": MediaType.IMAGE,
    ".png": MediaType.IMAGE,
    ".webp": MediaType.IMAGE,
}

# Operaciones válidas por tipo de medio.
ALLOWED_OPERATIONS: dict[MediaType, frozenset[Operation]] = {
    MediaType.VIDEO: frozenset(
        {
            Operation.VIDEO_CONVERT,
            Operation.AUDIO_EXTRACT,
            Operation.VIDEO_THUMBNAIL,
            Operation.METADATA,
            Operation.CLASSIFY,
        }
    ),
    MediaType.AUDIO: frozenset(
        {Operation.AUDIO_CONVERT, Operation.METADATA, Operation.LYRICS, Operation.CLASSIFY}
    ),
    MediaType.IMAGE: frozenset(
        {Operation.IMAGE_THUMBNAIL, Operation.METADATA, Operation.CLASSIFY}
    ),
}

# Operaciones que el coordinador elige cuando el caso no las especifica.
DEFAULT_OPERATIONS: dict[MediaType, tuple[Operation, ...]] = {
    MediaType.VIDEO: (Operation.VIDEO_CONVERT, Operation.AUDIO_EXTRACT, Operation.VIDEO_THUMBNAIL),
    MediaType.AUDIO: (Operation.AUDIO_CONVERT, Operation.METADATA),
    MediaType.IMAGE: (Operation.IMAGE_THUMBNAIL,),
}

# Pool dueño de cada operación (heterogeneidad de cómputo, Unidad 1).
OPERATION_POOL: dict[Operation, Pool] = {
    Operation.VIDEO_CONVERT: Pool.VIDEO,
    Operation.AUDIO_EXTRACT: Pool.VIDEO,
    Operation.VIDEO_THUMBNAIL: Pool.VIDEO,
    Operation.AUDIO_CONVERT: Pool.AUDIO,
    Operation.IMAGE_THUMBNAIL: Pool.AUDIO,
    Operation.METADATA: Pool.METADATA,
    Operation.LYRICS: Pool.METADATA,
    Operation.CLASSIFY: Pool.METADATA,
}


def queue_key(pool: Pool, priority: Priority) -> str:
    """Nombre lógico de la cola, p. ej. 'video-alta'. La URL real la da Terraform."""
    return f"{pool.value}-{priority.value}"


ALL_WORK_QUEUES: tuple[str, ...] = tuple(queue_key(p, pr) for p in Pool for pr in Priority)
RESULTS_QUEUE = "resultados"
DLQ = "dlq"

# Orden de consumo de cada pool: prioridad estricta y, si sus colas están
# vacías, ayuda a otro tipo que su máquina pueda atender (Diagrama 4).
POLL_ORDER: dict[Pool, tuple[str, ...]] = {
    Pool.VIDEO: ("video-alta", "video-normal", "audio-normal"),
    Pool.AUDIO: (
        "audio-alta",
        "audio-normal",
        "metadatos-alta",
        "metadatos-normal",
        "video-normal",
    ),
    Pool.METADATA: ("metadatos-alta", "metadatos-normal", "audio-normal"),
}


class UnsupportedFile(ValueError):
    """Extensión desconocida u operación no válida para el tipo de archivo."""


def detect_media_type(filename: str) -> MediaType | None:
    return EXTENSIONS.get(PurePosixPath(filename).suffix.lower())


def plan_operations(filename: str, requested: list[Operation] | None = None) -> list[Operation]:
    """Operaciones que se van a ejecutar sobre un archivo.

    Lanza UnsupportedFile si el tipo no se reconoce o si alguna operación pedida
    no aplica a ese tipo. El worker igual valida con ffprobe: un .mp4 corrupto
    pasa este filtro y falla en el worker.
    """
    media = detect_media_type(filename)
    if media is None:
        raise UnsupportedFile(f"extensión no soportada: {filename}")
    if not requested:
        return list(DEFAULT_OPERATIONS[media])
    invalid = [op for op in requested if op not in ALLOWED_OPERATIONS[media]]
    if invalid:
        raise UnsupportedFile(f"operaciones no válidas para {media}: {invalid}")
    return list(dict.fromkeys(requested))  # sin duplicados, en orden