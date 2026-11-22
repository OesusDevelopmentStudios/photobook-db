# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

import pytest
from fastapi.testclient import TestClient

from photobook_db.services.auth import AuthService, Role

PASSWORD = "correct horse battery"


@pytest.fixture
def auth(client: TestClient) -> AuthService:
    return client.app.state.auth


def log_in(client: TestClient, username: str, device_name: str = "laptop") -> str:
    response = client.post(
        "/api/v1/auth/tokens", json={"username": username, "password": PASSWORD, "device_name": device_name}
    )
    assert response.status_code == 201
    return response.json()["token"]


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_login_returns_token_and_user(client: TestClient, auth: AuthService) -> None:
    user = auth.create_user("restricted_user", PASSWORD, Role.USER)

    response = client.post(
        "/api/v1/auth/tokens", json={"username": "restricted_user", "password": PASSWORD, "device_name": "laptop"}
    )

    assert response.status_code == 201
    body = response.json()
    assert body["token"].startswith("pk_")
    assert body["user"] == {"id": user.id, "username": "restricted_user", "role": "user"}


def test_wrong_password_is_not_authenticated(client: TestClient, auth: AuthService) -> None:
    auth.create_user("restricted_user", PASSWORD, Role.USER)

    response = client.post(
        "/api/v1/auth/tokens",
        json={"username": "restricted_user", "password": "wrong password", "device_name": "laptop"},
    )

    assert response.status_code == 401
    assert response.json()["code"] == "not_authenticated"
    assert response.headers["www-authenticate"] == "Bearer"


def test_login_without_device_name_is_invalid(client: TestClient) -> None:
    response = client.post("/api/v1/auth/tokens", json={"username": "restricted_user", "password": PASSWORD})

    assert response.status_code == 422
    assert response.json()["errors"][0]["field"] == "body.device_name"


def test_too_many_failed_logins_are_refused_for_a_while(client: TestClient, auth: AuthService) -> None:
    auth.create_user("restricted_user", PASSWORD, Role.USER)
    wrong_login = {"username": "restricted_user", "password": "wrong password", "device_name": "laptop"}
    for _ in range(10):
        client.post("/api/v1/auth/tokens", json=wrong_login)

    response = client.post("/api/v1/auth/tokens", json=wrong_login)

    assert response.status_code == 429
    assert response.json()["code"] == "too_many_attempts"
    assert int(response.headers["retry-after"]) > 0


def test_me_returns_the_logged_in_user(client: TestClient, auth: AuthService) -> None:
    user = auth.create_user("restricted_user", PASSWORD, Role.USER)
    token = log_in(client, "restricted_user")

    response = client.get("/api/v1/auth/me", headers=bearer(token))

    assert response.status_code == 200
    assert response.json() == {"id": user.id, "username": "restricted_user", "role": "user", "restricted_tags": []}


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Bearer pk_unknown"}, {"Authorization": "Basic a2lkOnBhc3N3b3Jk"}],
    ids=["no token", "unknown token", "not a bearer token"],
)
@pytest.mark.parametrize(
    ("method", "path"),
    [("GET", "/api/v1/auth/me"), ("GET", "/api/v1/auth/tokens"), ("DELETE", "/api/v1/auth/tokens/1")],
)
def test_protected_endpoints_need_a_valid_token(
    client: TestClient, method: str, path: str, headers: dict[str, str]
) -> None:
    response = client.request(method, path, headers=headers)

    assert response.status_code == 401
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["code"] == "not_authenticated"


def test_lists_own_devices_and_marks_the_current_one(client: TestClient, auth: AuthService) -> None:
    auth.create_user("restricted_user", PASSWORD, Role.USER)
    laptop_token = log_in(client, "restricted_user", "laptop")
    log_in(client, "restricted_user", "phone")

    response = client.get("/api/v1/auth/tokens", headers=bearer(laptop_token))

    assert response.status_code == 200
    devices = response.json()
    assert [(device["device_name"], device["is_current"]) for device in devices] == [
        ("laptop", True),
        ("phone", False),
    ]
    assert devices[0]["created_at"].endswith("Z")  # UTC, as the API promises


def test_revoking_the_current_token_logs_out(client: TestClient, auth: AuthService) -> None:
    auth.create_user("restricted_user", PASSWORD, Role.USER)
    token = log_in(client, "restricted_user")
    token_id = client.get("/api/v1/auth/tokens", headers=bearer(token)).json()[0]["token_id"]

    response = client.delete(f"/api/v1/auth/tokens/{token_id}", headers=bearer(token))

    assert response.status_code == 204
    assert client.get("/api/v1/auth/me", headers=bearer(token)).status_code == 401


def test_cannot_revoke_another_users_token(client: TestClient, auth: AuthService) -> None:
    auth.create_user("restricted_user", PASSWORD, Role.USER)
    auth.create_user("admin_user", PASSWORD, Role.ADMIN)
    restricted_users_token = log_in(client, "restricted_user")
    restricted_users_token_id = client.get(
        "/api/v1/auth/tokens", headers=bearer(restricted_users_token)
    ).json()[0]["token_id"]
    admin_token = log_in(client, "admin_user")

    response = client.delete(f"/api/v1/auth/tokens/{restricted_users_token_id}", headers=bearer(admin_token))

    assert response.status_code == 404
    assert client.get("/api/v1/auth/me", headers=bearer(restricted_users_token)).status_code == 200


def test_me_lists_the_users_restricted_tags(client: TestClient, auth: AuthService) -> None:
    restricted_user = auth.create_user("restricted_user", PASSWORD, Role.USER)
    client.app.state.access.set_restricted_terms(restricted_user.id, ["rating:private"])

    response = client.get("/api/v1/auth/me", headers=bearer(log_in(client, "restricted_user")))

    assert response.json()["restricted_tags"] == ["rating:private"]


def test_me_lists_no_restricted_tags_for_admins(client: TestClient, auth: AuthService) -> None:
    admin = auth.create_user("admin_user", PASSWORD, Role.ADMIN)
    client.app.state.access.set_restricted_terms(admin.id, ["rating:private"])

    response = client.get("/api/v1/auth/me", headers=bearer(log_in(client, "admin_user")))

    assert response.json()["restricted_tags"] == []


# --- Changing your own password

NEW_PASSWORD = "a brand new password"


def change_password(client: TestClient, token: str, current_password: str, new_password: str = NEW_PASSWORD):
    body = {"current_password": current_password, "new_password": new_password}
    return client.patch("/api/v1/auth/me", json=body, headers=bearer(token))


def test_user_changes_their_own_password(client: TestClient, auth: AuthService) -> None:
    auth.create_user("restricted_user", PASSWORD, Role.USER)
    laptop = log_in(client, "restricted_user", "laptop")
    phone = log_in(client, "restricted_user", "phone")

    response = change_password(client, laptop, PASSWORD)

    assert response.status_code == 200
    assert response.json()["username"] == "restricted_user"
    assert client.get("/api/v1/auth/me", headers=bearer(laptop)).status_code == 200  # still logged in here
    assert client.get("/api/v1/auth/me", headers=bearer(phone)).status_code == 401  # logged out elsewhere
    login = {"username": "restricted_user", "password": NEW_PASSWORD, "device_name": "tablet"}
    assert client.post("/api/v1/auth/tokens", json=login).status_code == 201


def test_wrong_current_password_keeps_the_login(client: TestClient, auth: AuthService) -> None:
    auth.create_user("restricted_user", PASSWORD, Role.USER)
    laptop = log_in(client, "restricted_user")

    response = change_password(client, laptop, "wrong password")

    assert (response.status_code, response.json()["code"]) == (403, "wrong_password")
    assert client.get("/api/v1/auth/me", headers=bearer(laptop)).status_code == 200
    log_in(client, "restricted_user")  # the old password still works


def test_new_password_must_be_long_enough(client: TestClient, auth: AuthService) -> None:
    auth.create_user("restricted_user", PASSWORD, Role.USER)

    response = change_password(client, log_in(client, "restricted_user"), PASSWORD, new_password="short")

    assert (response.status_code, response.json()["errors"][0]["field"]) == (422, "password")


def test_guessing_the_current_password_is_limited_like_logins(client: TestClient, auth: AuthService) -> None:
    auth.create_user("restricted_user", PASSWORD, Role.USER)
    laptop = log_in(client, "restricted_user")
    for _attempt in range(10):
        change_password(client, laptop, "wrong password")

    response = change_password(client, laptop, PASSWORD)

    assert (response.status_code, response.json()["code"]) == (429, "too_many_attempts")
    assert "Retry-After" in response.headers


def test_changing_the_password_needs_a_login(client: TestClient) -> None:
    body = {"current_password": PASSWORD, "new_password": NEW_PASSWORD}

    assert client.patch("/api/v1/auth/me", json=body).status_code == 401
