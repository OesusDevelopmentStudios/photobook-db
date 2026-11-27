# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

"""Signed URLs for streaming a video to a player that cannot send the login token (Qt Multimedia sends no headers).
A URL is signed for one file and one login, and expires after 12 hours."""

from photobook_db.services.stream._service import LIFETIME, SIGNING_KEY_SETTING, StreamGrant, StreamService

__all__ = ["LIFETIME", "SIGNING_KEY_SETTING", "StreamGrant", "StreamService"]
