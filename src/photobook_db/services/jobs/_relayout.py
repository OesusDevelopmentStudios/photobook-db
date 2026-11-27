# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

import logging
from typing import Any

from photobook_db.services.media import MediaService
from photobook_db.services.jobs._service import JobContext, JobInfo, JobKind, JobsService

_logger = logging.getLogger(__name__)


class Relayout:
    """Moves files to where the path template places them: all of them after the template changed, or some after
    their tags changed. Media imported or retagged meanwhile already follow the template."""

    def __init__(self, jobs: JobsService, media: MediaService) -> None:
        self._jobs = jobs
        self._media = media

    def start(self, user_id: int | None, media_ids: list[int] | None = None) -> JobInfo:
        """None: all media."""
        params = {} if media_ids is None else {"image_ids": media_ids}
        return self._jobs.submit(JobKind.RELAYOUT, params, user_id)

    def run(self, context: JobContext) -> dict[str, Any]:
        media_ids = context.params.get("image_ids")
        if media_ids is None:
            media_ids = self._media.all_media_ids()

        moved = 0
        failed: list[dict[str, Any]] = []
        context.report_progress(0, len(media_ids))
        for done, media_id in enumerate(media_ids, start=1):
            try:
                moved += self._media.move_file_to_layout(media_id)
            except Exception:
                _logger.exception("Could not move the file of media %s to the new layout", media_id)
                failed.append({"image_id": media_id, "code": "move_failed"})  # it stays where it was

            context.report_progress(done, len(media_ids))

        return {"moved": moved, "unchanged": len(media_ids) - moved - len(failed), "failed": failed}
