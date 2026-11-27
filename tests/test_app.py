# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

import io
import logging
import time
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from PIL import Image as PillowImage
from sqlalchemy import select

from photobook_db.app import create_app
from photobook_db.config import Config
from photobook_db.db import DATABASE_FILE_NAME, open_database
from photobook_db.db.models import FileMove, Media, Namespace
from photobook_db.services.auth import Role


def test_creating_the_app_touches_no_files(config: Config) -> None:
    create_app(config)

    assert not (config.data_dir / DATABASE_FILE_NAME).exists()


def test_starting_creates_the_database(config: Config) -> None:
    with TestClient(create_app(config)) as client:
        with client.app.state.database.session() as session:
            namespace_names = session.scalars(select(Namespace.name)).all()

    assert (config.data_dir / DATABASE_FILE_NAME).exists()
    assert "general" in namespace_names


def test_errors_are_problem_details(client: TestClient) -> None:
    response = client.get("/api/v1/no-such-endpoint")

    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["code"] == "not_found"


def test_logs_the_setup_code_while_no_admin_exists(config: Config, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING), TestClient(create_app(config)) as client:
        setup_code = client.app.state.auth.setup_code()

    assert setup_code in caplog.text
    assert "/admin/setup" in caplog.text


def test_does_not_log_a_setup_code_once_an_admin_exists(config: Config, caplog: pytest.LogCaptureFixture) -> None:
    with TestClient(create_app(config)) as client:
        client.app.state.auth.create_user("admin_user", "correct horse battery", Role.ADMIN)
    caplog.clear()  # the first start had no admin yet and rightly logged a code

    with caplog.at_level(logging.WARNING), TestClient(create_app(config)):
        pass

    assert "setup code" not in caplog.text


def test_finishes_interrupted_file_moves_when_it_starts(config: Config) -> None:
    # A move that a crash interrupted: the file is already at its new place, the database not yet updated.
    moment = datetime(2026, 11, 12, 12, 0)
    database = open_database(config.data_dir / DATABASE_FILE_NAME)
    with database.session() as session:
        image = Media(
            sha256="ab" * 32, storage_path="old/beach.jpg", original_filename="beach.jpg", mime_type="image/jpeg",
            width=1, height=1, file_size=1, taken_at=moment, taken_at_source="import_time",
            imported_at=moment, updated_at=moment,
        )
        session.add(image)
        session.flush()
        session.add(FileMove(media_id=image.id, from_path="old/beach.jpg", to_path="new/beach.jpg", created_at=moment))
        session.commit()
    database.close()
    (config.library_dir / "new").mkdir()
    (config.library_dir / "new" / "beach.jpg").write_bytes(b"image")

    with TestClient(create_app(config)) as client:
        with client.app.state.database.session() as session:
            stored_path = session.scalars(select(Media.storage_path)).one()
            leftover_moves = session.scalars(select(FileMove)).all()

    assert stored_path == "new/beach.jpg"
    assert leftover_moves == []


def test_gives_older_images_their_meta_tags_when_it_starts(config: Config) -> None:
    # An image as a server before 0.2.0 stored it: no meta tags.
    moment = datetime(2026, 11, 12, 12, 0)
    (config.library_dir / "beach.png").write_bytes(_png_bytes())
    database = open_database(config.data_dir / DATABASE_FILE_NAME)
    with database.session() as session:
        session.add(Media(
            sha256="cd" * 32, storage_path="beach.png", original_filename="beach.png", mime_type="image/png",
            width=8, height=8, file_size=1, taken_at=moment, taken_at_source="import_time",
            imported_at=moment, updated_at=moment,
        ))
        session.commit()
    database.close()

    with TestClient(create_app(config)) as client:
        images = client.app.state.images
        deadline = time.monotonic() + 10
        while not images.get_image(1).tags and time.monotonic() < deadline:
            time.sleep(0.01)

        tags = [str(tag) for tag in images.get_image(1).tags]

    assert tags == ["meta:low_res", "meta:png", "meta:still_image"]


def _png_bytes() -> bytes:
    buffer = io.BytesIO()
    PillowImage.new("RGB", (8, 8), "teal").save(buffer, format="PNG")
    return buffer.getvalue()
