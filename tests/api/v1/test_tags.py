# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

import io
import json
import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from photobook_db.services.auth import Role

from ...tag_lists import user_tags

PASSWORD = "correct horse battery"


@pytest.fixture
def user_headers(client: TestClient) -> dict[str, str]:
    client.app.state.auth.create_user("restricted_user", PASSWORD, Role.USER)
    response = client.post(
        "/api/v1/auth/tokens", json={"username": "restricted_user", "password": PASSWORD, "device_name": "t"}
    )
    return {"Authorization": f"Bearer {response.json()['token']}"}


def test_tags_need_a_login(client: TestClient) -> None:
    assert client.get("/api/v1/tags").status_code == 401


def test_returns_items_with_tag_and_count(client: TestClient, user_headers: dict[str, str]) -> None:
    with client.app.state.database.engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO tag (namespace_id, name) SELECT id, 'summer' FROM namespace WHERE name = 'general'"
        )

    response = client.get("/api/v1/tags", params={"prefix": "sum"}, headers=user_headers)

    assert response.status_code == 200
    assert response.json() == {"items": [{"tag": "general:summer", "media_count": 0}], "next_cursor": None}


@pytest.mark.parametrize("limit", [0, 101])
def test_limit_must_be_between_1_and_100(client: TestClient, user_headers: dict[str, str], limit: int) -> None:
    response = client.get("/api/v1/tags", params={"limit": limit}, headers=user_headers)

    assert response.status_code == 422
    assert response.json()["errors"][0]["field"] == "query.limit"


def general_tag_pages(client: TestClient, headers: dict[str, str], limit: int) -> list[list[tuple[str, int]]]:
    """Every page of the general tags, as (tag, count); stops after 10, so a cursor that never ends fails."""
    pages: list[list[tuple[str, int]]] = []
    cursor = None
    while len(pages) < 10:
        params = {"namespace": "general", "limit": limit} | ({} if cursor is None else {"cursor": cursor})
        body = client.get("/api/v1/tags", params=params, headers=headers).json()
        pages.append([(item["tag"], item["media_count"]) for item in body["items"]])
        cursor = body["next_cursor"]
        if cursor is None:
            break

    return pages


def test_pages_through_a_namespace(client: TestClient, user_headers: dict[str, str]) -> None:
    with client.app.state.database.engine.begin() as connection:
        for name in ("harbour", "hills", "lake", "market", "station"):
            connection.exec_driver_sql(
                f"INSERT INTO tag (namespace_id, name) SELECT id, '{name}' FROM namespace WHERE name = 'general'"
            )

    pages = general_tag_pages(client, user_headers, limit=2)

    assert [[tag for tag, _count in page] for page in pages] == [  # the same count: by name
        ["general:harbour", "general:hills"], ["general:lake", "general:market"], ["general:station"]
    ]


def test_a_damaged_cursor_is_refused(client: TestClient, user_headers: dict[str, str]) -> None:
    response = client.get("/api/v1/tags", params={"cursor": "not a cursor"}, headers=user_headers)

    assert (response.status_code, response.json()["errors"][0]["field"]) == (422, "query.cursor")


# --- Adding, renaming and deleting, for admins

@pytest.fixture
def admin_headers(client: TestClient) -> dict[str, str]:
    client.app.state.auth.create_user("admin_user", PASSWORD, Role.ADMIN)
    response = client.post(
        "/api/v1/auth/tokens", json={"username": "admin_user", "password": PASSWORD, "device_name": "t"}
    )
    return {"Authorization": f"Bearer {response.json()['token']}"}


def upload(client: TestClient, headers: dict[str, str], tags: list[str], colour: str = "teal") -> int:
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), colour).save(buffer, format="JPEG")
    metadata = json.dumps({"original_filename": "beach.jpg", "tags": tags})
    files = {"file": ("beach.jpg", buffer.getvalue())}
    return client.post("/api/v1/media", headers=headers, files=files, data={"metadata": metadata}).json()["id"]


def test_admin_adds_a_tag_that_is_then_suggested(
    client: TestClient, admin_headers: dict[str, str], user_headers: dict[str, str]
) -> None:
    client.app.state.tags.create_namespace("location", single_value=False)

    response = client.post("/api/v1/tags", json={"tag": "Location:Old Harbour"}, headers=admin_headers)

    assert (response.status_code, response.json()) == (201, {"tag": "location:old_harbour", "media_count": 0})
    suggested = client.get("/api/v1/tags", params={"prefix": "location:old"}, headers=user_headers).json()["items"]
    assert suggested == [{"tag": "location:old_harbour", "media_count": 0}]


@pytest.mark.parametrize(
    ("tag", "status", "code"),
    [
        ("general:summer", 409, "already_exists"),
        ("meta:video", 422, "namespace_read_only"),
        ("a|b", 422, "invalid_tag"),
    ],
)
def test_adding_a_tag_is_refused(
    client: TestClient, admin_headers: dict[str, str], tag: str, status: int, code: str
) -> None:
    upload(client, admin_headers, ["summer"])

    response = client.post("/api/v1/tags", json={"tag": tag}, headers=admin_headers)

    assert (response.status_code, response.json()["code"]) == (status, code)


def test_pages_follow_the_counts(client: TestClient, admin_headers: dict[str, str]) -> None:
    upload(client, admin_headers, ["summer", "beach"], colour="red")
    upload(client, admin_headers, ["summer"], colour="green")
    client.post("/api/v1/tags", json={"tag": "lake"}, headers=admin_headers)

    pages = general_tag_pages(client, admin_headers, limit=1)

    assert pages == [[("general:summer", 2)], [("general:beach", 1)], [("general:lake", 0)]]


def test_an_admin_adds_a_rating_value(client: TestClient, admin_headers: dict[str, str]) -> None:
    response = client.post("/api/v1/tags", json={"tag": "rating:secret"}, headers=admin_headers)

    assert response.status_code == 201
    items = client.get("/api/v1/namespaces", headers=admin_headers).json()["items"]
    assert next(item["allowed_values"] for item in items if item["name"] == "rating") == ["public", "private", "secret"]


def test_users_cannot_add_tags(client: TestClient, user_headers: dict[str, str]) -> None:
    assert client.post("/api/v1/tags", json={"tag": "harbour"}, headers=user_headers).status_code == 403


def test_admin_renames_a_folder_tag(client: TestClient, admin_headers: dict[str, str]) -> None:
    media_id = upload(client, admin_headers, ["author:anon_one"])

    response = client.patch("/api/v1/tags/author/anon_one", json={"new_tag": "Author:Anon Two"}, headers=admin_headers)

    assert (response.status_code, response.json()["tag"]) == (200, "author:anon_two")
    job_id = response.json()["job_id"]
    deadline = time.monotonic() + 5
    while client.get(f"/api/v1/jobs/{job_id}", headers=admin_headers).json()["status"] != "done":
        assert time.monotonic() < deadline, "the relayout did not finish in time"
        time.sleep(0.01)

    image = client.get(f"/api/v1/media/{media_id}", headers=admin_headers).json()
    assert user_tags(image["tags"]) == ["author:anon_two"]
    assert "/anon_two/" in image["storage_path"]


def test_admin_deletes_a_tag(client: TestClient, admin_headers: dict[str, str]) -> None:
    media_id = upload(client, admin_headers, ["summer", "beach"])

    response = client.delete("/api/v1/tags/general/summer", headers=admin_headers)

    assert response.json() == {"job_id": None}
    assert user_tags(client.get(f"/api/v1/media/{media_id}", headers=admin_headers).json()["tags"]) == [
        "general:beach"
    ]


def test_unknown_tag_is_not_found(client: TestClient, admin_headers: dict[str, str]) -> None:
    assert client.delete("/api/v1/tags/general/nothing", headers=admin_headers).status_code == 404
    assert client.patch("/api/v1/tags/nowhere/x", json={"new_tag": "y"}, headers=admin_headers).status_code == 404


def test_the_servers_own_tags_cannot_be_renamed_or_deleted(client: TestClient, admin_headers: dict[str, str]) -> None:
    upload(client, admin_headers, ["summer"])

    deleted = client.delete("/api/v1/tags/meta/jpeg", headers=admin_headers)
    renamed = client.patch("/api/v1/tags/meta/jpeg", json={"new_tag": "meta:png"}, headers=admin_headers)

    assert (deleted.status_code, deleted.json()["code"]) == (422, "namespace_read_only")
    assert (renamed.status_code, renamed.json()["code"]) == (422, "namespace_read_only")


def test_users_cannot_rename_or_delete_tags(client: TestClient, user_headers: dict[str, str]) -> None:
    assert client.delete("/api/v1/tags/general/summer", headers=user_headers).status_code == 403
    assert client.patch("/api/v1/tags/general/summer", json={"new_tag": "y"}, headers=user_headers).status_code == 403
