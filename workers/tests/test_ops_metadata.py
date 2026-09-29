"""`ops/metadatos.py`: metadata, lyrics y classify (pool metadatos).

Las APIs externas (MusicBrainz y lyrics.ovh) se reemplazan con monkeypatch
sobre `_get_json`: ningún test sale a internet. Casos: catálogo encontrado,
catálogo caído (degrada, no falla), letra encontrada / no encontrada / API
caída, artista y título desde tags, `params` o nombre de archivo, y la
carpeta que arma classify por tipo.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from shared.messages import ErrorCode
from workers.ops import OPERATIONS
from workers.ops import metadatos as metadata_mod
from workers.ops.base import OpError

from .conftest import make_task

_MB_RESPONSE = {
    "recordings": [
        {
            "id": "b1a9c0e9-d987-4042-ae91-78d6a3267d69",
            "score": 100,
            "title": "Bohemian Rhapsody",
            "artist-credit": [{"name": "Queen"}],
            "first-release-date": "1975-10-31",
            "releases": [{"title": "A Night at the Opera"}],
        }
    ]
}


def _run(task, src: Path, out_dir: Path):
    return OPERATIONS[task.operation](
        task, src, out_dir, timeout_s=60, on_progress=lambda _: None
    )


def _fake_api(monkeypatch, responder) -> list[str]:
    urls: list[str] = []

    def fake(url: str) -> dict:
        urls.append(url)
        return responder(url)

    monkeypatch.setattr(metadata_mod, "_get_json", fake)
    monkeypatch.setattr(metadata_mod, "_MB_MIN_INTERVAL_S", 0)
    return urls


def test_metadata_audio_combines_ffprobe_and_catalog(
    media, tmp_path, monkeypatch
) -> None:
    urls = _fake_api(monkeypatch, lambda url: _MB_RESPONSE)

    output = _run(make_task("metadata", "audio"), media["audio"], tmp_path)

    [out] = output.files
    data = json.loads(out.read_text(encoding="utf-8"))
    assert out.name == "metadata.json"
    assert data["tags"]["artist"] == "Queen"  # de los tags del mp3
    assert data["duration_s"] == pytest.approx(2, abs=0.2)
    assert data["catalog"]["release"] == "A Night at the Opera"
    assert "musicbrainz.org" in urls[0]
    assert output.meta["catalog_match"] is True


def test_metadata_survives_catalog_outage(media, tmp_path, monkeypatch) -> None:
    def down(url: str) -> dict:
        raise OpError(ErrorCode.EXTERNAL_API_ERROR, "musicbrainz.org respondió 503")

    _fake_api(monkeypatch, down)

    output = _run(make_task("metadata", "audio"), media["audio"], tmp_path)

    # El catálogo es enriquecimiento: sin él, ffprobe igual responde.
    data = json.loads(output.files[0].read_text(encoding="utf-8"))
    assert "503" in data["catalog"]["error"]
    assert output.meta["catalog_match"] is False


def test_metadata_for_video_skips_catalog(media, tmp_path, monkeypatch) -> None:
    urls = _fake_api(monkeypatch, lambda url: _MB_RESPONSE)

    output = _run(make_task("metadata", "video"), media["video"], tmp_path)

    assert urls == []
    data = json.loads(output.files[0].read_text(encoding="utf-8"))
    assert {s["codec_type"] for s in data["streams"]} == {"video", "audio"}


def test_metadata_corrupt_input(media, tmp_path) -> None:
    with pytest.raises(OpError) as exc:
        _run(make_task("metadata", "video"), media["corrupt"], tmp_path)

    assert exc.value.code is ErrorCode.CORRUPT_INPUT


def test_lyrics_found(media, tmp_path, monkeypatch) -> None:
    urls = _fake_api(monkeypatch, lambda url: {"lyrics": "Is this the real life?"})

    output = _run(make_task("lyrics", "audio"), media["audio"], tmp_path)

    names = {f.name for f in output.files}
    assert names == {"lyrics.txt", "lyrics.json"}
    assert (tmp_path / "lyrics.txt").read_text(
        encoding="utf-8"
    ) == "Is this the real life?"
    assert urls == ["https://api.lyrics.ovh/v1/Queen/Bohemian%20Rhapsody"]
    assert output.meta == {
        "found": True,
        "artist": "Queen",
        "title": "Bohemian Rhapsody",
    }


def test_lyrics_not_found_still_completes(media, tmp_path, monkeypatch) -> None:
    def not_found(url: str) -> dict:
        raise metadata_mod.NotFound(url)

    _fake_api(monkeypatch, not_found)

    output = _run(make_task("lyrics", "audio"), media["audio"], tmp_path)

    assert [f.name for f in output.files] == ["lyrics.json"]
    assert output.meta["found"] is False


def test_lyrics_api_down_fails(media, tmp_path, monkeypatch) -> None:
    def down(url: str) -> dict:
        raise OpError(ErrorCode.EXTERNAL_API_ERROR, "timeout")

    _fake_api(monkeypatch, down)

    with pytest.raises(OpError) as exc:
        _run(make_task("lyrics", "audio"), media["audio"], tmp_path)

    assert exc.value.code is ErrorCode.EXTERNAL_API_ERROR


def test_lyrics_params_override_tags(media, tmp_path, monkeypatch) -> None:
    urls = _fake_api(monkeypatch, lambda url: {"lyrics": "..."})

    task = make_task("lyrics", "audio", artist="Freddie Mercury", title="Love Kills")
    _run(task, media["audio"], tmp_path)

    assert urls == ["https://api.lyrics.ovh/v1/Freddie%20Mercury/Love%20Kills"]


def test_lyrics_falls_back_to_file_name(media, tmp_path, monkeypatch) -> None:
    urls = _fake_api(monkeypatch, lambda url: {"lyrics": "..."})

    # El audio del clip de prueba no trae tags: queda "Artista - Título" del nombre.
    task = make_task(
        "lyrics", "audio", input_key="uploads/u1/Soda Stereo - De Música Ligera.mp3"
    )
    _run(task, media["video"], tmp_path)

    assert urls == ["https://api.lyrics.ovh/v1/Soda%20Stereo/De%20M%C3%BAsica%20Ligera"]


@pytest.mark.parametrize(
    ("key", "media_type", "folder"),
    [
        ("video", "video", "video/sd/corto"),
        ("audio", "audio", "audio/rock/corto"),
        ("image", "image", "image/720p/horizontal"),
    ],
)
def test_classify_builds_folder_by_type(
    media, tmp_path, key, media_type, folder
) -> None:
    output = _run(make_task("classify", media_type), media[key], tmp_path)

    data = json.loads(output.files[0].read_text(encoding="utf-8"))
    assert output.files[0].name == "classification.json"
    assert data["folder"] == folder
    assert output.meta["folder"] == folder
