# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import delete, inspect, select, text
from sqlalchemy.exc import IntegrityError

from photobook_db.db import DATABASE_FILE_NAME, Database, open_database
from photobook_db.db._connection import create_sqlite_engine
from photobook_db.db._migrations import _alembic_config
from photobook_db.db.models import Base, Image, Namespace, Tag, image_tag


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


def add_image_with_tag(database: Database) -> int:
    with database.session() as session:
        general = session.scalars(select(Namespace).where(Namespace.name == "general")).one()
        tag = Tag(namespace_id=general.id, name="summer")
        image = Image(
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
        session.execute(image_tag.insert().values(image_id=image.id, tag_id=tag.id))
        session.commit()
        return image.id


def test_creates_tables_in_empty_database(database: Database) -> None:
    table_names = set(inspect(database.engine).get_table_names())

    assert {"namespace", "tag", "image", "image_tag"} <= table_names


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
    add_image_with_tag(first_opening)
    first_opening.close()

    second_opening = open_database(database_path)
    with second_opening.session() as session:
        image_count = len(session.scalars(select(Image)).all())
        namespace_count = len(session.scalars(select(Namespace)).all())
    second_opening.close()

    assert image_count == 1
    assert namespace_count == 7  # the built-in namespaces are not added twice


def test_models_match_migrations(database: Database) -> None:
    with database.engine.connect() as connection:
        differences = compare_metadata(MigrationContext.configure(connection), Base.metadata)

    assert differences == [], "The models differ from the migrations; add a migration for the change."


def test_migrations_can_be_undone_and_redone(database: Database) -> None:
    with database.engine.begin() as connection:
        command.downgrade(_alembic_config(connection), "base")
    assert "image" not in inspect(database.engine).get_table_names()

    with database.engine.begin() as connection:
        command.upgrade(_alembic_config(connection), "head")
    assert "image" in inspect(database.engine).get_table_names()


def migrate(database_path: Path, revision: str, downgrade: bool = False) -> None:
    """To a revision before the latest, with foreign keys on, as the server opens the file."""
    engine = create_sqlite_engine(database_path)
    with engine.begin() as connection:
        (command.downgrade if downgrade else command.upgrade)(_alembic_config(connection), revision)
    engine.dispose()


def tags_of(database: Database, image_id: int) -> set[str]:
    with database.session() as session:
        rows = session.execute(
            select(Namespace.name, Tag.name)
            .join(Tag, Tag.namespace_id == Namespace.id)
            .join(image_tag, image_tag.c.tag_id == Tag.id)
            .where(image_tag.c.image_id == image_id)
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
    image_id = add_image_with_tag(old_database)  # tagged general:summer
    old_database.close()

    database = open_database(database_path)  # the server upgrades the file on opening
    with database.session() as session:
        editable = {namespace.name: namespace.user_editable for namespace in session.scalars(select(Namespace))}
    image_tags = tags_of(database, image_id)
    database.close()

    assert editable == {name: name != "meta" for name in
                        ["category", "author", "character", "rating", "album", "general", "weather", "meta"]}
    assert image_tags == {"general:summer"}


def test_undoing_0002_removes_the_meta_tags_and_keeps_the_others(database_path: Path) -> None:
    database = open_database(database_path)
    image_id = add_image_with_tag(database)
    with database.session() as session:
        meta = session.scalars(select(Namespace).where(Namespace.name == "meta")).one()
        tag = Tag(namespace_id=meta.id, name="hi_res")
        session.add(tag)
        session.flush()
        session.execute(image_tag.insert().values(image_id=image_id, tag_id=tag.id))
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
    image_id = add_image_with_tag(database)

    with database.session() as session:
        session.execute(delete(Image).where(Image.id == image_id))
        session.commit()
        remaining_links = session.execute(select(image_tag)).all()

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
    image_id = add_image_with_tag(database)
    with database.session() as session:
        rating_tag = select(Tag).join(Namespace).where(Namespace.name == "rating", Tag.name == "public")
        public = session.scalars(rating_tag).one()
        session.execute(image_tag.insert().values(image_id=image_id, tag_id=public.id))
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
    image_id = add_image_with_tag(database)
    with database.session() as session:
        photo_tag = select(Tag).join(Namespace).where(Namespace.name == "category", Tag.name == "photo")
        photo = session.scalars(photo_tag).one()
        session.execute(image_tag.insert().values(image_id=image_id, tag_id=photo.id))
        session.commit()
    database.close()

    migrate(database_path, "0003", downgrade=True)

    assert fixed_namespaces(database_path) == {"rating"}
    assert tag_names(database_path, "category") == ["photo"]  # artwork was a value only
