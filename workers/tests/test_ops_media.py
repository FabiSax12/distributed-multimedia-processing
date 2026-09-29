"""`ops/video.py`, `ops/audio.py` y `ops/ffmpeg.py` con ffmpeg real.

Cubre: salida válida por operación, `params` opcionales, formatos no
soportados, archivo corrupto, video sin pista de audio, progreso y timeout.
Cada error tiene que salir como `OpError` con el `ErrorCode` que termina en el
reporte del caso.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from shared.messages import ErrorCode
from workers.ops import OPERATIONS
from workers.ops.base import OpError
from workers.ops.ffmpeg import probe

from .conftest import make_task


def _run(operation: str, media_type: str, src: Path, out_dir: Path, **params):
    task = make_task(operation, media_type, **params)
    progress: list[int] = []
    output = OPERATIONS[task.operation](
        task, src, out_dir, timeout_s=60, on_progress=progress.append
    )
    return output, progress


def _streams(path: Path) -> dict[str, dict]:
    return {s["codec_type"]: s for s in probe(path)["streams"]}


def test_probe_rejects_corrupt_file(media) -> None:
    with pytest.raises(OpError) as exc:
        probe(media["corrupt"])

    assert exc.value.code is ErrorCode.CORRUPT_INPUT


def test_video_convert_defaults_to_mp4(media, tmp_path) -> None:
    output, progress = _run("video_convert", "video", media["video"], tmp_path)

    [out] = output.files
    assert out.suffix == ".mp4"
    assert {"video", "audio"} <= _streams(out).keys()
    assert output.meta["target_format"] == "mp4"
    # ffmpeg reporta out_time_us: el progreso sube y nunca llega a 100 antes
    # del resultado terminal.
    assert progress and progress == sorted(progress) and max(progress) <= 99


def test_video_convert_to_webm(media, tmp_path) -> None:
    output, _ = _run(
        "video_convert", "video", media["video"], tmp_path, target_format="webm"
    )

    assert output.files[0].suffix == ".webm"
    assert _streams(output.files[0])["video"]["codec_name"] == "vp9"


def test_video_convert_unknown_format_is_unsupported(media, tmp_path) -> None:
    with pytest.raises(OpError) as exc:
        _run("video_convert", "video", media["video"], tmp_path, target_format="exe")

    assert exc.value.code is ErrorCode.UNSUPPORTED_FORMAT


def test_video_convert_corrupt_input(media, tmp_path) -> None:
    with pytest.raises(OpError) as exc:
        _run("video_convert", "video", media["corrupt"], tmp_path)

    assert exc.value.code is ErrorCode.CORRUPT_INPUT


def test_audio_extract_to_mp3(media, tmp_path) -> None:
    output, _ = _run("audio_extract", "video", media["video"], tmp_path)

    [out] = output.files
    assert out.suffix == ".mp3"
    assert _streams(out).keys() == {"audio"}


def test_audio_extract_without_audio_track_fails(media, tmp_path) -> None:
    with pytest.raises(OpError) as exc:
        _run("audio_extract", "video", media["silent_video"], tmp_path)

    assert exc.value.code is ErrorCode.PROCESSING_ERROR


def test_video_thumbnail_is_a_scaled_jpeg(media, tmp_path) -> None:
    output, _ = _run(
        "video_thumbnail", "video", media["video"], tmp_path, thumbnail_width=160
    )

    [out] = output.files
    assert out.name == "thumbnail.jpg"
    assert _streams(out)["video"]["width"] == 160


def test_audio_convert_to_ogg(media, tmp_path) -> None:
    output, _ = _run(
        "audio_convert", "audio", media["audio"], tmp_path, target_format="ogg"
    )

    [out] = output.files
    assert out.suffix == ".ogg"
    assert _streams(out)["audio"]["codec_name"] == "vorbis"


def test_image_thumbnail_defaults_to_320px(media, tmp_path) -> None:
    output, _ = _run("image_thumbnail", "image", media["image"], tmp_path)

    [out] = output.files
    assert _streams(out)["video"]["width"] == 320
    assert output.meta["source_width"] == 1280


def test_operation_over_its_time_limit_times_out(media, tmp_path) -> None:
    task = make_task("video_convert", "video")

    with pytest.raises(OpError) as exc:
        OPERATIONS[task.operation](
            task, media["video"], tmp_path, timeout_s=0.001, on_progress=lambda _: None
        )

    assert exc.value.code is ErrorCode.TIMEOUT
