# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

"""Changing the tags of images, with real files: a change to a folder-forming tag moves the file."""

import logging
from datetime import datetime
from pathlib import Path

import pytest
from PIL import ExifTags, Image
from sqlalchemy import select, update

from photobook_db.db import Database
from photobook_db.db.models import Namespace, Tag, media_tag
from photobook_db.services.access import Restrictions
from photobook_db.services.media import MediaRecord, MediaService
from photobook_db.services.importer import ImporterService, UploadMetadata
from photobook_db.services.media_probe import MediaProbe
from photobook_db.services.settings import SettingsService
from photobook_db.services.storage import StorageService
from photobook_db.services.tags import TagName, TagsService
from photobook_db.services.thumbnails import ThumbnailsService
from photobook_db.utils.problems import Problem
from photobook_db.utils.time import utc_now

from ...tag_lists import user_tags

NO_RESTRICTIONS = Restrictions([])


class Library:
    def __init__(self, database: Database, tmp_path: Path) -> None:
        self.database = database
        self.library_dir = tmp_path / "library"
        settings = SettingsService(database)
        tags = TagsService(database, lambda: self.storage.path_template_namespaces())
        self.storage = StorageService(database, settings, self.library_dir, tags.namespace_names)
        self.images = MediaService(database, tags, self.storage)
        thumbnails = ThumbnailsService(tmp_path / "thumbnails")
        self.importer = ImporterService(self.images, tags, self.storage, thumbnails, settings, MediaProbe())

    def add_media(self, colour: str, tags: tuple[str, ...]) -> MediaRecord:
        """Taken in 2024, so its folder is <category>/<author>/2024."""
        incoming = self.importer.new_incoming_path()
        exif = Image.Exif()
        exif.get_ifd(ExifTags.IFD.Exif)[ExifTags.Base.DateTimeOriginal] = "2024:05:01 10:00:00"
        Image.new("RGB", (8, 8), colour).save(incoming, format="JPEG", exif=exif)
        return self.importer.import_media(
            incoming, UploadMetadata(f"{colour}.jpg", tags=tags), user_id=None, restrictions=NO_RESTRICTIONS
        )

    def change_tags(self, media_id: int, add_tags: list[str], remove_tags: list[str]) -> MediaRecord:
        """For a user without restrictions."""
        return self.images.change_tags(media_id, add_tags, remove_tags, NO_RESTRICTIONS)

    def change_tags_of_many(self, media_ids: list[int], add_tags: list[str], remove_tags: list[str]) -> int:
        return self.images.change_tags_of_many(media_ids, add_tags, remove_tags, NO_RESTRICTIONS)

    def folder_of(self, record: MediaRecord) -> str:
        return str(Path(record.storage_path).parent)

    def file_exists(self, record: MediaRecord) -> bool:
        return (self.library_dir / record.storage_path).is_file()

    def added_at(self, media_id: int, tag: str) -> datetime | None:
        """When the image got the tag; None if it does not have it."""
        return self.added_times(tag).get(media_id)

    def added_times(self, tag: str) -> dict[int, datetime]:
        namespace, name = tag.split(":")
        query = (
            select(media_tag.c.media_id, media_tag.c.added_at)
            .join(Tag, Tag.id == media_tag.c.tag_id)
            .join(Namespace, Namespace.id == Tag.namespace_id)
            .where(Namespace.name == namespace, Tag.name == name)
        )
        with self.database.session() as session:
            return dict(session.execute(query).all())

    def backdate(self, media_id: int, tag: str, when: datetime) -> None:
        """As if the image had got the tag at `when`."""
        namespace, name = tag.split(":")
        tag_id = select(Tag.id).join(Namespace).where(Namespace.name == namespace, Tag.name == name)
        with self.database.session() as session:
            session.execute(
                update(media_tag)
                .where(media_tag.c.media_id == media_id, media_tag.c.tag_id == tag_id.scalar_subquery())
                .values(added_at=when)
            )
            session.commit()


@pytest.fixture
def library(database: Database, tmp_path: Path) -> Library:
    return Library(database, tmp_path)


def tag_texts(record: MediaRecord) -> list[str]:
    return user_tags(record.tags)


def problem_of(action: object) -> Problem:
    with pytest.raises(Problem) as raised:
        action()  # type: ignore[operator]
    return raised.value


# --- One image

def test_adds_and_removes_tags(library: Library) -> None:
    image = library.add_media("red", ("category:photo", "summer", "beach"))

    changed = library.change_tags(
        image.id, add_tags=["Sunset Sky", "author:sample_artist"], remove_tags=["beach"]
    )

    assert tag_texts(changed) == ["author:sample_artist", "category:photo", "general:summer", "general:sunset_sky"]
    assert changed.updated_at >= image.updated_at


@pytest.mark.parametrize("change", [{"add_tags": ["meta:video"]}, {"remove_tags": ["meta:jpeg"]}])
def test_meta_tags_are_neither_added_nor_removed(library: Library, change: dict[str, list[str]]) -> None:
    image = library.add_media("red", ("summer",))
    before = [str(tag) for tag in library.images.get_media(image.id).tags]

    problem = problem_of(lambda: library.change_tags(image.id, **({"add_tags": [], "remove_tags": []} | change)))

    assert (problem.status, problem.code) == (422, "tag_read_only")
    assert [str(tag) for tag in library.images.get_media(image.id).tags] == before


def test_removing_a_tag_the_image_lacks_changes_nothing(library: Library) -> None:
    image = library.add_media("red", ("summer",))

    unchanged = library.change_tags(image.id, add_tags=[], remove_tags=["winter"])

    assert unchanged == image  # not even updated_at


def test_tag_both_added_and_removed_stays(library: Library) -> None:
    image = library.add_media("red", ("summer",))

    changed = library.change_tags(image.id, add_tags=["summer"], remove_tags=["summer"])

    assert tag_texts(changed) == ["general:summer"]


def test_new_tag_in_a_single_value_namespace_replaces_the_old_one(library: Library) -> None:
    image = library.add_media("red", ("category:photo",))

    changed = library.change_tags(image.id, add_tags=["category:artwork"], remove_tags=[])

    assert tag_texts(changed) == ["category:artwork"]


def test_moves_the_file_when_its_folder_changes(library: Library) -> None:
    image = library.add_media("red", ("category:photo",))
    assert library.folder_of(image) == "photo/_unknown/2024"

    changed = library.change_tags(
        image.id, add_tags=["category:artwork", "author:sample_artist"], remove_tags=[]
    )

    assert library.folder_of(changed) == "artwork/sample_artist/2024"
    assert Path(changed.storage_path).name == Path(image.storage_path).name
    assert library.file_exists(changed)
    assert not (library.library_dir / "photo").exists()  # the empty old folders are gone


def test_keeps_the_file_when_the_folder_stays(library: Library) -> None:
    image = library.add_media("red", ("category:photo",))

    changed = library.change_tags(image.id, add_tags=["summer"], remove_tags=[])

    assert changed.storage_path == image.storage_path


def test_file_name_taken_in_the_new_folder_gets_a_number(library: Library) -> None:
    image = library.add_media("red", ("category:photo",))
    occupied = library.library_dir / "artwork/_unknown/2024" / Path(image.storage_path).name
    occupied.parent.mkdir(parents=True)
    occupied.write_bytes(b"another file")

    changed = library.change_tags(image.id, add_tags=["category:artwork"], remove_tags=[])

    assert changed.storage_path.endswith("_2.jpg")
    assert occupied.read_bytes() == b"another file"


def test_invalid_tag_changes_nothing(library: Library) -> None:
    image = library.add_media("red", ("summer",))

    problem = problem_of(lambda: library.change_tags(image.id, add_tags=["ok", "a|b"], remove_tags=[]))

    assert (problem.status, problem.code) == (422, "invalid_tag")
    assert library.images.get_media(image.id) == image


def test_unknown_image_is_not_found(library: Library) -> None:
    assert problem_of(lambda: library.change_tags(999, add_tags=["summer"], remove_tags=[])).status == 404


def test_failed_move_keeps_the_tags_and_the_file_where_recorded(
    library: Library, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    image = library.add_media("red", ("category:photo",))

    def disk_unavailable(*_arguments: object) -> None:
        raise OSError("disk unavailable")

    monkeypatch.setattr(library.storage, "move_media_file", disk_unavailable)
    with caplog.at_level(logging.ERROR):
        changed = library.change_tags(image.id, add_tags=["category:artwork"], remove_tags=[])

    assert tag_texts(changed) == ["category:artwork"]
    assert changed.storage_path == image.storage_path
    assert library.file_exists(changed)
    assert "Could not move the file" in caplog.text


# --- Many images

def test_changes_many_images_and_moves_their_files(library: Library) -> None:
    images = [library.add_media(colour, ("category:photo",)) for colour in ("red", "green", "blue")]

    updated = library.change_tags_of_many(
        [image.id for image in images], add_tags=["author:sample_artist", "album:trip"], remove_tags=[]
    )

    assert updated == 3
    for record in library.images.get_media_list([image.id for image in images]):
        assert tag_texts(record) == ["album:trip", "author:sample_artist", "category:photo"]
        assert library.folder_of(record) == "photo/sample_artist/2024"
        assert library.file_exists(record)


def test_unknown_id_changes_none_of_the_images(library: Library) -> None:
    image = library.add_media("red", ("summer",))

    problem = problem_of(
        lambda: library.change_tags_of_many([image.id, 999], add_tags=["album:trip"], remove_tags=[])
    )

    assert (problem.status, problem.detail) == (404, "One of the photos or videos does not exist.")
    assert library.images.get_media(image.id) == image


def test_repeated_ids_count_once(library: Library) -> None:
    image = library.add_media("red", ())

    assert library.change_tags_of_many([image.id, image.id], add_tags=["summer"], remove_tags=[]) == 1


def test_images_that_already_have_the_tag_are_not_touched(library: Library) -> None:
    tagged = library.add_media("red", ("summer",))
    untagged = library.add_media("green", ())

    library.change_tags_of_many([tagged.id, untagged.id], add_tags=["summer"], remove_tags=[])

    assert library.images.get_media(tagged.id).updated_at == tagged.updated_at
    assert tag_texts(library.images.get_media(untagged.id)) == ["general:summer"]


# --- When a tag was added (an album's order)

LONG_AGO = datetime(2025, 1, 1, 12, 0)


def test_a_tag_gets_the_time_it_was_added(library: Library) -> None:
    image = library.add_media("red", ("album:trip",))
    before = utc_now()

    library.change_tags(image.id, add_tags=["album:summer"], remove_tags=[])

    added = library.added_at(image.id, "album:summer")
    assert added is not None and added >= before


def test_other_changes_keep_the_time_a_tag_was_added(library: Library) -> None:
    image = library.add_media("red", ("album:trip",))
    library.backdate(image.id, "album:trip", LONG_AGO)

    library.change_tags(image.id, add_tags=["beach"], remove_tags=[])
    library.change_tags_of_many([image.id], add_tags=["album:trip", "sunset"], remove_tags=[])  # had it already

    assert library.added_at(image.id, "album:trip") == LONG_AGO


def test_a_rename_keeps_the_time_and_a_merge_keeps_the_existing_one(library: Library) -> None:
    renamed = library.add_media("red", ("album:trip",))
    merged = library.add_media("green", ("album:trip", "album:journey"))
    library.backdate(renamed.id, "album:trip", LONG_AGO)
    library.backdate(merged.id, "album:trip", LONG_AGO)
    library.backdate(merged.id, "album:journey", LONG_AGO.replace(year=2026))

    library.images.replace_tag(TagName("album", "trip"), TagName("album", "journey"))

    assert library.added_at(renamed.id, "album:journey") == LONG_AGO
    assert library.added_at(merged.id, "album:journey") == LONG_AGO.replace(year=2026)
