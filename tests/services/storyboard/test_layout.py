# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

import pytest

from photobook_db.services.storyboard import StoryboardLayout, storyboard_layout


@pytest.mark.parametrize(
    ("duration_ms", "interval_ms", "frame_count", "columns"),
    [
        (338_100, 3381, 100, 10),  # a long video: 100 frames, spread over all of it
        (100_000, 1000, 100, 10),
        (100_001, 1001, 100, 10),  # never 101 frames
        (17_600, 1000, 18, 10),  # a short one: a frame a second
        (2_500, 1000, 3, 3),
        (2_000, 1000, 2, 2),  # the last frame is before the end
        (600, 1000, 1, 1),
        (0, 1000, 1, 1),  # a duration FFmpeg did not know
    ],
)
def test_frames_are_at_least_a_second_apart(duration_ms: int, interval_ms: int, frame_count: int, columns: int) -> None:
    layout = storyboard_layout(duration_ms, 1920, 1080)

    assert (layout.interval_ms, layout.frame_count, layout.columns) == (interval_ms, frame_count, columns)
    assert layout.frame_times_ms()[-1] < max(duration_ms, 1)


@pytest.mark.parametrize(
    ("width", "height", "frame_size"),
    [
        (1920, 1080, (160, 90)),
        (1080, 1920, (160, 284)),  # portrait, as shown
        (120, 90, (120, 90)),  # a small video is not made larger
        (100, 1000, (32, 320)),  # very tall: narrower, so the sprite stays small enough for WebP
    ],
)
def test_frames_are_160_pixels_wide(width: int, height: int, frame_size: tuple[int, int]) -> None:
    layout = storyboard_layout(60_000, width, height)

    assert (layout.frame_width, layout.frame_height) == frame_size


def test_frames_fill_the_sprite_row_by_row() -> None:
    layout = storyboard_layout(25_000, 1920, 1080)  # 25 frames

    assert layout.rows == 3
    assert layout.sprite_size() == (1600, 270)
    assert [layout.frame_box(index) for index in (0, 9, 10, 24)] == [(0, 0), (1440, 0), (0, 90), (640, 180)]


def test_layout_survives_json() -> None:
    layout = storyboard_layout(25_000, 1080, 1920)

    assert StoryboardLayout.from_json(layout.to_json()) == layout
