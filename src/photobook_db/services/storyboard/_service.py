# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

import io
import json
import os
import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

from PIL import Image

from photobook_db.services.media_probe import MediaProbe
from photobook_db.services.storyboard._layout import StoryboardLayout, storyboard_layout
from photobook_db.utils.problems import Problem

# Each frame is a seek of its own, so the time a storyboard takes depends on its frame count, not on the video's
# length; a few at once are much faster on a server with a few cores.
FRAME_WORKERS = min(4, os.cpu_count() or 1)
# A video FFmpeg reads only very slowly must not keep a request for long: frames not started by then are left out.
DEADLINE_SECONDS = 120.0
WEBP_QUALITY = 75


class StoryboardService:
    def __init__(self, storyboard_dir: Path, media_probe: MediaProbe) -> None:
        self._storyboard_dir = storyboard_dir
        self._media_probe = media_probe
        self._locks = _LocksByKey()

    def storyboard(self, video_path: Path, sha256: str, duration_ms: int, width: int, height: int) -> StoryboardLayout:
        """The video's storyboard, made on the first call; width and height as the video is shown. Two calls for
        the same video make it once. Raises 503 storyboard_unavailable."""
        stored = self._stored_layout(sha256)
        if stored is not None:
            return stored

        with self._locks.holding(sha256):
            stored = self._stored_layout(sha256)  # made while this call waited
            if stored is not None:
                return stored

            if not self._media_probe.available():
                raise storyboard_unavailable("The server has no FFmpeg to read videos with.")

            layout = storyboard_layout(duration_ms, width, height)
            sprite = self._sprite(video_path, layout)
            _write_atomically(self.sprite_path(sha256), _webp(sprite))
            # Written last: a layout on disk means its sprite is complete.
            _write_atomically(self._layout_path(sha256), json.dumps(layout.to_json()).encode())
            return layout

    def sprite_path(self, sha256: str) -> Path:
        """storyboard/<first two hex digits>/<sha256>.webp; it exists once storyboard() has returned."""
        return self._storyboard_dir / sha256[:2] / f"{sha256}.webp"

    def _layout_path(self, sha256: str) -> Path:
        return self.sprite_path(sha256).with_suffix(".json")

    def _stored_layout(self, sha256: str) -> StoryboardLayout | None:
        try:
            layout = StoryboardLayout.from_json(json.loads(self._layout_path(sha256).read_text()))
        except (OSError, ValueError, KeyError, TypeError):
            return None  # not made yet, or damaged: made again

        return layout if self.sprite_path(sha256).exists() else None

    def _sprite(self, video_path: Path, layout: StoryboardLayout) -> Image.Image:
        deadline = time.monotonic() + DEADLINE_SECONDS
        size = (layout.frame_width, layout.frame_height)

        def frame_at(at_ms: int) -> Image.Image | None:
            if time.monotonic() > deadline:
                return None

            return _decoded(self._media_probe.frame(video_path, at_ms, max(size)), size)

        with ThreadPoolExecutor(FRAME_WORKERS) as pool:
            frames = _gaps_filled(list(pool.map(frame_at, layout.frame_times_ms())))

        sprite = Image.new("RGB", layout.sprite_size())
        for index, frame in enumerate(frames):
            sprite.paste(frame, layout.frame_box(index))

        return sprite


def storyboard_unavailable(detail: str) -> Problem:
    return Problem(503, "storyboard_unavailable", "No storyboard", detail)


def _decoded(png: bytes | None, size: tuple[int, int]) -> Image.Image | None:
    if png is None:
        return None

    try:
        with Image.open(io.BytesIO(png)) as frame:
            return frame.convert("RGB").resize(size) if frame.size != size else frame.convert("RGB")
    except (OSError, ValueError):
        return None


def _gaps_filled(frames: list[Image.Image | None]) -> list[Image.Image]:
    """A frame FFmpeg could not make (e.g. just before the end) shows the nearest one that it could, so every
    frame still stands for its time. Raises 503 if there is none."""
    made = {index: frame for index, frame in enumerate(frames) if frame is not None}
    if not made:
        raise storyboard_unavailable("FFmpeg cannot decode the video's pictures.")

    # On a tie the earlier frame: it is what was on screen just before.
    return [made[min(made, key=lambda found: abs(found - index))] for index in range(len(frames))]


def _webp(sprite: Image.Image) -> bytes:
    buffer = io.BytesIO()
    sprite.save(buffer, format="WEBP", quality=WEBP_QUALITY)
    return buffer.getvalue()


def _write_atomically(path: Path, content: bytes) -> None:
    """Under another name first, so a crash never leaves half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    partial.write_bytes(content)
    os.replace(partial, path)


class _LocksByKey:
    """A lock per key, kept only while someone holds or waits for it."""

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._locks: dict[str, tuple[threading.Lock, int]] = {}

    @contextmanager
    def holding(self, key: str) -> Iterator[None]:
        with self._guard:
            lock, users = self._locks.get(key, (threading.Lock(), 0))
            self._locks[key] = (lock, users + 1)

        try:
            with lock:
                yield
        finally:
            with self._guard:
                lock, users = self._locks[key]
                if users == 1:
                    del self._locks[key]
                else:
                    self._locks[key] = (lock, users - 1)
