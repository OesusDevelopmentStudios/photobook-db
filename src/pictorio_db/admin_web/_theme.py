# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

"""The look of the pages: KDE's Breeze Dark colours with the accent colour the admin chose."""

# The accent colours Plasma's own Colours settings offer, so the pages can match the desktop.
PLASMA_ACCENT_COLORS = [
    ("Pink", "#e93a9a"),
    ("Red", "#e93d58"),
    ("Orange", "#e9643a"),
    ("Yellow", "#e8cb2d"),
    ("Green", "#3dd425"),
    ("Teal", "#00d3b8"),
    ("Blue", "#3daee9"),
    ("Purple", "#b875dc"),
    ("Violet", "#926ee4"),
    ("Grey", "#686b6f"),
]

LIGHT_TEXT = "#fcfcfc"  # Breeze text colours
DARK_TEXT = "#232629"


def readable_text_on(background: str) -> str:
    """Light or dark text, whichever contrasts more with the background (WCAG 2 contrast ratio)."""
    background_luminance = _relative_luminance(background)
    light_contrast = (_relative_luminance(LIGHT_TEXT) + 0.05) / (background_luminance + 0.05)
    dark_contrast = (background_luminance + 0.05) / (_relative_luminance(DARK_TEXT) + 0.05)
    return LIGHT_TEXT if light_contrast >= dark_contrast else DARK_TEXT


def _relative_luminance(color: str) -> float:
    """Of a "#rrggbb" colour, from 0 (black) to 1 (white), as WCAG 2 defines it."""
    channels = [int(color[index:index + 2], 16) / 255 for index in (1, 3, 5)]
    red, green, blue = [
        channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4 for channel in channels
    ]
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue
