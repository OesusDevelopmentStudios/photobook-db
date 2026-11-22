# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

import pytest

from photobook_db.services.meta_tags import meta_tags_for


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
