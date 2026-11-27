# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

"""Storyboards with a fake FFmpeg that makes coloured frames, and once with the real one (skipped without it)."""

import io
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest
from PIL import Image

from photobook_db.services.media_probe import MediaProbe
from photobook_db.services.storyboard import StoryboardService, _service
from photobook_db.utils.problems import Problem

SHA256 = "ab" * 32
VIDEO = Path("/videos/waves.mp4")  # the fake never reads it


class FakeProbe:
    """Frames as PNG in a colour chosen by their time; counts the frames asked for."""

    def __init__(self, colour_at: Callable[[int], str | None] = lambda _at_ms: "teal", delay: float = 0.0) -> None:
        self.colour_at = colour_at
        self.delay = delay
        self.frames_made = 0
        self.most_at_once = 0  # frames being made at the same time
        self.ffmpeg_found = True
        self._count_lock = threading.Lock()
        self._at_once = 0

    def available(self) -> bool:
        return self.ffmpeg_found

    def frame(self, _path: Path, at_ms: int, max_side: int) -> bytes | None:
        with self._count_lock:
            self.frames_made += 1
            self._at_once += 1
            self.most_at_once = max(self.most_at_once, self._at_once)

        time.sleep(self.delay)
        with self._count_lock:
            self._at_once -= 1

        colour = self.colour_at(at_ms)
        if colour is None:
            return None

        buffer = io.BytesIO()
        Image.new("RGB", (max_side, max_side * 9 // 16), colour).save(buffer, format="PNG")
        return buffer.getvalue()


def make_service(tmp_path: Path, probe: FakeProbe | MediaProbe) -> StoryboardService:
    return StoryboardService(tmp_path / "storyboard", probe)  # type: ignore[arg-type]


def refused(action: Callable[[], object]) -> Problem:
    with pytest.raises(Problem) as raised:
        action()
    return raised.value


def frame_colours(service: StoryboardService, frame_count: int, columns: int = 10) -> list[tuple[int, int, int]]:
    """The colour in the middle of each frame of the stored sprite."""
    with Image.open(service.sprite_path(SHA256)) as sprite:
        rgb = sprite.convert("RGB")
        return [
            rgb.getpixel(((index % columns) * 160 + 80, (index // columns) * 90 + 45))  # type: ignore[misc]
            for index in range(frame_count)
        ]


def close_to(colour: tuple[int, int, int], expected: tuple[int, int, int]) -> bool:
    return all(abs(channel - wanted) <= 40 for channel, wanted in zip(colour, expected))


def test_makes_and_stores_the_sprite(tmp_path: Path) -> None:
    service = make_service(tmp_path, FakeProbe())

    layout = service.storyboard(VIDEO, SHA256, 25_000, 1920, 1080)

    assert (layout.frame_count, layout.interval_ms) == (25, 1000)
    with Image.open(service.sprite_path(SHA256)) as sprite:
        assert (sprite.format, sprite.size) == ("WEBP", (1600, 270))
    assert service.sprite_path(SHA256) == tmp_path / "storyboard" / "ab" / f"{SHA256}.webp"


def test_each_frame_is_at_its_time(tmp_path: Path) -> None:
    colours = ["red", "lime", "blue"]
    service = make_service(tmp_path, FakeProbe(lambda at_ms: colours[at_ms // 1000]))

    service.storyboard(VIDEO, SHA256, 2_500, 1920, 1080)

    expected = [(255, 0, 0), (0, 255, 0), (0, 0, 255)]
    assert all(map(close_to, frame_colours(service, 3, columns=3), expected))


def test_is_made_once(tmp_path: Path) -> None:
    probe = FakeProbe()
    service = make_service(tmp_path, probe)
    first = service.storyboard(VIDEO, SHA256, 25_000, 1920, 1080)

    assert service.storyboard(VIDEO, SHA256, 25_000, 1920, 1080) == first
    assert probe.frames_made == 25


def test_two_requests_at_once_make_it_once(tmp_path: Path) -> None:
    probe = FakeProbe(delay=0.02)
    service = make_service(tmp_path, probe)
    layouts = []
    requests = [
        threading.Thread(target=lambda: layouts.append(service.storyboard(VIDEO, SHA256, 8_000, 1920, 1080)))
        for _ in range(2)
    ]
    for request in requests:
        request.start()
    for request in requests:
        request.join()

    assert len(layouts) == 2 and layouts[0] == layouts[1]
    assert probe.frames_made == 8


def test_different_videos_are_made_at_the_same_time(tmp_path: Path) -> None:
    probe = FakeProbe(delay=0.2)
    service = make_service(tmp_path, probe)
    requests = [
        threading.Thread(target=service.storyboard, args=(VIDEO, sha256, 1_000, 1920, 1080))
        for sha256 in ("ab" * 32, "cd" * 32)
    ]
    for request in requests:
        request.start()
    for request in requests:
        request.join()

    assert probe.most_at_once == 2  # each has one frame


def test_a_missing_frame_shows_the_nearest_one(tmp_path: Path) -> None:
    colours = {0: "red", 1000: None, 2000: None, 3000: "blue", 4000: None}
    service = make_service(tmp_path, FakeProbe(lambda at_ms: colours[at_ms]))

    service.storyboard(VIDEO, SHA256, 4_500, 1920, 1080)

    red, blue = (255, 0, 0), (0, 0, 255)
    assert all(map(close_to, frame_colours(service, 5, columns=5), [red, red, blue, blue, blue]))


def test_without_any_frame_there_is_no_storyboard(tmp_path: Path) -> None:
    service = make_service(tmp_path, FakeProbe(lambda _at_ms: None))

    problem = refused(lambda: service.storyboard(VIDEO, SHA256, 4_500, 1920, 1080))

    assert (problem.status, problem.code) == (503, "storyboard_unavailable")
    assert not service.sprite_path(SHA256).exists()


def test_stops_asking_for_frames_after_the_deadline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_service, "DEADLINE_SECONDS", 0.0)
    probe = FakeProbe()
    service = make_service(tmp_path, probe)

    assert refused(lambda: service.storyboard(VIDEO, SHA256, 60_000, 1920, 1080)).code == "storyboard_unavailable"
    assert probe.frames_made == 0


def test_without_ffmpeg_there_is_no_storyboard(tmp_path: Path) -> None:
    probe = FakeProbe()
    probe.ffmpeg_found = False
    service = make_service(tmp_path, probe)

    problem = refused(lambda: service.storyboard(VIDEO, SHA256, 4_500, 1920, 1080))

    assert (problem.status, problem.code) == (503, "storyboard_unavailable")
    assert "FFmpeg" in problem.detail


def test_a_stored_storyboard_outlives_ffmpeg(tmp_path: Path) -> None:
    probe = FakeProbe()
    service = make_service(tmp_path, probe)
    made = service.storyboard(VIDEO, SHA256, 4_500, 1920, 1080)
    probe.ffmpeg_found = False

    assert service.storyboard(VIDEO, SHA256, 4_500, 1920, 1080) == made


@pytest.mark.parametrize("damage", ["layout", "sprite"])
def test_a_damaged_storyboard_is_made_again(tmp_path: Path, damage: str) -> None:
    probe = FakeProbe()
    service = make_service(tmp_path, probe)
    service.storyboard(VIDEO, SHA256, 4_500, 1920, 1080)
    if damage == "layout":
        service.sprite_path(SHA256).with_suffix(".json").write_text("{")
    else:
        service.sprite_path(SHA256).unlink()

    service.storyboard(VIDEO, SHA256, 4_500, 1920, 1080)

    assert probe.frames_made == 10
    assert service.sprite_path(SHA256).exists()


@pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="FFmpeg is not installed")
def test_with_ffmpeg(tmp_path: Path) -> None:
    """A video that is red for a second, then green, then blue."""
    video = tmp_path / "colours.mp4"
    colour = "geq=r='if(lt(T,1),255,0)':g='if(between(T,1,1.99),255,0)':b='if(gte(T,2),255,0)'"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=black:size=320x180:rate=10:duration=3",
         "-vf", colour, "-c:v", "mpeg4", "-q:v", "2", str(video)],
        check=True,
    )
    service = make_service(tmp_path, MediaProbe())

    layout = service.storyboard(video, SHA256, 3_000, 320, 180)

    assert (layout.frame_count, layout.frame_width, layout.frame_height) == (3, 160, 90)
    expected = [(255, 0, 0), (0, 255, 0), (0, 0, 255)]
    assert all(map(close_to, frame_colours(service, 3, columns=3), expected)), frame_colours(service, 3, columns=3)
