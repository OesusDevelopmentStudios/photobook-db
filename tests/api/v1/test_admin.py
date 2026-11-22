# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

import pytest
from fastapi.testclient import TestClient

from photobook_db.services.auth import AuthService, Role

PASSWORD = "correct horse battery"


@pytest.fixture
def auth(client: TestClient) -> AuthService:
    return client.app.state.auth


def token_for(client: TestClient, auth: AuthService, username: str, role: Role) -> dict[str, str]:
    auth.create_user(username, PASSWORD, role)
    response = client.post(
        "/api/v1/auth/tokens", json={"username": username, "password": PASSWORD, "device_name": "test"}
    )
    return {"Authorization": f"Bearer {response.json()['token']}"}


@pytest.fixture
def admin_headers(client: TestClient, auth: AuthService) -> dict[str, str]:
    return token_for(client, auth, "admin_user", Role.ADMIN)


ADMIN_ENDPOINTS = [
    ("GET", "/api/v1/admin/users", None),
    ("POST", "/api/v1/admin/users", {"username": "new", "password": PASSWORD}),
    ("PATCH", "/api/v1/admin/users/1", {"username": "renamed"}),
    ("DELETE", "/api/v1/admin/users/1", None),
]


@pytest.mark.parametrize(("method", "path", "body"), ADMIN_ENDPOINTS)
def test_admin_endpoints_need_a_token(client: TestClient, method: str, path: str, body: dict | None) -> None:
    response = client.request(method, path, json=body)

    assert response.status_code == 401


@pytest.mark.parametrize(("method", "path", "body"), ADMIN_ENDPOINTS)
def test_admin_endpoints_are_forbidden_for_normal_users(
    client: TestClient, auth: AuthService, method: str, path: str, body: dict | None
) -> None:
    user_headers = token_for(client, auth, "restricted_user", Role.USER)

    response = client.request(method, path, json=body, headers=user_headers)

    assert response.status_code == 403
    assert response.json()["code"] == "forbidden"


def test_creates_and_lists_users(client: TestClient, admin_headers: dict[str, str]) -> None:
    response = client.post(
        "/api/v1/admin/users", json={"username": "Restricted_User", "password": PASSWORD}, headers=admin_headers
    )

    assert response.status_code == 201
    created = response.json()
    assert {key: created[key] for key in ("username", "role", "restricted_tags", "restrictions_active")} == {
        "username": "restricted_user",
        "role": "user",
        "restricted_tags": [],
        "restrictions_active": True,
    }
    assert created["created_at"].endswith("Z")
    listed = client.get("/api/v1/admin/users", headers=admin_headers).json()
    assert [(user["username"], user["restrictions_active"]) for user in listed] == [
        ("admin_user", False), ("restricted_user", True)
    ]


def test_existing_username_is_a_conflict(client: TestClient, admin_headers: dict[str, str]) -> None:
    response = client.post(
        "/api/v1/admin/users", json={"username": "ADMIN_USER", "password": PASSWORD}, headers=admin_headers
    )

    assert response.status_code == 409
    assert response.json()["code"] == "already_exists"


@pytest.mark.parametrize("method", ["POST", "PATCH"])
def test_no_one_can_be_made_an_admin(
    client: TestClient, auth: AuthService, admin_headers: dict[str, str], method: str
) -> None:
    restricted_user = auth.create_user("restricted_user", PASSWORD, Role.USER)
    path = "/api/v1/admin/users" if method == "POST" else f"/api/v1/admin/users/{restricted_user.id}"
    body = {"role": "admin"} | ({"username": "second", "password": PASSWORD} if method == "POST" else {})

    response = client.request(method, path, json=body, headers=admin_headers)

    assert response.status_code == 422
    assert response.json()["errors"][0]["field"] == "body.role"
    assert [user.role for user in auth.list_users()] == [Role.ADMIN, Role.USER]


def test_renames_a_user(client: TestClient, auth: AuthService, admin_headers: dict[str, str]) -> None:
    restricted_user = auth.create_user("restricted_user", PASSWORD, Role.USER)

    response = client.patch(
        f"/api/v1/admin/users/{restricted_user.id}", json={"username": "Username"}, headers=admin_headers
    )

    assert response.status_code == 200
    assert response.json()["username"] == "username"


def test_renaming_to_a_taken_name_is_a_conflict(
    client: TestClient, auth: AuthService, admin_headers: dict[str, str]
) -> None:
    restricted_user = auth.create_user("restricted_user", PASSWORD, Role.USER)

    response = client.patch(
        f"/api/v1/admin/users/{restricted_user.id}", json={"username": "admin_user"}, headers=admin_headers
    )

    assert response.status_code == 409
    assert response.json()["code"] == "already_exists"


def test_deletes_a_user(client: TestClient, auth: AuthService, admin_headers: dict[str, str]) -> None:
    restricted_user = auth.create_user("restricted_user", PASSWORD, Role.USER)

    response = client.delete(f"/api/v1/admin/users/{restricted_user.id}", headers=admin_headers)

    assert response.status_code == 204
    assert [user.username for user in auth.list_users()] == ["admin_user"]


def test_the_admin_account_cannot_be_deleted(client: TestClient, admin_headers: dict[str, str]) -> None:
    own_id = client.get("/api/v1/auth/me", headers=admin_headers).json()["id"]

    response = client.delete(f"/api/v1/admin/users/{own_id}", headers=admin_headers)

    assert response.status_code == 409
    assert response.json()["code"] == "admin_not_deletable"


# --- Restricted tags

def create(client: TestClient, admin_headers: dict[str, str], body: dict) -> object:
    return client.post("/api/v1/admin/users", json={"password": PASSWORD} | body, headers=admin_headers)


def test_creates_a_user_with_restricted_tags(client: TestClient, admin_headers: dict[str, str]) -> None:
    response = create(
        client, admin_headers, {"username": "restricted_user", "restricted_tags": ["rating:private", "character:*"]}
    )

    assert response.status_code == 201
    assert response.json()["restricted_tags"] == ["character:*", "rating:private"]
    assert response.json()["restrictions_active"] is True


def test_invalid_term_creates_no_user(client: TestClient, auth: AuthService, admin_headers: dict[str, str]) -> None:
    response = create(
        client, admin_headers, {"username": "restricted_user", "restricted_tags": ["rating:private", "-x"]}
    )

    assert response.status_code == 422
    assert response.json()["errors"][0]["field"] == "body.restricted_tags.1"
    assert [user.username for user in auth.list_users()] == ["admin_user"]


def test_patch_replaces_or_keeps_restricted_tags(client: TestClient, admin_headers: dict[str, str]) -> None:
    user_id = create(
        client, admin_headers, {"username": "restricted_user", "restricted_tags": ["rating:private"]}
    ).json()["id"]

    replaced = client.patch(
        f"/api/v1/admin/users/{user_id}", json={"restricted_tags": ["category:refs"]}, headers=admin_headers
    )
    kept = client.patch(f"/api/v1/admin/users/{user_id}", json={"password": "another password"}, headers=admin_headers)

    assert replaced.json()["restricted_tags"] == ["category:refs"]
    assert kept.json()["restricted_tags"] == ["category:refs"]


def test_invalid_term_changes_nothing(client: TestClient, admin_headers: dict[str, str]) -> None:
    user_id = create(
        client, admin_headers, {"username": "restricted_user", "restricted_tags": ["rating:private"]}
    ).json()["id"]

    response = client.patch(
        f"/api/v1/admin/users/{user_id}", json={"username": "renamed", "restricted_tags": ["(a"]}, headers=admin_headers
    )

    assert response.status_code == 422
    users = client.get("/api/v1/admin/users", headers=admin_headers).json()
    user = next(user for user in users if user["id"] == user_id)
    assert (user["username"], user["restricted_tags"]) == ("restricted_user", ["rating:private"])


def test_restricted_tags_of_an_admin_are_kept_but_inactive(client: TestClient, admin_headers: dict[str, str]) -> None:
    own_id = client.get("/api/v1/auth/me", headers=admin_headers).json()["id"]

    response = client.patch(
        f"/api/v1/admin/users/{own_id}", json={"restricted_tags": ["rating:private"]}, headers=admin_headers
    )

    assert response.json()["restricted_tags"] == ["rating:private"]
    assert response.json()["restrictions_active"] is False
