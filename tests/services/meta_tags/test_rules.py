# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

import pytest

from photobook_db.services.meta_tags import meta_tags_for, video_meta_tags


def names(mime_type: str = "image/png", size: tuple[int, int] = (1000, 800), animated: bool = False) -> list[str]:
    return [str(tag) for tag in meta_tags_for(mime_type, *size, animated)]


def test_a_still_picture_has_its_kind_and_file_type() -> None:
    assert names("image/jpeg") == ["meta:still_image", "meta:jpeg"]


def test_an_animated_picture() -> None:
    assert names("image/gif", animated=True) == ["meta:animated", "meta:gif"]


def test_an_unknown_type_gets_no_file_type_tag() -> None:
    assert names("image/x-unknown") == ["meta:still_image"]


@pytest.mark.parametrize(
    ("size", "size_class"),
    [
        ((500, 500), "low_res"),
        ((501, 300), None),
        ((1600, 1200), "hi_res"),
        ((1200, 1600), "hi_res"),  # either side may be the long one
        ((1599, 1200), None),
        ((4000, 2000), "hi_res"),  # long enough for absurd_res, not wide enough
        ((3200, 2400), "absurd_res"),
        ((2400, 3200), "absurd_res"),
    ],
)
def test_size_class(size: tuple[int, int], size_class: str | None) -> None:
    expected = ["meta:still_image", "meta:png"] + ([f"meta:{size_class}"] if size_class else [])
    assert names(size=size) == expected


def video_names(container: str = "mp4", size: tuple[int, int] = (2560, 1440), has_sound: bool = True,
                duration_ms: int = 60_000) -> list[str]:
    return [str(tag) for tag in video_meta_tags(container, *size, has_sound, duration_ms)]


def test_a_video_has_its_kind_container_size_and_sound() -> None:
    assert video_names() == ["meta:video", "meta:mp4", "meta:hi_res", "meta:sound"]


def test_a_silent_small_video() -> None:
    expected = ["meta:video", "meta:webm", "meta:low_res", "meta:no_sound"]
    assert video_names("webm", (320, 240), has_sound=False) == expected


@pytest.mark.parametrize(("duration_ms", "long"), [(300_000, False), (300_001, True)])
def test_a_video_over_five_minutes_is_long(duration_ms: int, long: bool) -> None:
    assert ("meta:long_video" in video_names(duration_ms=duration_ms)) is long


def test_an_unknown_container_gets_no_file_type_tag() -> None:
    assert video_names("ogg", (1000, 800)) == ["meta:video", "meta:sound"]
