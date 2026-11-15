# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

import pytest
from fastapi.testclient import TestClient

from pictorio_db.services.auth import AuthService, Role, UserInfo

from .conftest import PASSWORD, csrf_token_in

NEW_PASSWORD = "a brand new password"
BACK_BUTTON = '<a class="button back" href="/admin/users" aria-label="Back to all users">'


@pytest.fixture
def auth(admin_browser: TestClient) -> AuthService:
    return admin_browser.app.state.auth


@pytest.fixture
def apo(auth: AuthService) -> UserInfo:
    return auth.list_users()[0]  # the admin the browser is logged in as


@pytest.fixture
def kid(auth: AuthService) -> UserInfo:
    return auth.create_user("kid", PASSWORD)


def post(browser: TestClient, path: str, **fields: str):  # -> Response
    form = {"csrf_token": csrf_token_in(browser.get("/admin/users").text)} | fields
    return browser.post(path, data=form, follow_redirects=False)


def restricted_terms(browser: TestClient, user: UserInfo) -> list[str]:
    return browser.app.state.access.restricted_terms(user.id)


def logs_in_with_api(browser: TestClient, username: str, password: str) -> bool:
    login = {"username": username, "password": password, "device_name": "laptop"}
    return browser.post("/api/v1/auth/tokens", json=login).status_code == 201


def test_needs_a_login(client: TestClient) -> None:
    assert client.get("/admin/users", follow_redirects=False).status_code == 303


# --- The list

def test_lists_users_with_their_restricted_tags(admin_browser: TestClient, kid: UserInfo) -> None:
    admin_browser.app.state.access.set_restricted_terms(kid.id, ["rating:nsfw"])

    page = admin_browser.get("/admin/users").text

    assert '<a href="/admin/users" aria-current="page">User Management</a>' in page
    assert '<a class="button" href="/admin/users/new">+ Add New User</a>' in page
    assert f'<a href="/admin/users/{kid.id}">kid</a>' in page
    assert "<code>rating:nsfw</code>" in page


@pytest.mark.parametrize("search, shown", [("KI", ["kid"]), ("", ["apo", "kid"]), ("nobody", [])])
def test_searches_by_username(
    admin_browser: TestClient, auth: AuthService, kid: UserInfo, search: str, shown: list[str]
) -> None:
    page = admin_browser.get("/admin/users", params={"search": search}).text

    assert [user.username for user in auth.list_users() if f">{user.username}</a>" in page] == shown
    assert f'value="{search}"' in page
    assert ("No username contains" in page) == (shown == [])


# --- New users

def test_creates_a_standard_user_with_restricted_tags(admin_browser: TestClient, auth: AuthService) -> None:
    new_user_page = admin_browser.get("/admin/users/new").text
    assert "New accounts are standard users" in new_user_page
    assert BACK_BUTTON in new_user_page

    response = post(admin_browser, "/admin/users/new", username="Kid", password=PASSWORD, password_repeated=PASSWORD,
                    restricted_tags="rating:nsfw\n\n character:* \n")

    kid = next(user for user in auth.list_users() if user.username == "kid")
    assert (response.status_code, response.headers["location"]) == (303, f"/admin/users/{kid.id}?saved=true")
    assert kid.role == Role.USER
    assert restricted_terms(admin_browser, kid) == ["character:*", "rating:nsfw"]
    assert logs_in_with_api(admin_browser, "kid", PASSWORD)


@pytest.mark.parametrize(
    "fields, status, message",
    [
        ({"password_repeated": "something else"}, 422, "The two passwords differ."),
        ({"restricted_tags": "rating:nsfw\ntwo words"}, 422, "Restricted tag “two words”: One search term: no spaces"),
        ({"username": "apo"}, 409, "Username is taken."),
        ({"username": "no spaces"}, 422, "Username: 1 to"),
    ],
    ids=["passwords differ", "invalid term", "taken username", "invalid username"],
)
def test_new_user_that_cannot_be_created_shows_why(
    admin_browser: TestClient, auth: AuthService, fields: dict[str, str], status: int, message: str
) -> None:
    form = {"username": "kid", "password": PASSWORD, "password_repeated": PASSWORD,
            "restricted_tags": "rating:nsfw"} | fields

    response = post(admin_browser, "/admin/users/new", **form)

    assert response.status_code == status
    assert message in response.text
    assert f'value="{form["username"]}"' in response.text  # what was entered stays in the form
    assert [user.username for user in auth.list_users()] == ["apo"]


# --- One user

def test_shows_a_user(admin_browser: TestClient, kid: UserInfo) -> None:
    admin_browser.app.state.access.set_restricted_terms(kid.id, ["rating:nsfw", "character:*"])

    page = admin_browser.get(f"/admin/users/{kid.id}").text

    assert 'name="username" required autocomplete="off" value="kid"' in page
    assert ">character:*\nrating:nsfw</textarea>" in page
    assert '<a href="/admin/users" aria-current="page">User Management</a>' in page
    assert BACK_BUTTON in page


def test_unknown_user_is_a_404_page(admin_browser: TestClient) -> None:
    response = admin_browser.get("/admin/users/999")

    assert response.status_code == 404
    assert "This user does not exist" in response.text


def test_renames_and_changes_restricted_tags_but_keeps_the_password(
    admin_browser: TestClient, auth: AuthService, kid: UserInfo
) -> None:
    response = post(admin_browser, f"/admin/users/{kid.id}", username="Kiddo", restricted_tags="rating:nsfw")

    assert response.headers["location"] == f"/admin/users/{kid.id}?saved=true"
    assert auth.get_user(kid.id).username == "kiddo"
    assert restricted_terms(admin_browser, kid) == ["rating:nsfw"]
    assert logs_in_with_api(admin_browser, "kiddo", PASSWORD)


def test_empty_restricted_tags_remove_them_all(admin_browser: TestClient, kid: UserInfo) -> None:
    admin_browser.app.state.access.set_restricted_terms(kid.id, ["rating:nsfw"])

    post(admin_browser, f"/admin/users/{kid.id}", username="kid", restricted_tags="")

    assert restricted_terms(admin_browser, kid) == []


def test_new_password_replaces_the_old_one(admin_browser: TestClient, auth: AuthService, kid: UserInfo) -> None:
    auth.create_token("kid", PASSWORD, "laptop")

    post(admin_browser, f"/admin/users/{kid.id}", username="kid", password=NEW_PASSWORD,
         password_repeated=NEW_PASSWORD)

    assert auth.list_tokens(kid.id) == []  # logged out everywhere
    assert not logs_in_with_api(admin_browser, "kid", PASSWORD)
    assert logs_in_with_api(admin_browser, "kid", NEW_PASSWORD)


def test_invalid_change_shows_why_and_changes_nothing(
    admin_browser: TestClient, auth: AuthService, kid: UserInfo
) -> None:
    response = post(admin_browser, f"/admin/users/{kid.id}", username="apo", restricted_tags="rating:nsfw")

    assert response.status_code == 409
    assert "Username is taken." in response.text
    assert ">rating:nsfw</textarea>" in response.text  # what was entered stays in the form
    assert (auth.get_user(kid.id).username, restricted_terms(admin_browser, kid)) == ("kid", [])


# --- The admin account

def test_admin_has_no_restricted_tags_and_cannot_be_removed(admin_browser: TestClient, apo: UserInfo) -> None:
    page = admin_browser.get(f"/admin/users/{apo.id}").text

    assert 'name="restricted_tags"' not in page
    assert "The admin account cannot be removed." in page
    assert '<button type="button" class="danger" disabled>Remove User</button>' in page


def test_admin_can_be_renamed_and_stays_logged_in(admin_browser: TestClient, auth: AuthService, apo: UserInfo) -> None:
    post(admin_browser, f"/admin/users/{apo.id}", username="boss")

    assert auth.get_user(apo.id).username == "boss"
    assert admin_browser.get("/admin/users", follow_redirects=False).status_code == 200


def test_admin_cannot_be_removed(admin_browser: TestClient, auth: AuthService, apo: UserInfo) -> None:
    response = post(admin_browser, f"/admin/users/{apo.id}/delete")

    assert response.status_code == 409
    assert "The admin account cannot be deleted." in response.text
    assert auth.list_users() == [apo]


# --- Devices

def test_lists_and_logs_out_a_device(admin_browser: TestClient, auth: AuthService, kid: UserInfo) -> None:
    laptop = auth.create_token("kid", PASSWORD, "laptop").info
    assert "laptop" in admin_browser.get(f"/admin/users/{kid.id}").text

    response = post(admin_browser, f"/admin/users/{kid.id}/devices/{laptop.id}/revoke")

    assert (response.status_code, response.headers["location"]) == (303, f"/admin/users/{kid.id}")
    assert auth.list_tokens(kid.id) == []


def test_another_users_device_cannot_be_logged_out(
    admin_browser: TestClient, auth: AuthService, apo: UserInfo, kid: UserInfo
) -> None:
    [web_login] = auth.list_tokens(apo.id)

    response = post(admin_browser, f"/admin/users/{kid.id}/devices/{web_login.id}/revoke")

    assert response.status_code == 404
    assert [token.id for token in auth.list_tokens(apo.id)] == [web_login.id]


# --- Removing

def test_removes_a_user_after_asking(admin_browser: TestClient, auth: AuthService, kid: UserInfo) -> None:
    assert f'<form method="get" action="/admin/users/{kid.id}/delete">' in admin_browser.get(
        f"/admin/users/{kid.id}"
    ).text
    assert "Remove user kid?" in admin_browser.get(f"/admin/users/{kid.id}/delete").text

    response = post(admin_browser, f"/admin/users/{kid.id}/delete")

    assert (response.status_code, response.headers["location"]) == (303, "/admin/users?deleted=true")
    assert [user.username for user in auth.list_users()] == ["apo"]
