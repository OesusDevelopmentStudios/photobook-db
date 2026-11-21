# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import photobook_db
from photobook_db.config import Config
from photobook_db.services.auth import Role
from photobook_db.services.same_machine import app_specific_id

MACHINE_ID = "0123456789abcdef0123456789abcdef"
HOST_LIBRARY = Path("/home/kid/Pictures/photobook-library")
PASSWORD = "correct horse battery"


@pytest.fixture
def config(config: Config) -> Config:
    """The server's computer has a machine ID; the library is mounted into a container from HOST_LIBRARY."""
    config.host_machine_id_file.write_text(MACHINE_ID + "\n")
    return replace(config, host_library_dir=HOST_LIBRARY)


@pytest.fixture
def headers(client: TestClient) -> dict[str, str]:
    client.app.state.auth.create_user("kid", PASSWORD, Role.USER)
    login = {"username": "kid", "password": PASSWORD, "device_name": "t"}
    return {"Authorization": f"Bearer {client.post('/api/v1/auth/tokens', json=login).json()['token']}"}


def test_server_info_is_public_and_names_the_server(client: TestClient) -> None:
    response = client.get("/api/v1/server/info")

    assert response.status_code == 200
    assert response.json() == {
        "name": "photobook-db",
        "version": photobook_db.__version__,
        "api_version": 1,
        "setup_required": True,
    }


def test_setup_is_no_longer_required_once_an_admin_exists(client: TestClient) -> None:
    client.app.state.auth.create_user("apo", "correct horse battery", Role.ADMIN)

    response = client.get("/api/v1/server/info")

    assert response.json()["setup_required"] is False


def test_gives_a_client_on_the_same_computer_the_library_folder(
    client: TestClient, headers: dict[str, str]
) -> None:
    response = client.get("/api/v1/server/local", params={"machine": app_specific_id(MACHINE_ID)}, headers=headers)

    assert response.status_code == 200
    assert response.json() == {"library_dir": str(HOST_LIBRARY)}


def test_refuses_a_client_on_another_computer(client: TestClient, headers: dict[str, str]) -> None:
    other_computer = app_specific_id("fedcba9876543210fedcba9876543210")

    response = client.get("/api/v1/server/local", params={"machine": other_computer}, headers=headers)

    assert response.status_code == 404
    assert response.json()["code"] == "not_same_machine"


def test_the_library_folder_needs_a_login(client: TestClient) -> None:
    response = client.get("/api/v1/server/local", params={"machine": app_specific_id(MACHINE_ID)})

    assert response.status_code == 401
