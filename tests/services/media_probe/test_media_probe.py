# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

"""Against short videos made by FFmpeg at test time; skipped without FFmpeg, with the reason."""

import io
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import pytest
from PIL import Image

from photobook_db.services.media_probe import MediaProbe
from photobook_db.services.media_probe import _service

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="FFmpeg is not installed"
)

TWO_SECONDS = ["-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:duration=2"]
SOUND = ["-f", "lavfi", "-i", "sine=frequency=440:duration=2"]


def make_video(path: Path, *arguments: str) -> Path:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *arguments, str(path)], check=True)
    return path


def has_encoder(name: str) -> bool:
    encoders = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
    return f" {name} " in encoders


@pytest.fixture
def probe() -> MediaProbe:
    return MediaProbe()


# MPEG-4 Part 2 and AAC: encoders every FFmpeg build has
@pytest.mark.parametrize(
    ("name", "container_format", "container", "mime_type"),
    [
        ("clip.mp4", "mp4", "mp4", "video/mp4"),
        ("clip.mov", "mov", "mov", "video/quicktime"),
        ("clip.mkv", "matroska", "mkv", "video/x-matroska"),
        ("clip.avi", "avi", "avi", "video/x-msvideo"),
    ],
)
def test_reads_a_video_in_each_container(
    probe: MediaProbe, tmp_path: Path, name: str, container_format: str, container: str, mime_type: str
) -> None:
    video = make_video(tmp_path / name, *TWO_SECONDS, *SOUND, "-c:v", "mpeg4", "-c:a", "aac", "-f", container_format)

    info = probe.probe(video)

    assert info is not None
    assert (info.container, info.mime_type) == (container, mime_type)
    assert (info.width, info.height) == (320, 240)
    assert 1900 <= info.duration_ms <= 2100
    assert (info.video_codec, info.audio_codec) == ("mpeg4", "aac")
    assert info.bitrate is not None and info.bitrate > 0
    assert info.frame_rate == 25


def test_reads_a_webm_video(probe: MediaProbe, tmp_path: Path) -> None:
    if not has_encoder("libvpx"):
        pytest.skip("this FFmpeg cannot write VP8")

    info = probe.probe(make_video(tmp_path / "clip.webm", *TWO_SECONDS, "-c:v", "libvpx", "-f", "webm"))

    assert info is not None and info.container == "webm"
    assert (info.video_codec, info.audio_codec) == ("vp8", None)


# As phones store a video recorded upright: the picture as the sensor saw it, and the turn to show it
def test_gives_the_size_as_shown(probe: MediaProbe, tmp_path: Path) -> None:
    plain = make_video(tmp_path / "plain.mp4", *TWO_SECONDS, "-c:v", "mpeg4")
    rotated = make_video(tmp_path / "rotated.mp4", "-display_rotation", "90", "-i", str(plain), "-c", "copy")

    info = probe.probe(rotated)

    assert info is not None and (info.width, info.height) == (240, 320)


def test_reads_the_recording_time(probe: MediaProbe, tmp_path: Path) -> None:
    apple = make_video(
        tmp_path / "phone.mov", *TWO_SECONDS, "-c:v", "mpeg4", "-movflags", "use_metadata_tags",
        "-metadata", "com.apple.quicktime.creationdate=2025-07-14T18:03:12+0200",
    )
    camera = make_video(tmp_path / "camera.mp4", *TWO_SECONDS, "-c:v", "mpeg4",
                        "-metadata", "creation_time=2025-07-14T16:03:12Z")

    assert (probe.probe(apple).recorded_at, probe.probe(apple).recorded_utc_offset_minutes) == (
        datetime(2025, 7, 14, 18, 3, 12), 120)
    assert (probe.probe(camera).recorded_at, probe.probe(camera).recorded_utc_offset_minutes) == (
        datetime(2025, 7, 14, 16, 3, 12), 0)


def test_knows_no_video_in_other_files(probe: MediaProbe, tmp_path: Path) -> None:
    image = tmp_path / "beach.png"
    Image.new("RGB", (64, 48)).save(image)
    text = tmp_path / "notes.mp4"
    text.write_text("not a video")
    sound = make_video(tmp_path / "song.m4a", *SOUND, "-c:a", "aac")

    assert probe.probe(image) is None
    assert probe.probe(text) is None
    assert probe.probe(sound) is None
    assert probe.probe(tmp_path / "missing.mp4") is None


def test_gives_an_upright_frame_that_fits(probe: MediaProbe, tmp_path: Path) -> None:
    plain = make_video(tmp_path / "plain.mp4", *TWO_SECONDS, "-c:v", "mpeg4")
    rotated = make_video(tmp_path / "rotated.mp4", "-display_rotation", "90", "-i", str(plain), "-c", "copy")

    wide = probe.frame(plain, at_ms=1000, max_side=100)
    upright = probe.frame(rotated, at_ms=1000, max_side=100)

    assert wide is not None and upright is not None
    assert Image.open(io.BytesIO(wide)).size == (100, 75)
    assert Image.open(io.BytesIO(upright)).size == (75, 100)
    assert Image.open(io.BytesIO(wide)).format == "PNG"


def test_gives_no_frame_past_the_end(probe: MediaProbe, tmp_path: Path) -> None:
    video = make_video(tmp_path / "clip.mp4", *TWO_SECONDS, "-c:v", "mpeg4")

    assert probe.frame(video, at_ms=60000, max_side=100) is None


def test_stops_ffmpeg_that_takes_too_long(
    probe: MediaProbe, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    video = make_video(tmp_path / "clip.mp4", *TWO_SECONDS, "-c:v", "mpeg4")
    monkeypatch.setattr(_service, "PROBE_TIMEOUT_SECONDS", 0.000001)
    monkeypatch.setattr(_service, "FRAME_TIMEOUT_SECONDS", 0.000001)

    assert probe.probe(video) is None
    assert probe.frame(video, at_ms=0, max_side=100) is None


# Without FFmpeg the server works as before and knows no videos.
def test_knows_nothing_without_ffmpeg(tmp_path: Path) -> None:
    video = make_video(tmp_path / "clip.mp4", *TWO_SECONDS, "-c:v", "mpeg4")
    without = MediaProbe(ffprobe="no-such-ffprobe", ffmpeg="no-such-ffmpeg")

    assert not without.available()
    assert without.probe(video) is None
    assert without.frame(video, at_ms=0, max_side=100) is None
