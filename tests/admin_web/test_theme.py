# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

import pytest

from pictorio_db.admin_web._theme import DARK_TEXT, LIGHT_TEXT, PLASMA_ACCENT_COLORS, readable_text_on


@pytest.mark.parametrize(
    "background, text",
    [("#000000", LIGHT_TEXT), ("#ffffff", DARK_TEXT), ("#e8cb2d", DARK_TEXT), ("#686b6f", LIGHT_TEXT)],
    ids=["black", "white", "plasma yellow", "plasma grey"],
)
def test_picks_the_text_colour_that_is_easier_to_read(background: str, text: str) -> None:
    assert readable_text_on(background) == text


def test_plasma_blue_is_one_of_the_swatches() -> None:
    assert ("Blue", "#3daee9") in PLASMA_ACCENT_COLORS
