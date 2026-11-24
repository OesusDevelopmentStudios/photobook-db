# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

"""Meta tags for images stored before the server set them on import, with real files and the real worker."""

import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from PIL import Image
from sqlalchemy import delete, select

from photobook_db.db import Database
from photobook_db.db.models import Namespace, Tag, image_tag
from photobook_db.services.access import Restrictions
from photobook_db.services.auth import AuthService, Role
from photobook_db.services.images import ImageRecord, ImagesService
from photobook_db.services.importer import ImporterService, UploadMetadata
from photobook_db.services.media_probe import MediaProbe
from photobook_db.services.jobs import JobInfo, JobKind, JobsService, JobStatus, MetaTagging
from photobook_db.services.settings import SettingsService
from photobook_db.services.storage import StorageService
from photobook_db.services.tags import TagName, TagsService
from photobook_db.services.thumbnails import ThumbnailsService


class Library:
    def __init__(self, database: Database, tmp_path: Path) -> None:
        self.database = database
        settings = SettingsService(database)
        tags = TagsService(database, lambda: self.storage.path_template_namespaces())
        self.storage = StorageService(database, settings, tmp_path / "library", tags.folder_namespace_names)
        self.images = ImagesService(database, tags, self.storage)
        self.importer = ImporterService(
            self.images, tags, self.storage, ThumbnailsService(tmp_path / "t"), settings, MediaProbe()
        )
        self.jobs = JobsService(database)
        self.meta_tagging = MetaTagging(self.jobs, self.images, self.storage)
        self.jobs.register(JobKind.META_TAGS, self.meta_tagging.run)
        self.admin = AuthService(database).create_user("admin_user", "correct horse battery", Role.ADMIN)

    def add_older_image(self, colour: str, frames: int = 1, tags: tuple[str, ...] = ("summer",)) -> ImageRecord:
        """As stored before 0.2.0: without meta tags. A GIF with `frames` frames, the first in `colour`."""
        incoming = self.importer.new_incoming_path()
        pictures = [Image.new("RGB", (8, 8), shade) for shade in (colour, "black", "white")[:frames]]
        pictures[0].save(incoming, format="GIF", save_all=True, append_images=pictures[1:])
        image = self.importer.import_image(incoming, UploadMetadata(f"{colour}.gif", tags=tags), None, Restrictions([]))
        self._remove_meta_tags(image.id)
        return image

    def _remove_meta_tags(self, image_id: int) -> None:
        meta_tag_ids = select(Tag.id).join(Namespace).where(Namespace.name == "meta")
        with self.database.session() as session:
            session.execute(
                delete(image_tag).where(image_tag.c.image_id == image_id, image_tag.c.tag_id.in_(meta_tag_ids))
            )
            session.commit()

    def tags_of(self, image: ImageRecord) -> list[str]:
        return [str(tag) for tag in self.images.get_image(image.id).tags]

    def finished(self, job: JobInfo) -> JobInfo:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            job = self.jobs.get_job(job.id, self.admin)
            if job.status in (JobStatus.DONE, JobStatus.FAILED):
                return job

            time.sleep(0.01)

        raise AssertionError("The job did not finish in time")


@pytest.fixture
def library(database: Database, tmp_path: Path) -> Library:
    return Library(database, tmp_path)


@pytest.fixture
def worker(library: Library) -> Iterator[None]:
    library.jobs.start()
    yield
    library.jobs.stop()


def test_gives_older_images_their_meta_tags(library: Library, worker: None) -> None:
    still = library.add_older_image("red")
    moving = library.add_older_image("green", frames=3)
    assert library.tags_of(still) == ["general:summer"]

    job = library.meta_tagging.start_if_needed()

    assert job is not None
    assert library.finished(job).result == {"tagged": 2, "failed": []}
    assert library.tags_of(still) == ["general:summer", "meta:gif", "meta:low_res", "meta:still_image"]
    assert library.tags_of(moving) == ["general:summer", "meta:animated", "meta:gif", "meta:low_res"]


def test_nothing_to_do_when_every_image_has_them(library: Library) -> None:
    image = library.add_older_image("red")
    library.images.set_meta_tags(image.id, [TagName("meta", "gif")])

    assert library.meta_tagging.start_if_needed() is None


def test_is_not_queued_twice(library: Library) -> None:
    library.add_older_image("red")

    assert library.meta_tagging.start_if_needed() is not None  # no worker: it waits
    assert library.meta_tagging.start_if_needed() is None


def test_an_unreadable_file_is_reported_and_the_rest_tagged(library: Library, worker: None) -> None:
    missing = library.add_older_image("red")
    found = library.add_older_image("green", tags=("beach",))
    library.storage.absolute_path(missing.storage_path).unlink()

    job = library.finished(library.meta_tagging.start_if_needed())  # type: ignore[arg-type]

    assert job.result == {"tagged": 1, "failed": [{"image_id": missing.id, "code": "unreadable"}]}
    assert "meta:gif" in library.tags_of(found)
    assert library.tags_of(missing) == ["general:summer"]


def test_new_meta_tags_replace_the_old_ones_only(library: Library) -> None:
    image = library.add_older_image("red")
    library.images.set_meta_tags(image.id, [TagName("meta", "gif"), TagName("meta", "low_res")])

    library.images.set_meta_tags(image.id, [TagName("meta", "gif"), TagName("meta", "animated")])

    assert library.tags_of(image) == ["general:summer", "meta:animated", "meta:gif"]
