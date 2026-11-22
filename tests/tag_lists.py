# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

"""For tests about the tags people set: the server adds meta tags to every file it stores."""

from collections.abc import Iterable


def user_tags(tags: Iterable[object]) -> list[str]:
    """The tags as text, without the meta tags."""
    return [str(tag) for tag in tags if not str(tag).startswith("meta:")]
