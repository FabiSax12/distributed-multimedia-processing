"""Operaciones del pool de video (c7i-flex.large): cómputo intensivo con ffmpeg.

`params` opcionales (el coordinador los copia tal cual de `CaseFileIn.params`):
- video_convert:   `target_format` (mp4 | mkv | mov | webm), por defecto mp4.
- audio_extract:   `audio_format` (mp3 | ogg | m4a | flac | wav), por defecto mp3.
- video_thumbnail: `thumbnail_width` en px, por defecto 320.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from shared.messages import ErrorCode, SubTaskMessage

from . import ffmpeg
from .base import OpError, OpOutput, choose, output_name

# libx264 "veryfast": en 2 vCPU la diferencia de tamaño contra "medium" no
# compensa triplicar el tiempo. webm usa VP9 en modo realtime por lo mismo.
_VIDEO_CODECS: dict[str, list[str]] = {
    "mp4": ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart"],
    "mov": ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-pix_fmt", "yuv420p", "-c:a", "aac"],
    "mkv": ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-c:a", "aac"],
    "webm": ["-c:v", "libvpx-vp9", "-deadline", "realtime", "-cpu-used", "8",
             "-b:v", "0", "-crf", "35", "-c:a", "libopus"],
}  # fmt: skip

_DEFAULT_THUMBNAIL_WIDTH = 320


def video_convert(
    task: SubTaskMessage,
    src: Path,
    out_dir: Path,
    *,
    timeout_s: float,
    on_progress: Callable[[int], None],
) -> OpOutput:
    fmt = choose(task.params, "target_format", "mp4", set(_VIDEO_CODECS))
    info = ffmpeg.probe(src, timeout_s)
    duration = ffmpeg.duration_s(info)
    out = out_dir / output_name(task, fmt)
    ffmpeg.run(
        ["-i", str(src), *_VIDEO_CODECS[fmt], str(out)],
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


def audio_extract(
    task: SubTaskMessage,
    src: Path,
    out_dir: Path,
    *,
    timeout_s: float,
    on_progress: Callable[[int], None],
) -> OpOutput:
    fmt = choose(task.params, "audio_format", "mp3", set(ffmpeg.AUDIO_CODECS))
    info = ffmpeg.probe(src, timeout_s)
    if not ffmpeg.has_stream(info, "audio"):
        raise OpError(ErrorCode.PROCESSING_ERROR, "el video no tiene pista de audio")
    duration = ffmpeg.duration_s(info)
    out = out_dir / output_name(task, fmt)
    ffmpeg.run(
        ["-i", str(src), "-vn", *ffmpeg.AUDIO_CODECS[fmt], str(out)],
        timeout_s=timeout_s,
        duration=duration,
        on_progress=on_progress,
    )
    return OpOutput(
        [out],
        {
            "audio_format": fmt,
            "duration_ms": ffmpeg.ms(duration),
            "size_bytes": out.stat().st_size,
        },
    )


def video_thumbnail(
    task: SubTaskMessage,
    src: Path,
    out_dir: Path,
    *,
    timeout_s: float,
    on_progress: Callable[[int], None],
) -> OpOutput:
    width = int(task.params.get("thumbnail_width", _DEFAULT_THUMBNAIL_WIDTH))
    info = ffmpeg.probe(src, timeout_s)
    if not ffmpeg.has_stream(info, "video"):
        raise OpError(ErrorCode.PROCESSING_ERROR, "el archivo no tiene pista de video")
    # Un frame al 10 % de la duración: el primer frame suele ser negro.
    at = (ffmpeg.duration_s(info) or 0) * 0.1
    out = out_dir / "thumbnail.jpg"
    ffmpeg.run(
        ["-ss", f"{at:.2f}", "-i", str(src), "-frames:v", "1",
         "-vf", f"scale={width}:-2", "-q:v", "3", str(out)],
        timeout_s=timeout_s,
    )  # fmt: skip
    return OpOutput([out], {"at_ms": ffmpeg.ms(at), "width": width})
