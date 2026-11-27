# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

"""Importing videos, against short videos made by FFmpeg at test time; skipped without FFmpeg, with the reason."""

import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import pytest
from PIL import Image

from photobook_db.db import Database
from photobook_db.services.importer import UploadMetadata
from photobook_db.services.media_probe import MediaProbe
from photobook_db.services.rotation import RotationService
from photobook_db.utils.problems import Problem

from .test_importer import IMPORT_TIME, NO_RESTRICTIONS, Library, refused

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="FFmpeg is not installed"
)

SOUND = ["-f", "lavfi", "-i", "sine=frequency=440:duration=2"]


def picture(seconds: float = 2, rate: int = 25) -> list[str]:
    return ["-f", "lavfi", "-i", f"testsrc=size=320x240:rate={rate}:duration={seconds}"]


@pytest.fixture
def library(database: Database, tmp_path: Path) -> Library:
    return Library(database, tmp_path)


def incoming_video(library: Library, *arguments: str, container: str = "mp4") -> Path:
    """Made where uploads arrive; the arguments say what it holds."""
    made = library.importer.new_incoming_path().with_suffix(f".{container}")
    subprocess.run(["ffmpeg", "-v", "error", "-y", *arguments, "-f", container, str(made)], check=True)
    incoming = library.importer.new_incoming_path()
    made.rename(incoming)  # uploads arrive without a suffix
    return incoming


def tag_names(record: object) -> set[str]:
    return {str(tag) for tag in record.tags}  # type: ignore[attr-defined]


def test_imports_a_video_with_its_details(library: Library) -> None:
    incoming = incoming_video(library, *picture(), *SOUND, "-c:v", "mpeg4", "-c:a", "aac")

    record = library.import_image(incoming, UploadMetadata("waves.mp4", tags=("general:coast",)), None)

    assert record.media_kind == "video"
    assert (record.mime_type, record.width, record.height) == ("video/mp4", 320, 240)
    assert 1900 <= record.duration_ms <= 2100
    assert (record.video_codec, record.audio_codec, record.frame_rate) == ("mpeg4", "aac", 25)
    assert record.bitrate is not None and record.bitrate > 0
    assert record.storage_path.endswith(".mp4")
    assert record.taken_at == IMPORT_TIME and record.taken_at_source == "import_time"
    assert {"general:coast", "meta:video", "meta:mp4", "meta:low_res", "meta:sound"} <= tag_names(record)
    assert "meta:still_image" not in tag_names(record)


def test_names_the_file_after_its_container(library: Library) -> None:
    for container, name in (("mov", "clip.mov"), ("matroska", "clip.mkv"), ("avi", "clip.avi")):
        incoming = incoming_video(library, *picture(rate=24 + len(name)), "-c:v", "mpeg4", container=container)

        record = library.import_image(incoming, UploadMetadata(name), None)

        assert record.storage_path.endswith(name.rsplit(".", 1)[1])


def test_poster_frame_is_the_thumbnail_upright(library: Library) -> None:
    plain = incoming_video(library, *picture(), "-c:v", "mpeg4")
    rotated = library.importer.new_incoming_path()
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-display_rotation", "90", "-i", str(plain), "-c", "copy", "-f", "mp4",
         str(rotated)],
        check=True,
    )
    plain.unlink()

    record = library.import_image(rotated, UploadMetadata("upright.mp4"), None)

    assert (record.width, record.height) == (240, 320)
    with Image.open(library.thumbnails.thumbnail_path(record.sha256, 256)) as thumbnail:
        assert thumbnail.size == (192, 256)  # portrait, as shown
        assert thumbnail.format == "WEBP"


def test_takes_the_videos_own_recording_time(library: Library) -> None:
    incoming = incoming_video(
        library, *picture(), "-c:v", "mpeg4", "-movflags", "use_metadata_tags",
        "-metadata", "com.apple.quicktime.creationdate=2025-07-14T18:03:12+0200", container="mov",
    )

    record = library.import_image(incoming, UploadMetadata("phone.mov", file_mtime=datetime(2026, 1, 1)), None)

    assert (record.taken_at, record.taken_at_source) == (datetime(2025, 7, 14, 18, 3, 12), "container")
    assert record.taken_at_utc_offset_minutes == 120
    assert "/2025/" in record.storage_path  # filed by the recording year


def test_a_silent_long_video(library: Library) -> None:
    incoming = incoming_video(library, *picture(seconds=301, rate=1), "-c:v", "mpeg4")

    record = library.import_image(incoming, UploadMetadata("timelapse.mp4"), None)

    assert {"meta:no_sound", "meta:long_video"} <= tag_names(record)
    assert record.audio_codec is None


# A video FFmpeg can read but not decode would be a black tile and a player error: it stays out.
def test_refuses_a_video_over_the_video_limit(library: Library) -> None:
    incoming = incoming_video(library, *picture(), "-c:v", "mpeg4")
    limit = incoming.stat().st_size - 1
    library.settings.set("max_video_upload_bytes", limit)  # images keep their larger limit

    problem = refused(lambda: library.import_image(incoming, UploadMetadata("waves.mp4"), None))

    assert (problem.status, problem.code, problem.extra["limit_bytes"]) == (413, "file_too_large", limit)
    assert "videos" in problem.detail
    library.assert_nothing_stored()


def test_a_video_may_be_larger_than_the_image_limit(library: Library) -> None:
    incoming = incoming_video(library, *picture(), "-c:v", "mpeg4")
    library.settings.set("max_upload_bytes", incoming.stat().st_size // 2)

    record = library.import_image(incoming, UploadMetadata("waves.mp4"), None)

    assert record.media_kind == "video"


def test_refuses_a_video_without_a_decodable_picture(library: Library, monkeypatch: pytest.MonkeyPatch) -> None:
    incoming = incoming_video(library, *picture(), "-c:v", "mpeg4")
    monkeypatch.setattr(library.media_probe, "frame", lambda *_arguments: None)

    problem = refused(lambda: library.import_image(incoming, UploadMetadata("broken.mp4"), None))

    assert (problem.status, problem.code) == (415, "unsupported_media_type")
    assert problem.title == "The video cannot be decoded"  # not "Not a supported image": the file is a video
    library.assert_nothing_stored()


# Without FFmpeg the server says why it refuses a video, not that the file is no image.
def test_without_ffmpeg_says_videos_are_not_supported(library: Library) -> None:
    incoming = incoming_video(library, *picture(), "-c:v", "mpeg4")
    library.importer._media_probe = MediaProbe(ffprobe="no-such-ffprobe", ffmpeg="no-such-ffmpeg")

    problem = refused(lambda: library.import_image(incoming, UploadMetadata("waves.mp4"), None))

    assert (problem.status, problem.code) == (415, "unsupported_media_type")
    assert problem.title == "Videos are not supported here"
    assert problem.extra["detected_type"] == "video/mp4"
    library.assert_nothing_stored()


def test_still_refuses_files_that_are_neither(library: Library) -> None:
    incoming = library.importer.new_incoming_path()
    incoming.write_text("not a picture, not a video")

    problem = refused(lambda: library.import_image(incoming, UploadMetadata("notes.txt"), None))

    assert problem.code == "unsupported_media_type"
    assert "detected_type" not in problem.extra
    library.assert_nothing_stored()


def test_makes_a_videos_thumbnails_again(library: Library) -> None:
    record = library.import_image(incoming_video(library, *picture(), "-c:v", "mpeg4"), UploadMetadata("a.mp4"), None)
    library.thumbnails.delete_thumbnails(record.sha256)

    library.importer.make_thumbnails(
        library.storage.absolute_path(record.storage_path), record.sha256, video_duration_ms=record.duration_ms
    )

    assert library.thumbnails.thumbnail_path(record.sha256, 1024).exists()


def test_a_video_is_never_turned(library: Library) -> None:
    record = library.import_image(incoming_video(library, *picture(), "-c:v", "mpeg4"), UploadMetadata("a.mp4"), None)
    rotation = RotationService(library.images, library.storage, library.thumbnails)

    with pytest.raises(Problem) as raised:
        rotation.rotate(record.id, 90, NO_RESTRICTIONS)

    assert raised.value.code == "rotation_not_supported"
