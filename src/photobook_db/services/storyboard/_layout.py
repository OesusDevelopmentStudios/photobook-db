# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

from dataclasses import asdict, dataclass
from typing import Any

MAX_FRAMES = 100
MIN_INTERVAL_MS = 1000
FRAME_WIDTH = 160
MAX_FRAME_HEIGHT = 320  # a very tall video gets narrower frames, so the sprite stays a size WebP can store
MAX_COLUMNS = 10


@dataclass(frozen=True)
class StoryboardLayout:
    """Frame i shows the video at i * interval_ms; frames fill the sprite row by row."""

    interval_ms: int
    frame_count: int
    frame_width: int
    frame_height: int
    columns: int

    @property
    def rows(self) -> int:
        return -(-self.frame_count // self.columns)

    def frame_times_ms(self) -> list[int]:
        return [index * self.interval_ms for index in range(self.frame_count)]

    def frame_box(self, index: int) -> tuple[int, int]:
        """The top left corner of frame `index` in the sprite."""
        row, column = divmod(index, self.columns)
        return column * self.frame_width, row * self.frame_height

    def sprite_size(self) -> tuple[int, int]:
        return self.columns * self.frame_width, self.rows * self.frame_height

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "StoryboardLayout":
        return cls(**{name: int(data[name]) for name in cls.__dataclass_fields__})


def storyboard_layout(duration_ms: int, width: int, height: int) -> StoryboardLayout:
    """For a video of that duration and size as shown: up to 100 frames, at least a second apart."""
    interval_ms = max(MIN_INTERVAL_MS, -(-duration_ms // MAX_FRAMES))
    frame_count = max(1, min(MAX_FRAMES, -(-duration_ms // interval_ms)))
    frame_width = min(FRAME_WIDTH, width)  # a small video is not made larger
    frame_height = max(1, round(frame_width * height / width))
    if frame_height > MAX_FRAME_HEIGHT:
        frame_width = max(1, round(frame_width * MAX_FRAME_HEIGHT / frame_height))
        frame_height = MAX_FRAME_HEIGHT

    return StoryboardLayout(interval_ms, frame_count, frame_width, frame_height, min(MAX_COLUMNS, frame_count))
