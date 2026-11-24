# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

"""The tags of the "meta" namespace: what a file is, read from the file itself. The server alone sets them."""

from photobook_db.services.meta_tags._rules import meta_tags_for, video_meta_tags

__all__ = ["meta_tags_for", "video_meta_tags"]
