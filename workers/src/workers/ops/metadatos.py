"""Operaciones del pool de metadatos (t3.micro): ligadas a red, no a CPU.

- metadata: ffprobe (formato, duración, streams, tags) y, para audio, la
  grabación que más se parece en MusicBrainz. El catálogo es enriquecimiento:
  si no responde, el resultado sale igual con `catalog.error`.
- lyrics: letra desde lyrics.ovh. Sin letra para la canción sigue siendo un
  resultado válido (`found: false`); la API caída es `external_api_error`.
- classify: categoría y carpeta sugerida (`video/1080p/largo`,
  `audio/rock/corto`, `image/720p/vertical`) a partir de ffprobe.

Artista y título salen, en este orden, de `params` (el JSON del dataset), de
los tags del archivo o del nombre `Artista - Título.ext`.

Ninguna de las dos APIs pide llave. MusicBrainz pide un User-Agent que
identifique la aplicación y como mucho una consulta por segundo por IP; el
lock de abajo lo respeta aunque corran varios hilos.
"""

from __future__ import annotations

import json
import re
import threading
import time
import unicodedata
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import Request, urlopen

from shared.messages import ErrorCode, SubTaskMessage
from shared.routing import MediaType

from . import ffmpeg
from .base import OpError, OpOutput

_USER_AGENT = (
    "dmp-ic6600/0.1 (+https://github.com/FabiSax12/distributed-multimedia-processing)"
)
_HTTP_TIMEOUT_S = 10
_MUSICBRAINZ_URL = "https://musicbrainz.org/ws/2/recording"
_LYRICS_URL = "https://api.lyrics.ovh/v1"

_MB_MIN_INTERVAL_S = 1.1
_mb_lock = threading.Lock()
_mb_last_call = 0.0

_LOSSLESS_CODECS = {"flac", "alac", "wavpack", "ape"}


class NotFound(Exception):
    """La API respondió 404: no hay datos para esa consulta."""


def _get_json(url: str) -> dict[str, Any]:
    request = Request(url, headers={"User-Agent": _USER_AGENT, "Accept": "application/json"})
    host = urlparse(url).netloc
    try:
        with urlopen(request, timeout=_HTTP_TIMEOUT_S) as resp:
            return json.load(resp)
    except HTTPError as exc:
        if exc.code == 404:
            raise NotFound(url) from exc
        raise OpError(ErrorCode.EXTERNAL_API_ERROR, f"{host} respondió {exc.code}") from exc
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise OpError(ErrorCode.EXTERNAL_API_ERROR, f"{host}: {exc}") from exc


def _tags(info: dict[str, Any]) -> dict[str, str]:
    """Tags del contenedor y de los streams (ogg/opus los guardan en el stream),
    con las claves en minúscula."""
    tags: dict[str, str] = {}
    for source in (*info["streams"], info.get("format", {})):
        for key, value in (source.get("tags") or {}).items():
            tags.setdefault(key.lower(), value)
    return tags


def artist_title(task: SubTaskMessage, info: dict[str, Any]) -> tuple[str | None, str | None]:
    tags = _tags(info)
    artist = task.params.get("artist") or tags.get("artist")
    title = task.params.get("title") or tags.get("title")
    stem = Path(task.input_key).stem
    if (not artist or not title) and " - " in stem:
        name_artist, name_title = (part.strip() for part in stem.split(" - ", 1))
        artist, title = artist or name_artist, title or name_title
    return artist, title


def _summary(info: dict[str, Any]) -> dict[str, Any]:
    fmt = info.get("format", {})
    streams = [
        {
            key: s[key]
            for key in ("codec_type", "codec_name", "width", "height", "sample_rate", "channels")
            if key in s
        }
        for s in info["streams"]
    ]
    return {
        "format": fmt.get("format_name"),
        "duration_s": ffmpeg.duration_s(info),
        "size_bytes": int(fmt["size"]) if "size" in fmt else None,
        "bit_rate": int(fmt["bit_rate"]) if "bit_rate" in fmt else None,
        "streams": streams,
        "tags": _tags(info),
    }


def _musicbrainz(artist: str, title: str) -> dict[str, Any]:
    global _mb_last_call
    query = f'artist:"{artist}" AND recording:"{title}"'
    url = f"{_MUSICBRAINZ_URL}?{urlencode({'query': query, 'fmt': 'json', 'limit': 1})}"
    with _mb_lock:
        wait = _MB_MIN_INTERVAL_S - (time.monotonic() - _mb_last_call)
        if wait > 0:
            time.sleep(wait)
        _mb_last_call = time.monotonic()
    try:
        recordings = _get_json(url).get("recordings") or []
    except (OpError, NotFound) as exc:
        return {"source": "musicbrainz", "error": str(exc)}
    if not recordings:
        return {"source": "musicbrainz", "match": None}
    best = recordings[0]
    return {
        "source": "musicbrainz",
        "recording_id": best.get("id"),
        "score": best.get("score"),
        "title": best.get("title"),
        "artist": ", ".join(c.get("name", "") for c in best.get("artist-credit", [])),
        "release": (best.get("releases") or [{}])[0].get("title"),
        "first_release_date": best.get("first-release-date"),
    }


def _write_json(path: Path, data: dict[str, Any]) -> Path:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def metadata(
    task: SubTaskMessage,
    src: Path,
    out_dir: Path,
    *,
    timeout_s: float,
    on_progress: Callable[[int], None],
) -> OpOutput:
    info = ffmpeg.probe(src, timeout_s)
    data = _summary(info)
    catalog_match = False
    if task.media_type is MediaType.AUDIO:
        artist, title = artist_title(task, info)
        if artist and title:
            data["catalog"] = _musicbrainz(artist, title)
            catalog_match = bool(data["catalog"].get("recording_id"))
    out = _write_json(out_dir / "metadata.json", data)
    return OpOutput(
        [out],
        {"format": data["format"], "duration_s": data["duration_s"], "catalog_match": catalog_match},
    )


def lyrics(
    task: SubTaskMessage,
    src: Path,
    out_dir: Path,
    *,
    timeout_s: float,
    on_progress: Callable[[int], None],
) -> OpOutput:
    info = ffmpeg.probe(src, timeout_s)
    artist, title = artist_title(task, info)
    if not (artist and title):
        meta = {"found": False, "reason": "sin artista o título (tags, params o nombre)"}
        return OpOutput([_write_json(out_dir / "lyrics.json", meta)], meta)

    try:
        text = (_get_json(f"{_LYRICS_URL}/{quote(artist)}/{quote(title)}").get("lyrics") or "").strip()
    except NotFound:
        text = ""

    meta = {"found": bool(text), "artist": artist, "title": title}
    files = [_write_json(out_dir / "lyrics.json", {**meta, "source": "lyrics.ovh"})]
    if text:
        lyrics_path = out_dir / "lyrics.txt"
        lyrics_path.write_text(text, encoding="utf-8")
        files.insert(0, lyrics_path)
    return OpOutput(files, meta)


def _slug(value: str) -> str:
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_value.lower()).strip("-") or "otro"


def _resolution(height: int) -> str:
    for minimum, label in ((2160, "4k"), (1080, "1080p"), (720, "720p")):
        if height >= minimum:
            return label
    return "sd"


def _duration_bucket(seconds: float | None) -> str:
    if seconds is None or seconds < 60:
        return "corto"
    return "medio" if seconds < 600 else "largo"


def classify(
    task: SubTaskMessage,
    src: Path,
    out_dir: Path,
    *,
    timeout_s: float,
    on_progress: Callable[[int], None],
) -> OpOutput:
    info = ffmpeg.probe(src, timeout_s)
    duration = ffmpeg.duration_s(info)
    video = next((s for s in info["streams"] if s.get("codec_type") == "video"), None)
    audio = next((s for s in info["streams"] if s.get("codec_type") == "audio"), None)

    labels: dict[str, Any] = {"media_type": task.media_type.value}
    if video and task.media_type is not MediaType.AUDIO:
        width, height = int(video.get("width", 0)), int(video.get("height", 0))
        labels["resolution"] = _resolution(min(width, height))
        labels["orientation"] = (
            "horizontal" if width > height else "vertical" if height > width else "cuadrada"
        )
    if task.media_type is not MediaType.IMAGE:
        labels["duration"] = _duration_bucket(duration)
    if audio:
        labels["audio_quality"] = (
            "sin-perdida"
            if audio.get("codec_name") in _LOSSLESS_CODECS or str(audio.get("codec_name", "")).startswith("pcm_")
            else "comprimido"
        )
    genre = _tags(info).get("genre")
    if task.media_type is MediaType.AUDIO:
        labels["genre"] = _slug(genre) if genre else "sin-genero"

    parts = {
        MediaType.VIDEO: ("resolution", "duration"),
        MediaType.AUDIO: ("genre", "duration"),
        MediaType.IMAGE: ("resolution", "orientation"),
    }[task.media_type]
    folder = "/".join([task.media_type.value, *(labels[p] for p in parts)])

    out = _write_json(out_dir / "classification.json", {**labels, "folder": folder})
    return OpOutput([out], {"folder": folder})
