# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

"""How ffprobe's JSON is read, on canned output: runs without FFmpeg installed."""

from datetime import datetime
from typing import Any

from photobook_db.services.media_probe._probe_output import video_info_from

MP4_START = b"\x00\x00\x00\x20ftypisom"
WEBM_START = b"\x1a\x45\xdf\xa3\x9f\x42\x86\x81\x01\x42\xf7\x81\x01\x42\xf2\x81\x04\x42\xf3\x81\x08\x42\x82\x84webm"
MKV_START = b"\x1a\x45\xdf\xa3\xa3\x42\x86\x81\x01\x42\xf7\x81\x01\x42\xf2\x81\x04\x42\xf3\x81\x08\x42\x82\x88matroska"


def probe(format_name: str = "mov,mp4,m4a,3gp,3g2,mj2", streams: list[dict[str, Any]] | None = None,
          **format_fields: Any) -> dict[str, Any]:
    video = {"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080, "avg_frame_rate": "30000/1001"}
    return {
        "format": {"format_name": format_name, "duration": "222.4", "bit_rate": "8120000", **format_fields},
        "streams": [video, {"codec_type": "audio", "codec_name": "aac"}] if streams is None else streams,
    }


def test_reads_a_phone_video() -> None:
    info = video_info_from(probe(), MP4_START)

    assert info is not None
    assert (info.container, info.mime_type) == ("mp4", "video/mp4")
    assert (info.width, info.height) == (1920, 1080)
    assert info.duration_ms == 222400
    assert (info.video_codec, info.audio_codec) == ("h264", "aac")
    assert info.bitrate == 8120000
    assert info.frame_rate == 29.97


def test_tells_quicktime_from_mp4_by_the_brand() -> None:
    info = video_info_from(probe(tags={"major_brand": "qt  "}), MP4_START)

    assert info is not None and info.container == "mov"
    assert info.mime_type == "video/quicktime"


def test_tells_webm_from_matroska_by_the_document_type() -> None:
    webm = video_info_from(probe("matroska,webm"), WEBM_START)
    mkv = video_info_from(probe("matroska,webm"), MKV_START)

    assert webm is not None and webm.mime_type == "video/webm"
    assert mkv is not None and mkv.mime_type == "video/x-matroska"


def test_turns_the_size_with_the_rotation() -> None:
    by_matrix = probe(streams=[{"codec_type": "video", "codec_name": "hevc", "width": 1920, "height": 1080,
                                "side_data_list": [{"side_data_type": "Display Matrix", "rotation": -90}]}])
    by_tag = probe(streams=[{"codec_type": "video", "codec_name": "hevc", "width": 1920, "height": 1080,
                             "tags": {"rotate": "270"}}])
    upside_down = probe(streams=[{"codec_type": "video", "codec_name": "hevc", "width": 1920, "height": 1080,
                                  "side_data_list": [{"rotation": 180}]}])

    assert (video_info_from(by_matrix, MP4_START).width, video_info_from(by_matrix, MP4_START).height) == (1080, 1920)
    assert video_info_from(by_tag, MP4_START).width == 1080
    assert video_info_from(upside_down, MP4_START).width == 1920


def test_has_no_sound_without_an_audio_stream() -> None:
    info = video_info_from(probe(streams=[{"codec_type": "video", "codec_name": "vp9", "width": 640, "height": 360}]),
                           MP4_START)

    assert info is not None and info.audio_codec is None
    assert info.frame_rate is None  # not given


# Some containers carry a cover image as a "video" stream; it is not the picture.
def test_skips_a_cover_image() -> None:
    cover = {
        "codec_type": "video", "codec_name": "mjpeg", "width": 600, "height": 600, "disposition": {"attached_pic": 1}
    }
    picture = {"codec_type": "video", "codec_name": "h264", "width": 1280, "height": 720}

    info = video_info_from(probe(streams=[cover, picture]), MP4_START)

    assert info is not None and info.video_codec == "h264"


def test_is_no_video_without_a_picture_or_in_another_container() -> None:
    audio_only = probe(streams=[{"codec_type": "audio", "codec_name": "aac"}])

    assert video_info_from(audio_only, MP4_START) is None
    assert video_info_from(probe("png_pipe"), b"\x89PNG") is None
    assert video_info_from(probe("mpegts"), b"G") is None
    assert video_info_from({}, b"") is None


# Apple devices write the local time with its offset; that is the photo's kind of date.
def test_prefers_the_local_time_apple_devices_write() -> None:
    tags = {
        "creation_time": "2025-07-14T16:03:12.000000Z",
        "com.apple.quicktime.creationdate": "2025-07-14T18:03:12+0200",
    }

    info = video_info_from(probe(tags=tags), MP4_START)

    assert info.recorded_at == datetime(2025, 7, 14, 18, 3, 12)
    assert info.recorded_utc_offset_minutes == 120


def test_takes_utc_when_that_is_all_there_is() -> None:
    from_format = video_info_from(probe(tags={"creation_time": "2025-07-14T16:03:12.000000Z"}), MP4_START)
    from_stream = video_info_from(
        probe(streams=[{"codec_type": "video", "codec_name": "h264", "width": 8, "height": 8,
                        "tags": {"creation_time": "2025-07-14T16:03:12Z"}}]), MP4_START)

    assert from_format.recorded_at == datetime(2025, 7, 14, 16, 3, 12)
    assert from_format.recorded_utc_offset_minutes == 0
    assert from_stream.recorded_at == datetime(2025, 7, 14, 16, 3, 12)


def test_ignores_dates_that_are_not_dates() -> None:
    for creation_time in ("1970-01-01T00:00:00.000000Z", "0000-00-00", "yesterday", ""):
        info = video_info_from(probe(tags={"creation_time": creation_time}), MP4_START)

        assert info.recorded_at is None
        assert info.recorded_utc_offset_minutes is None
