# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from photobook_db.db import Database
from photobook_db.db.models import Tag, User
from photobook_db.services.media import MediaService, NewMedia
from photobook_db.services.settings import SettingsService
from photobook_db.services.storage import StorageService
from photobook_db.services.tags import TagName, TagsService
from photobook_db.utils.problems import Problem

BEACH = NewMedia(
    sha256="3fa2b1c9".ljust(64, "0"),
    storage_path="photo/_unknown/2024/beach_3fa2b1c9.jpg",
    original_filename="beach.jpg",
    mime_type="image/jpeg",
    width=4032,
    height=3024,
    file_size=2_481_234,
    taken_at=datetime(2024, 7, 14, 16, 2, 11),
    taken_at_source="exif",
    taken_at_utc_offset_minutes=120,
    imported_by_user_id=None,
)


@pytest.fixture
def images(database: Database, tmp_path: Path) -> MediaService:
    tags = TagsService(database, lambda: storage.path_template_namespaces())
    storage = StorageService(database, SettingsService(database), tmp_path / "library", tags.namespace_names)
    return MediaService(database, tags, storage)


def another_image(number: int) -> NewMedia:
    return replace(BEACH, sha256=f"{number:064x}", storage_path=f"photo/{number}.jpg")


def test_creates_and_returns_an_image(images: MediaService) -> None:
    created = images.create_media(BEACH, [TagName("general", "summer")])

    fetched = images.get_media(created.id)
    assert fetched == created
    assert (fetched.sha256, fetched.storage_path, fetched.width, fetched.taken_at_source) == (
        BEACH.sha256,
        BEACH.storage_path,
        4032,
        "exif",
    )
    assert fetched.imported_at == fetched.updated_at


def test_tags_are_sorted_by_namespace_then_name(images: MediaService) -> None:
    tags = [TagName("general", "summer"), TagName("category", "photo"), TagName("general", "beach")]

    created = images.create_media(BEACH, tags)

    assert [str(tag) for tag in created.tags] == ["category:photo", "general:beach", "general:summer"]


def test_reuses_existing_tags_and_ignores_repeats(images: MediaService, database: Database) -> None:
    images.create_media(BEACH, [TagName("general", "summer"), TagName("general", "summer")])
    images.create_media(another_image(2), [TagName("general", "summer")])

    with database.session() as session:
        assert session.scalar(select(func.count()).select_from(Tag).where(Tag.name == "summer")) == 1


def test_same_sha256_is_a_duplicate(images: MediaService) -> None:
    first = images.create_media(BEACH, [])

    with pytest.raises(Problem) as raised:
        images.create_media(replace(BEACH, storage_path="elsewhere.jpg"), [TagName("general", "new_tag")])

    assert (raised.value.status, raised.value.code) == (409, "duplicate_image")
    assert raised.value.extra == {"sha256": BEACH.sha256, "existing_image_id": first.id}
    assert images.get_media(first.id).tags == []  # tags are not merged into the existing image


def test_taken_storage_path_is_not_reported_as_duplicate(images: MediaService) -> None:
    images.create_media(BEACH, [])

    with pytest.raises(IntegrityError):
        images.create_media(replace(BEACH, sha256="f" * 64), [])


def test_finds_images_by_sha256(images: MediaService) -> None:
    created = images.create_media(BEACH, [])

    assert images.find_id_by_sha256(BEACH.sha256) == created.id
    assert images.find_id_by_sha256("0" * 64) is None


def test_unknown_image_is_not_found(images: MediaService) -> None:
    with pytest.raises(Problem) as raised:
        images.get_media(999)

    assert (raised.value.status, raised.value.code) == (404, "not_found")


def test_image_stays_when_its_importer_is_deleted(images: MediaService, database: Database) -> None:
    with database.session() as session:
        user = User(username="restricted_user", password_hash="x", role="user", created_at=datetime(2026, 1, 1))
        session.add(user)
        session.commit()
        user_id = user.id
    created = images.create_media(replace(BEACH, imported_by_user_id=user_id), [])

    with database.session() as session:
        session.execute(delete(User).where(User.id == user_id))
        session.commit()

    assert images.get_media(created.id).imported_by_user_id is None
