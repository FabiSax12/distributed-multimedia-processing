"""Operaciones del pool de audio (m7i-flex.large): ffmpeg liviano e imágenes.

`params` opcionales:
- audio_convert:   `target_format` (mp3 | ogg | m4a | flac | wav), por defecto mp3.
- image_thumbnail: `thumbnail_width` en px, por defecto 320.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from shared.messages import ErrorCode, SubTaskMessage

from . import ffmpeg
from .base import OpError, OpOutput, choose, output_name

_DEFAULT_THUMBNAIL_WIDTH = 320


def audio_convert(
    task: SubTaskMessage,
    src: Path,
    out_dir: Path,
    *,
    timeout_s: float,
    on_progress: Callable[[int], None],
) -> OpOutput:
    fmt = choose(task.params, "target_format", "mp3", set(ffmpeg.AUDIO_CODECS))
    info = ffmpeg.probe(src, timeout_s)
    if not ffmpeg.has_stream(info, "audio"):
        raise OpError(ErrorCode.CORRUPT_INPUT, "el archivo no tiene pista de audio")
    duration = ffmpeg.duration_s(info)
    out = out_dir / output_name(task, fmt)
    # -map 0:a: descarta la portada embebida (stream de video en muchos mp3).
    ffmpeg.run(
        ["-i", str(src), "-map", "0:a:0", *ffmpeg.AUDIO_CODECS[fmt], str(out)],
        timeout_s=timeout_s,
        duration=duration,
        on_progress=on_progress,
    )
    return OpOutput(
        [out],
        {
            "target_format": fmt,
            "duration_ms": ffmpeg.ms(duration),
            "size_bytes": out.stat().st_size,
        },
    )


def image_thumbnail(
    task: SubTaskMessage,
    src: Path,
    out_dir: Path,
    *,
    timeout_s: float,
    on_progress: Callable[[int], None],
) -> OpOutput:
    width = int(task.params.get("thumbnail_width", _DEFAULT_THUMBNAIL_WIDTH))
    info = ffmpeg.probe(src, timeout_s)
    video = next(s for s in info["streams"] if s.get("codec_type") == "video")
    out = out_dir / "thumbnail.jpg"
    ffmpeg.run(
        ["-i", str(src), "-frames:v", "1", "-vf", f"scale={width}:-2",
         "-q:v", "3", str(out)],
        timeout_s=timeout_s,
    )  # fmt: skip
    return OpOutput(
        [out],
        {
            "width": width,
            "source_width": video.get("width"),
            "source_height": video.get("height"),
        },
    )
