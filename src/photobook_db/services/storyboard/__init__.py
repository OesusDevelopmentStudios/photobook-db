# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

"""A video's storyboard: frames at a fixed interval in one WebP sprite, for previews above the app's scrub bar.
Made on the first request rather than at import, so a folder import of many videos stays quick."""

from photobook_db.services.storyboard._layout import StoryboardLayout, storyboard_layout
from photobook_db.services.storyboard._service import StoryboardService, storyboard_unavailable

__all__ = ["StoryboardLayout", "StoryboardService", "storyboard_layout", "storyboard_unavailable"]
