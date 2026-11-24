# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

"""What FFmpeg knows of a video: its container, streams, size and dates, and its frames. The only service that runs
FFmpeg; without it installed, the server works as before and knows no videos."""

from photobook_db.services.media_probe._probe_output import VideoInfo
from photobook_db.services.media_probe._service import MediaProbe

__all__ = ["MediaProbe", "VideoInfo"]
