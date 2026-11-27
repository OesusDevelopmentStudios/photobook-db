# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

import re
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import delete, inspect, select, text
from sqlalchemy.exc import IntegrityError

from photobook_db.db import DATABASE_FILE_NAME, Database, open_database
from photobook_db.db._connection import create_sqlite_engine
from photobook_db.db._migrations import migrate as migrate_database
from photobook_db.db.models import Base, Media, Namespace, Tag, media_tag


@pytest.fixture
def database_path(tmp_path: Path) -> Path:
    return tmp_path / DATABASE_FILE_NAME


@pytest.fixture
def database(database_path: Path) -> Iterator[Database]:
    opened_database = open_database(database_path)
    yield opened_database
    opened_database.close()


def now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def add_media_with_tag(database: Database) -> int:
    with database.session() as session:
        general = session.scalars(select(Namespace).where(Namespace.name == "general")).one()
        tag = Tag(namespace_id=general.id, name="summer")
        image = Media(
            sha256="ab" * 32,
            storage_path="photo/_unknown/2024/beach_abababab.jpg",
            original_filename="beach.jpg",
            mime_type="image/jpeg",
            width=40,
            height=30,
            file_size=1234,
            taken_at=now(),
            taken_at_source="import_time",
            imported_at=now(),
            updated_at=now(),
        )
        session.add_all([tag, image])
        session.flush()
        session.execute(media_tag.insert().values(media_id=image.id, tag_id=tag.id))
        session.commit()
        return image.id


def test_creates_tables_in_empty_database(database: Database) -> None:
    table_names = set(inspect(database.engine).get_table_names())

    assert {"namespace", "tag", "media", "media_tag"} <= table_names


def test_adds_builtin_namespaces(database: Database) -> None:
    with database.session() as session:
        namespaces = {namespace.name: namespace for namespace in session.scalars(select(Namespace))}

    assert set(namespaces) == {"category", "author", "character", "rating", "album", "general", "meta"}
    assert all(namespace.is_builtin for namespace in namespaces.values())
    assert {name for name, namespace in namespaces.items() if not namespace.user_editable} == {"meta"}
    single_value_names = {name for name, namespace in namespaces.items() if namespace.single_value}
    assert single_value_names == {"category", "rating"}


def test_uses_write_ahead_log_and_foreign_keys(database: Database) -> None:
    with database.engine.connect() as connection:
        assert connection.execute(text("PRAGMA journal_mode")).scalar() == "wal"
        assert connection.execute(text("PRAGMA foreign_keys")).scalar() == 1


def test_opening_again_keeps_data(database_path: Path) -> None:
    first_opening = open_database(database_path)
    add_media_with_tag(first_opening)
    first_opening.close()

    second_opening = open_database(database_path)
    with second_opening.session() as session:
        image_count = len(session.scalars(select(Media)).all())
        namespace_count = len(session.scalars(select(Namespace)).all())
    second_opening.close()

    assert image_count == 1
    assert namespace_count == 7  # the built-in namespaces are not added twice


def test_models_match_migrations(database: Database) -> None:
    with database.engine.connect() as connection:
        differences = compare_metadata(MigrationContext.configure(connection), Base.metadata)

    assert differences == [], "The models differ from the migrations; add a migration for the change."


def test_migrations_can_be_undone_and_redone(database: Database) -> None:
    migrate_database(database.engine, "base", downgrade=True)
    assert "media" not in inspect(database.engine).get_table_names()

    migrate_database(database.engine, "head")
    assert "media" in inspect(database.engine).get_table_names()


def migrate(database_path: Path, revision: str, downgrade: bool = False) -> None:
    """To a revision before the latest, as the server migrates: on its connection, foreign keys off meanwhile."""
    engine = create_sqlite_engine(database_path)
    migrate_database(engine, revision, downgrade)
    engine.dispose()


def tags_of(database: Database, media_id: int) -> set[str]:
    with database.session() as session:
        rows = session.execute(
            select(Namespace.name, Tag.name)
            .join(Tag, Tag.namespace_id == Namespace.id)
            .join(media_tag, media_tag.c.tag_id == Tag.id)
            .where(media_tag.c.media_id == media_id)
        )
        return {f"{namespace}:{name}" for namespace, name in rows}


# --- 0002: the meta namespace

def test_upgrade_from_0001_keeps_the_data_and_adds_meta(database_path: Path) -> None:
    migrate(database_path, "0001")
    engine = create_sqlite_engine(database_path)
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO namespace (name, single_value, is_builtin, created_at) VALUES ('weather', 0, 0, :now)"
        ), {"now": now().isoformat(sep=" ")})
    engine.dispose()
    old_database = open_database(database_path)
    media_id = add_media_with_tag(old_database)  # tagged general:summer
    old_database.close()

    database = open_database(database_path)  # the server upgrades the file on opening
    with database.session() as session:
        editable = {namespace.name: namespace.user_editable for namespace in session.scalars(select(Namespace))}
    image_tags = tags_of(database, media_id)
    database.close()

    assert editable == {name: name != "meta" for name in
                        ["category", "author", "character", "rating", "album", "general", "weather", "meta"]}
    assert image_tags == {"general:summer"}


def test_undoing_0002_removes_the_meta_tags_and_keeps_the_others(database_path: Path) -> None:
    database = open_database(database_path)
    media_id = add_media_with_tag(database)
    with database.session() as session:
        meta = session.scalars(select(Namespace).where(Namespace.name == "meta")).one()
        tag = Tag(namespace_id=meta.id, name="hi_res")
        session.add(tag)
        session.flush()
        session.execute(media_tag.insert().values(media_id=media_id, tag_id=tag.id))
        session.commit()
    database.close()

    migrate(database_path, "0001", downgrade=True)

    engine = create_sqlite_engine(database_path)
    with engine.connect() as connection:
        namespaces = set(connection.execute(text("SELECT name FROM namespace")).scalars())
        tags = set(connection.execute(text("SELECT name FROM tag")).scalars())
        links = connection.execute(text("SELECT COUNT(*) FROM image_tag")).scalar()
        columns = {column["name"] for column in inspect(connection).get_columns("namespace")}
    engine.dispose()
    assert "meta" not in namespaces
    assert tags == {"summer"}
    assert links == 1
    assert "user_editable" not in columns


def test_deleting_image_removes_its_tag_links(database: Database) -> None:
    media_id = add_media_with_tag(database)

    with database.session() as session:
        session.execute(delete(Media).where(Media.id == media_id))
        session.commit()
        remaining_links = session.execute(select(media_tag)).all()

    assert remaining_links == []


def test_tag_names_are_unique_within_a_namespace(database: Database) -> None:
    with database.session() as session:
        general = session.scalars(select(Namespace).where(Namespace.name == "general")).one()
        session.add_all([Tag(namespace_id=general.id, name="summer"), Tag(namespace_id=general.id, name="summer")])

        with pytest.raises(IntegrityError):
            session.commit()


# --- 0003: fixed values

def tag_names(database_path: Path, namespace: str) -> list[str]:
    engine = create_sqlite_engine(database_path)
    with engine.connect() as connection:
        names = connection.execute(text(
            "SELECT tag.name FROM tag JOIN namespace ON namespace.id = tag.namespace_id "
            "WHERE namespace.name = :namespace ORDER BY tag.id"
        ), {"namespace": namespace}).scalars().all()
    engine.dispose()
    return list(names)


def fixed_namespaces(database_path: Path) -> set[str]:
    engine = create_sqlite_engine(database_path)
    with engine.connect() as connection:
        names = connection.execute(text("SELECT name FROM namespace WHERE fixed_values")).scalars().all()
    engine.dispose()
    return set(names)


def stored_setting(database_path: Path, key: str) -> object:
    engine = create_sqlite_engine(database_path)
    with engine.connect() as connection:
        value = connection.execute(text("SELECT value FROM setting WHERE key = :key"), {"key": key}).scalar()
    engine.dispose()
    return value


def test_upgrade_to_0003_makes_the_stored_ratings_tags(database_path: Path) -> None:
    migrate(database_path, "0002")
    engine = create_sqlite_engine(database_path)
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO setting (key, value) VALUES ('rating_values', :value)"),
                           {"value": '["public", "members_only"]'})
    engine.dispose()

    migrate(database_path, "0003")

    assert fixed_namespaces(database_path) == {"rating"}
    assert tag_names(database_path, "rating") == ["public", "members_only"]  # in their order
    assert stored_setting(database_path, "rating_values") is None


def test_without_stored_ratings_the_defaults_become_tags(database_path: Path) -> None:
    open_database(database_path).close()

    assert tag_names(database_path, "rating") == ["public", "private"]


def test_undoing_0003_stores_the_ratings_again_and_drops_unused_tags(database_path: Path) -> None:
    database = open_database(database_path)
    media_id = add_media_with_tag(database)
    with database.session() as session:
        rating_tag = select(Tag).join(Namespace).where(Namespace.name == "rating", Tag.name == "public")
        public = session.scalars(rating_tag).one()
        session.execute(media_tag.insert().values(media_id=media_id, tag_id=public.id))
        session.commit()
    database.close()

    migrate(database_path, "0002", downgrade=True)

    assert stored_setting(database_path, "rating_values") == '["public", "private"]'
    assert tag_names(database_path, "rating") == ["public"]  # private was a value only; public is on an image


# --- 0004: category values

def test_upgrade_to_0004_makes_the_categories_values(database_path: Path) -> None:
    migrate(database_path, "0003")
    engine = create_sqlite_engine(database_path)
    with engine.begin() as connection:  # a category in use, from before
        connection.execute(
            text("INSERT INTO tag (namespace_id, name) SELECT id, 'refs' FROM namespace WHERE name = 'category'")
        )
    engine.dispose()

    migrate(database_path, "0004")

    assert fixed_namespaces(database_path) == {"rating", "category"}
    assert tag_names(database_path, "category") == ["refs", "photo", "artwork"]


def test_undoing_0004_keeps_only_the_categories_images_have(database_path: Path) -> None:
    database = open_database(database_path)
    media_id = add_media_with_tag(database)
    with database.session() as session:
        photo_tag = select(Tag).join(Namespace).where(Namespace.name == "category", Tag.name == "photo")
        photo = session.scalars(photo_tag).one()
        session.execute(media_tag.insert().values(media_id=media_id, tag_id=photo.id))
        session.commit()
    database.close()

    migrate(database_path, "0003", downgrade=True)

    assert fixed_namespaces(database_path) == {"rating"}
    assert tag_names(database_path, "category") == ["photo"]  # artwork was a value only


# --- 0005: when a tag was added

def test_upgrade_to_0005_dates_the_links_by_their_images_import(database_path: Path) -> None:
    migrate(database_path, "0004")
    engine = create_sqlite_engine(database_path)
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO image (sha256, storage_path, original_filename, mime_type, width, height, file_size, "
            "taken_at, taken_at_source, imported_at, updated_at, revision) VALUES "
            "(:sha256, 'beach.jpg', 'beach.jpg', 'image/jpeg', 1, 1, 1, :moment, 'exif', :imported, :moment, 0)"
        ), {"sha256": "ab" * 32, "moment": "2024-05-01 10:00:00", "imported": "2025-03-04 05:06:07"})
        connection.execute(text(
            "INSERT INTO image_tag (image_id, tag_id) SELECT 1, id FROM tag WHERE name = 'photo'"
        ))
    engine.dispose()

    migrate(database_path, "0005")

    engine = create_sqlite_engine(database_path)
    with engine.connect() as connection:
        added_at = connection.execute(text("SELECT added_at FROM image_tag")).scalar()
        indexes = {index["name"] for index in inspect(connection).get_indexes("image_tag")}
    engine.dispose()
    assert str(added_at).startswith("2025-03-04 05:06:07")
    assert "ix_image_tag_tag_id_added_at_image_id" in indexes


def test_undoing_0005_drops_the_times(database_path: Path) -> None:
    database = open_database(database_path)
    add_media_with_tag(database)
    database.close()

    migrate(database_path, "0004", downgrade=True)

    engine = create_sqlite_engine(database_path)
    with engine.connect() as connection:
        columns = {column["name"] for column in inspect(connection).get_columns("image_tag")}
        links = connection.execute(text("SELECT COUNT(*) FROM image_tag")).scalar()
    engine.dispose()
    assert "added_at" not in columns and links == 1


# --- 0006: videos in the image table

def count_rows(database_path: Path, table: str) -> int:
    engine = create_sqlite_engine(database_path)
    with engine.connect() as connection:
        count = connection.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar() or 0
    engine.dispose()
    return count


def add_file_move(database_path: Path, media_id: int) -> None:
    engine = create_sqlite_engine(database_path)
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO file_move (image_id, from_path, to_path, created_at) "
            "VALUES (:image_id, 'a.jpg', 'b.jpg', '2025-01-01 00:00:00')"
        ), {"image_id": media_id})
    engine.dispose()


# The image table is copied to change it; the rows that refer to its images must all stay.
def test_upgrade_to_0006_keeps_every_image_and_what_refers_to_it(database_path: Path) -> None:
    migrate(database_path, "0005")  # not open_database(): it would migrate to the latest first
    engine = create_sqlite_engine(database_path)
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO image (sha256, storage_path, original_filename, mime_type, width, height, file_size, "
            "taken_at, taken_at_source, imported_at, updated_at, revision) VALUES "
            "(:sha256, 'beach.jpg', 'beach.jpg', 'image/jpeg', 1, 1, 1, :moment, 'exif', :moment, :moment, 0)"
        ), {"sha256": "ab" * 32, "moment": "2024-05-01 10:00:00"})
        connection.execute(text(
            "INSERT INTO image_tag (image_id, tag_id, added_at) "
            "SELECT 1, id, '2024-05-01 10:00:00' FROM tag WHERE name = 'photo'"
        ))
    engine.dispose()
    add_file_move(database_path, 1)
    assert (count_rows(database_path, "image_tag"), count_rows(database_path, "file_move")) == (1, 1)

    migrate(database_path, "0006")

    assert count_rows(database_path, "image") == 1
    assert count_rows(database_path, "image_tag") == 1
    assert count_rows(database_path, "file_move") == 1
    engine = create_sqlite_engine(database_path)
    with engine.connect() as connection:
        kind = connection.execute(text("SELECT media_kind FROM image")).scalar()
        indexes = {index["name"] for index in inspect(connection).get_indexes("image")}
    engine.dispose()
    assert kind == "image"
    assert {"ix_image_taken_at_id", "ix_image_imported_at_id"} <= indexes


def test_0006_takes_videos_with_their_own_date(database: Database) -> None:
    media_id = add_media_with_tag(database)
    with database.session() as session:
        video = session.get(Media, media_id)
        video.media_kind = "video"
        video.taken_at_source = "container"
        video.duration_ms = 222400
        video.frame_rate = 29.97
        session.commit()

    with database.session() as session:
        stored = session.get(Media, media_id)
        assert (stored.media_kind, stored.taken_at_source, stored.frame_rate) == ("video", "container", 29.97)


def test_0006_refuses_an_unknown_kind(database: Database) -> None:
    media_id = add_media_with_tag(database)
    with database.session() as session, pytest.raises(IntegrityError):
        session.get(Media, media_id).media_kind = "sound"
        session.commit()


def test_undoing_0006_keeps_the_images_and_their_links(database_path: Path) -> None:
    database = open_database(database_path)
    media_id = add_media_with_tag(database)
    database.close()
    migrate(database_path, "0006", downgrade=True)  # the tables as 0006 named them
    add_file_move(database_path, media_id)

    migrate(database_path, "0005", downgrade=True)

    assert (count_rows(database_path, "image"), count_rows(database_path, "image_tag")) == (1, 1)
    assert count_rows(database_path, "file_move") == 1
    engine = create_sqlite_engine(database_path)
    with engine.connect() as connection:
        columns = {column["name"] for column in inspect(connection).get_columns("image")}
    engine.dispose()
    assert "media_kind" not in columns


# The schema before 0006 cannot hold a video; going back must not lose one quietly.
def test_undoing_0006_refuses_while_videos_are_stored(database_path: Path) -> None:
    database = open_database(database_path)
    media_id = add_media_with_tag(database)
    with database.session() as session:
        session.get(Media, media_id).media_kind = "video"
        session.commit()
    database.close()
    migrate(database_path, "0006", downgrade=True)

    with pytest.raises(RuntimeError, match="videos are stored"):
        migrate(database_path, "0005", downgrade=True)

    assert count_rows(database_path, "image") == 1


# --- 0007: media instead of image

def rows(database_path: Path, query: str) -> list[tuple[object, ...]]:
    engine = create_sqlite_engine(database_path)
    with engine.connect() as connection:
        found = [tuple(row) for row in connection.execute(text(query))]
    engine.dispose()
    return found


def schema(database_path: Path) -> list[tuple[str, str, str]]:
    """(type, name, SQL) of every table and index."""
    return [(kind, name, sql or "") for kind, name, sql in rows(  # type: ignore[misc]
        database_path, "SELECT type, name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY name"
    )]


def add_0006_rows(database_path: Path) -> None:
    """A user, a photo and a video, their tag links and a file move, as 0006 names the tables."""
    engine = create_sqlite_engine(database_path)
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO user (id, username, password_hash, role, created_at) "
            "VALUES (5, 'sample_user', 'x', 'user', '2024-05-01 09:00:00')"
        ))
        connection.execute(text(
            "INSERT INTO image (id, sha256, storage_path, original_filename, mime_type, width, height, file_size, "
            "taken_at, taken_at_source, taken_at_utc_offset_minutes, imported_at, updated_at, imported_by_user_id, "
            "revision, media_kind, duration_ms, video_codec, audio_codec, bitrate, frame_rate) VALUES "
            "(3, :a, 'beach.jpg', 'beach.jpg', 'image/jpeg', 40, 30, 1234, '2024-05-01 10:00:00', 'exif', 120, "
            "'2024-05-02 10:00:00', '2024-05-03 10:00:00', 5, 2, 'image', NULL, NULL, NULL, NULL, NULL), "
            "(8, :b, 'waves.mp4', 'waves.mp4', 'video/mp4', 1920, 1080, 98765, '2024-06-01 10:00:00', 'container', "
            "NULL, '2024-06-02 10:00:00', '2024-06-02 10:00:00', NULL, 0, 'video', 222400, 'h264', 'aac', 8120000, "
            "29.97)"
        ), {"a": "ab" * 32, "b": "cd" * 32})
        connection.execute(text(
            "INSERT INTO image_tag (image_id, tag_id, added_at) "
            "SELECT 3, id, '2024-05-04 10:00:00' FROM tag WHERE name = 'photo' UNION ALL "
            "SELECT 8, id, '2024-06-04 10:00:00' FROM tag WHERE name = 'photo'"
        ))
    engine.dispose()
    add_file_move(database_path, 8)


def comparable(found: list[tuple[str, str, str]]) -> list[tuple[str, str, list[str]]]:
    """Without what SQLite writes differently for the same schema: a renamed table's quotes, the constraints' order."""
    return [
        (kind, name, sorted(line.strip() for line in sql.replace('"', "").replace(",", "").split("\n")))
        for kind, name, sql in found
    ]


MEDIA_ROWS = "SELECT * FROM {table} ORDER BY id"
LINK_ROWS = "SELECT {table}_id, tag_id, added_at FROM {table}_tag ORDER BY {table}_id"
MOVE_ROWS = "SELECT id, {table}_id, from_path, to_path, created_at FROM file_move"


def test_upgrade_to_0007_keeps_every_row_under_the_new_names(database_path: Path) -> None:
    migrate(database_path, "0006")
    add_0006_rows(database_path)
    before = [rows(database_path, query.format(table="image")) for query in (MEDIA_ROWS, LINK_ROWS, MOVE_ROWS)]

    migrate(database_path, "0007")

    after = [rows(database_path, query.format(table="media")) for query in (MEDIA_ROWS, LINK_ROWS, MOVE_ROWS)]
    assert after == before
    assert [len(found) for found in after] == [2, 2, 1]
    tables = {name for kind, name, _sql in schema(database_path) if kind == "table"}
    assert {"media", "media_tag", "file_move"} <= tables and not {"image", "image_tag"} & tables


def test_undoing_0007_keeps_every_row_under_the_old_names(database_path: Path) -> None:
    migrate(database_path, "0006")
    add_0006_rows(database_path)
    before = [rows(database_path, query.format(table="image")) for query in (MEDIA_ROWS, LINK_ROWS, MOVE_ROWS)]
    schema_before = schema(database_path)
    migrate(database_path, "0007")

    migrate(database_path, "0006", downgrade=True)

    assert [rows(database_path, query.format(table="image")) for query in (MEDIA_ROWS, LINK_ROWS, MOVE_ROWS)] == before
    # The same tables, indexes and constraints as 0006 made
    assert comparable(schema(database_path)) == comparable(schema_before)


def test_no_name_in_the_schema_says_image(database: Database, database_path: Path) -> None:
    """Only the media kind 'image' itself: every table, column, index and constraint says media."""
    for _kind, name, sql in schema(database_path):
        without_values = re.sub(r"'[^']*'", "''", sql)
        assert "image" not in name and "image" not in without_values, sql


def test_constraints_are_named_as_the_models_name_them(database: Database, database_path: Path) -> None:
    [media_sql] = [sql for kind, name, sql in schema(database_path) if name == "media"]

    assert "CONSTRAINT ck_media_taken_at_source CHECK" in media_sql
    assert "CONSTRAINT ck_media_media_kind CHECK" in media_sql


def test_deleting_media_removes_what_refers_to_it(database: Database, database_path: Path) -> None:
    media_id = add_media_with_tag(database)
    engine = create_sqlite_engine(database_path)
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO file_move (media_id, from_path, to_path, created_at) "
            "VALUES (:media_id, 'a.jpg', 'b.jpg', '2025-01-01 00:00:00')"
        ), {"media_id": media_id})
    engine.dispose()

    with database.session() as session:
        session.execute(delete(Media).where(Media.id == media_id))
        session.commit()

    assert (count_rows(database_path, "media_tag"), count_rows(database_path, "file_move")) == (0, 0)
