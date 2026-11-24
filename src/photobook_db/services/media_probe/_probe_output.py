# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

"""Reads ffprobe's JSON output (`-print_format json -show_format -show_streams`)."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

# The containers the server takes as videos, by ffprobe's format name; MP4 and QuickTime share one, as do WebM and
# Matroska, and are told apart by the file's brand or document type.
_ISO_MEDIA = "mov,mp4,m4a,3gp,3g2,mj2"
_MATROSKA = "matroska,webm"
_AVI = "avi"

MIME_TYPES = {
    "mp4": "video/mp4",
    "mov": "video/quicktime",
    "webm": "video/webm",
    "mkv": "video/x-matroska",
    "avi": "video/x-msvideo",
}


@dataclass(frozen=True)
class VideoInfo:
    container: str  # "mp4", "mov", "webm", "mkv" or "avi"
    width: int  # as shown, after the rotation the file asks for
    height: int
    duration_ms: int
    video_codec: str  # e.g. "h264", "hevc", "vp9", "av1"
    audio_codec: str | None  # None: no sound
    bitrate: int | None  # bits per second, of the whole file
    frame_rate: float | None
    # When it was recorded, as a local wall-clock time and that place's offset from UTC in minutes. A file that only
    # knows UTC gives UTC and offset 0; one that knows nothing gives None.
    recorded_at: datetime | None
    recorded_utc_offset_minutes: int | None

    @property
    def mime_type(self) -> str:
        return MIME_TYPES[self.container]


def video_info_from(probe: dict[str, Any], file_start: bytes) -> VideoInfo | None:
    """None for a file that is not a video in one of the containers the server takes, or has no picture.
    file_start: the file's first bytes, which tell WebM from Matroska."""
    format_section = probe.get("format", {})
    container = _container(format_section, file_start)
    video = _first_video_stream(probe.get("streams", []))
    if container is None or video is None:
        return None

    width, height = int(video.get("width") or 0), int(video.get("height") or 0)
    if width <= 0 or height <= 0:
        return None

    if _rotation(video) % 180 != 0:
        width, height = height, width

    audio = next((stream for stream in probe.get("streams", []) if stream.get("codec_type") == "audio"), None)
    recorded_at, offset = _recording_time(format_section.get("tags", {}), video.get("tags", {}))
    return VideoInfo(
        container=container,
        width=width,
        height=height,
        duration_ms=_duration_ms(format_section, video),
        video_codec=str(video.get("codec_name", "")),
        audio_codec=str(audio["codec_name"]) if audio and audio.get("codec_name") else None,
        bitrate=_integer(format_section.get("bit_rate")),
        frame_rate=_frame_rate(video.get("avg_frame_rate")),
        recorded_at=recorded_at,
        recorded_utc_offset_minutes=offset,
    )


def _container(format_section: dict[str, Any], file_start: bytes) -> str | None:
    format_name = format_section.get("format_name")
    if format_name == _ISO_MEDIA:
        # QuickTime files name their brand "qt  "; MP4 and M4V name others (isom, mp42, M4V …).
        return "mov" if format_section.get("tags", {}).get("major_brand", "").strip() == "qt" else "mp4"

    if format_name == _MATROSKA:
        # The EBML header's DocType element: ID 0x4282, size 4, "webm" (Matroska's is size 8, "matroska")
        return "webm" if b"\x42\x82\x84webm" in file_start else "mkv"

    return "avi" if format_name == _AVI else None


def _first_video_stream(streams: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The picture itself, not a cover image some containers carry as a video stream."""
    for stream in streams:
        if stream.get("codec_type") == "video" and not stream.get("disposition", {}).get("attached_pic"):
            return stream

    return None


def _rotation(video: dict[str, Any]) -> int:
    """Degrees the picture is turned when shown: the display matrix, or the older "rotate" tag."""
    for side_data in video.get("side_data_list", []):
        if "rotation" in side_data:
            return int(round(float(side_data["rotation"])))

    return _integer(video.get("tags", {}).get("rotate")) or 0


def _duration_ms(format_section: dict[str, Any], video: dict[str, Any]) -> int:
    for seconds in (format_section.get("duration"), video.get("duration")):
        try:
            return max(0, round(float(seconds) * 1000))
        except (TypeError, ValueError):
            continue

    return 0


def _frame_rate(text: Any) -> float | None:
    """ffprobe writes e.g. "30000/1001"; "0/0" when unknown."""
    try:
        numerator, denominator = (float(part) for part in str(text).split("/"))
    except ValueError:
        return None

    return round(numerator / denominator, 3) if numerator > 0 and denominator > 0 else None


def _integer(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _recording_time(format_tags: dict[str, Any], video_tags: dict[str, Any]) -> tuple[datetime | None, int | None]:
    """Apple devices store the local time with its offset; other files only UTC (creation_time)."""
    apple = _parsed_time(format_tags.get("com.apple.quicktime.creationdate"))
    if apple is not None and apple.utcoffset() is not None:
        offset = apple.utcoffset() or timedelta()
        return apple.replace(tzinfo=None), round(offset.total_seconds() / 60)

    for tags in (format_tags, video_tags):
        utc = _parsed_time(tags.get("creation_time"))
        if utc is not None:
            if utc.utcoffset() is not None:
                utc = (utc - (utc.utcoffset() or timedelta())).replace(tzinfo=None)

            return utc, 0

    return None, None


def _parsed_time(text: Any) -> datetime | None:
    """ISO 8601 as files write it; some list a time twice, separated by ";"."""
    if not isinstance(text, str) or not text.strip():
        return None

    first = text.split(";")[0].strip()
    if first.endswith("Z"):
        first = first[:-1] + "+00:00"

    try:
        parsed = datetime.fromisoformat(first)
    except ValueError:
        return None

    # A zero date is what some cameras write when they do not know it.
    return None if parsed.year < 1971 else parsed
