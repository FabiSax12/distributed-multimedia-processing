"""ffprobe y ffmpeg como subprocesos.

El trabajo pesado corre en un proceso aparte, así que los hilos del worker no
compiten por el GIL: cada hilo solo espera a su ffmpeg. En EC2 los binarios
vienen del build estático que instala el `user_data` en `/usr/local/bin`.
"""

from __future__ import annotations

import json
import subprocess
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from shared.messages import ErrorCode

from .base import OpError

_PROBE_TIMEOUT_S = 60

# Formato de salida de audio -> argumentos de códec. Lo usan audio_extract
# (pool video) y audio_convert (pool audio).
AUDIO_CODECS: dict[str, list[str]] = {
    "mp3": ["-c:a", "libmp3lame", "-q:a", "2"],
    "ogg": ["-c:a", "libvorbis", "-q:a", "5"],
    "m4a": ["-c:a", "aac", "-b:a", "192k"],
    "flac": ["-c:a", "flac"],
    "wav": ["-c:a", "pcm_s16le"],
}


def probe(src: Path, timeout_s: float = _PROBE_TIMEOUT_S) -> dict[str, Any]:
    """ffprobe en JSON. Si no lo puede leer, el archivo está corrupto: el
    coordinador solo filtró por extensión."""
    try:
        proc = subprocess.run(
            [
                "ffprobe", "-v", "error", "-print_format", "json",
                "-show_format", "-show_streams", str(src),
            ],
            capture_output=True,
            text=True,
            timeout=min(timeout_s, _PROBE_TIMEOUT_S),
            check=False,  # el código de salida se revisa abajo
        )  # fmt: skip
    except subprocess.TimeoutExpired as exc:
        raise OpError(ErrorCode.TIMEOUT, "ffprobe superó el tiempo máximo") from exc
    info = json.loads(proc.stdout or "{}")
    if proc.returncode != 0 or not info.get("streams"):
        raise OpError(
            ErrorCode.CORRUPT_INPUT,
            f"ffprobe no pudo leer el archivo: {proc.stderr.strip() or 'sin streams'}",
        )
    return info


def duration_s(info: dict[str, Any]) -> float | None:
    try:
        return float(info["format"]["duration"])
    except (KeyError, TypeError, ValueError):
        return None


def has_stream(info: dict[str, Any], codec_type: str) -> bool:
    return any(s.get("codec_type") == codec_type for s in info["streams"])


def run(
    args: list[str],
    *,
    timeout_s: float,
    duration: float | None = None,
    on_progress: Callable[[int], None] | None = None,
) -> None:
    """Corre ffmpeg y traduce `-progress` a porcentaje (0-99).

    El 100 lo pone el resultado `completed`, no ffmpeg. Si pasa `timeout_s`,
    un Timer mata el proceso y se lanza `OpError(TIMEOUT)`.
    """
    cmd = [
        "ffmpeg", "-hide_banner", "-nostdin", "-y", "-loglevel", "error",
        "-progress", "pipe:1", "-nostats", *args,
    ]  # fmt: skip
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    timed_out = threading.Event()

    def _kill() -> None:
        timed_out.set()
        proc.kill()

    timer = threading.Timer(timeout_s, _kill)
    timer.start()
    last = -1
    try:
        # Con -loglevel error, stderr es chico y no llena el pipe mientras se
        # lee stdout línea por línea.
        for line in proc.stdout:  # type: ignore[union-attr]
            key, _, value = line.strip().partition("=")
            if key != "out_time_us" or not duration or on_progress is None:
                continue
            if value.isdigit():
                pct = min(99, int(int(value) / 1_000_000 / duration * 100))
                if pct > last:
                    last = pct
                    on_progress(pct)
        stderr = proc.stderr.read()  # type: ignore[union-attr]
        returncode = proc.wait()
    finally:
        timer.cancel()

    if timed_out.is_set():
        raise OpError(ErrorCode.TIMEOUT, f"ffmpeg superó {timeout_s:.0f} s")
    if returncode != 0:
        raise OpError(
            ErrorCode.PROCESSING_ERROR,
            f"ffmpeg terminó con código {returncode}: {stderr.strip()}",
        )
