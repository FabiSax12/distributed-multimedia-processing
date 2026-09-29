"""Operación -> función. Cubre todo `shared.routing.Operation`, porque por la
ayuda entre pools (`POLL_ORDER`) un worker puede recibir operaciones de otro
pool: video y metadatos ayudan con `audio-normal`, audio con `video-normal`."""

from __future__ import annotations

from shared.routing import Operation as Op

from .audio import audio_convert, image_thumbnail
from .base import Operation
from .video import audio_extract, video_convert, video_thumbnail

OPERATIONS: dict[Op, Operation] = {
    Op.VIDEO_CONVERT: video_convert,
    Op.AUDIO_EXTRACT: audio_extract,
    Op.VIDEO_THUMBNAIL: video_thumbnail,
    Op.AUDIO_CONVERT: audio_convert,
    Op.IMAGE_THUMBNAIL: image_thumbnail,
}
