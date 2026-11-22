# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

import logging
from typing import Any

from photobook_db.services.images import ImagesService
from photobook_db.services.importer import inspect_image
from photobook_db.services.jobs._service import JobContext, JobInfo, JobKind, JobsService
from photobook_db.services.meta_tags import meta_tags_for
from photobook_db.services.storage import StorageService

_logger = logging.getLogger(__name__)


class MetaTagging:
    """Gives the images that have no meta tags theirs, read from their files: those stored before the server set
    meta tags on import."""

    def __init__(self, jobs: JobsService, images: ImagesService, storage: StorageService) -> None:
        self._jobs = jobs
        self._images = images
        self._storage = storage

    def start_if_needed(self) -> JobInfo | None:
        """At start-up; nothing if every image has them, or if the job is waiting or running already."""
        if self._jobs.has_unfinished_job(JobKind.META_TAGS) or not self._images.ids_without_meta_tags():
            return None

        return self._jobs.submit(JobKind.META_TAGS, {}, None)

    def run(self, context: JobContext) -> dict[str, Any]:
        image_ids = self._images.ids_without_meta_tags()
        tagged = 0
        failed: list[dict[str, Any]] = []
        context.report_progress(0, len(image_ids))
        for done, image_id in enumerate(image_ids, start=1):
            try:
                self._tag(image_id)
                tagged += 1
            except Exception:
                _logger.exception("Could not read the file of image %s for its meta tags", image_id)
                failed.append({"image_id": image_id, "code": "unreadable"})  # it stays without them

            context.report_progress(done, len(image_ids))

        return {"tagged": tagged, "failed": failed}

    def _tag(self, image_id: int) -> None:
        with self._storage.changing_files():  # not while the file moves or a rotation rewrites it
            image = self._images.get_image(image_id)
            facts = inspect_image(self._storage.absolute_path(image.storage_path))

        meta_tags = meta_tags_for(facts.type.mime_type, facts.width, facts.height, facts.animated)
        self._images.set_meta_tags(image_id, meta_tags)
