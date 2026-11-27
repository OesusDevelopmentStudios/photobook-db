# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

"""Photos and videos and the tags on them."""

from photobook_db.services.media._service import (
    MediaRecord,
    MediaService,
    NewMedia,
    duplicate_media,
)

__all__ = ["MediaRecord", "MediaService", "NewMedia", "duplicate_media"]
