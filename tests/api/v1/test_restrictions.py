# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

"""What a user with restricted tags can see and do. An admin uploads a public and a private image; the user
"restricted_user" is restricted from rating:private."""

import io
import json
import os
from collections.abc import Callable
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import update

from photobook_db.config import Config
from photobook_db.db.models import Media as MediaModel
from photobook_db.services.auth import Role

PASSWORD = "correct horse battery"


@dataclass
class Setup:
    admin: dict[str, str]  # request headers
    restricted_user: dict[str, str]
    restricted_user_id: int
    safe_id: int
    private_id: int
    private_bytes: bytes


def jpeg(colour: str) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), colour).save(buffer, format="JPEG")
    return buffer.getvalue()


def upload(client: TestClient, headers: dict[str, str], content: bytes, tags: list[str]):  # -> httpx Response
    metadata = json.dumps({"original_filename": "a.jpg", "tags": tags})
    files = {"file": ("a.jpg", content)}
    return client.post("/api/v1/images", headers=headers, files=files, data={"metadata": metadata})


def log_in(client: TestClient, username: str, role: Role) -> tuple[dict[str, str], int]:
    user = client.app.state.auth.create_user(username, PASSWORD, role)
    login = {"username": username, "password": PASSWORD, "device_name": "t"}
    token = client.post("/api/v1/auth/tokens", json=login).json()["token"]
    return {"Authorization": f"Bearer {token}"}, user.id


@pytest.fixture
def setup(client: TestClient) -> Setup:
    admin, _admin_id = log_in(client, "admin_user", Role.ADMIN)
    restricted_user, restricted_user_id = log_in(client, "restricted_user", Role.USER)
    client.app.state.access.set_restricted_terms(restricted_user_id, ["rating:private"])
    private_bytes = jpeg("red")
    safe_id = upload(client, admin, jpeg("green"), ["rating:public", "summer"]).json()["id"]
    private_id = upload(client, admin, private_bytes, ["rating:private", "summer"]).json()["id"]
    return Setup(admin, restricted_user, restricted_user_id, safe_id, private_id, private_bytes)


def found(client: TestClient, headers: dict[str, str], params: dict[str, object]) -> tuple[set[int], str]:
    body = client.get("/api/v1/images", params=params, headers=headers).json()
    return {item["id"] for item in body["items"]}, body["effective_query"]


def tags_of(client: TestClient, setup: Setup, image_id: int) -> list[str]:
    return client.get(f"/api/v1/images/{image_id}", headers=setup.admin).json()["tags"]


# --- Seeing and finding

@pytest.mark.parametrize(
    "params",
    [{}, {"q": "rating:private"}, {"q": "summer", "apply_hidden_filters": False}],
    ids=["everything", "searching for the restricted tag", "without hidden filters"],
)
def test_search_never_shows_restricted_images(client: TestClient, setup: Setup, params: dict[str, object]) -> None:
    images, effective_query = found(client, setup.restricted_user, params)

    assert setup.private_id not in images
    assert effective_query == params.get("q", "")  # restricted_user has no hidden filters; restrictions are never shown


def test_admin_sees_everything(client: TestClient, setup: Setup) -> None:
    assert found(client, setup.admin, {})[0] == {setup.safe_id, setup.private_id}


@pytest.mark.parametrize("suffix", ["", "/file", "/thumbnail"])
def test_restricted_image_does_not_exist_for_the_user(client: TestClient, setup: Setup, suffix: str) -> None:
    assert client.get(f"/api/v1/images/{setup.private_id}{suffix}", headers=setup.restricted_user).status_code == 404
    assert client.get(f"/api/v1/images/{setup.private_id}{suffix}", headers=setup.admin).status_code == 200
    assert client.get(f"/api/v1/images/{setup.safe_id}{suffix}", headers=setup.restricted_user).status_code == 200


def test_restricted_tags_are_never_suggested(client: TestClient, setup: Setup) -> None:
    def suggestions(headers: dict[str, str]) -> list[str]:
        response = client.get("/api/v1/tags", params={"prefix": "rating:"}, headers=headers)
        return [item["tag"] for item in response.json()["items"]]

    assert suggestions(setup.restricted_user) == ["rating:public"]
    assert set(suggestions(setup.admin)) == {"rating:private", "rating:public"}


def test_lifting_a_restriction_applies_at_once(client: TestClient, setup: Setup) -> None:
    client.patch(f"/api/v1/admin/users/{setup.restricted_user_id}", json={"restricted_tags": []}, headers=setup.admin)

    assert setup.private_id in found(client, setup.restricted_user, {"apply_hidden_filters": False})[0]


# --- Uploading and tagging

def test_upload_with_a_restricted_tag_is_refused(client: TestClient, setup: Setup, config: Config) -> None:
    response = upload(client, setup.restricted_user, jpeg("blue"), ["rating:private"])

    assert response.status_code == 403
    assert (response.json()["code"], response.json()["tags"]) == ("tag_restricted", ["rating:private"])
    assert os.listdir(config.library_dir / ".incoming") == []
    assert len(found(client, setup.admin, {})[0]) == 2  # nothing was stored


def test_duplicate_of_a_restricted_image_does_not_say_which(client: TestClient, setup: Setup) -> None:
    response = upload(client, setup.restricted_user, setup.private_bytes, ["summer"])

    assert response.status_code == 409
    assert "existing_image_id" not in response.json()
    assert "sha256" in response.json()


def test_adding_a_restricted_tag_is_refused(client: TestClient, setup: Setup) -> None:
    response = client.patch(
        f"/api/v1/images/{setup.safe_id}", json={"add_tags": ["rating:private"]}, headers=setup.restricted_user
    )

    assert (response.status_code, response.json()["code"]) == (403, "tag_restricted")
    assert "rating:public" in tags_of(client, setup, setup.safe_id)


def test_restricted_image_cannot_be_tagged(client: TestClient, setup: Setup) -> None:
    response = client.patch(
        f"/api/v1/images/{setup.private_id}", json={"add_tags": ["beach"]}, headers=setup.restricted_user
    )

    assert response.status_code == 404
    assert "general:beach" not in tags_of(client, setup, setup.private_id)


def test_bulk_change_with_a_restricted_image_changes_nothing(client: TestClient, setup: Setup) -> None:
    body = {"image_ids": [setup.safe_id, setup.private_id], "add_tags": ["album:trip"]}

    response = client.post("/api/v1/images/bulk-tags", json=body, headers=setup.restricted_user)

    assert response.status_code == 404
    assert "album:trip" not in tags_of(client, setup, setup.safe_id)


# --- Every image endpoint, as a restricted user against a restricted image

def expect_not_found(method: str, path: str) -> Callable[[TestClient, Setup], None]:
    def check(client: TestClient, setup: Setup) -> None:
        body = {"add_tags": ["beach"]} if method == "PATCH" else {"degrees": 90} if method == "POST" else None
        response = client.request(
            method, path.format(image_id=setup.private_id), json=body, headers=setup.restricted_user
        )
        assert response.status_code == 404

    return check


def check_search_excludes(client: TestClient, setup: Setup) -> None:
    assert setup.private_id not in found(
        client, setup.restricted_user, {"q": "summer", "apply_hidden_filters": False}
    )[0]


def check_count_excludes(client: TestClient, setup: Setup) -> None:
    params = {"q": "summer", "apply_hidden_filters": False}
    assert client.get("/api/v1/images/count", params=params, headers=setup.restricted_user).json()["count"] == 1
    assert client.get("/api/v1/images/count", params=params, headers=setup.admin).json()["count"] == 2


def check_upload_refuses(client: TestClient, setup: Setup) -> None:
    assert upload(client, setup.restricted_user, jpeg("blue"), ["rating:private"]).status_code == 403
    duplicate = upload(client, setup.restricted_user, setup.private_bytes, [])
    assert duplicate.status_code == 409 and "existing_image_id" not in duplicate.json()


def check_bulk_refuses(client: TestClient, setup: Setup) -> None:
    body = {"image_ids": [setup.private_id], "add_tags": ["beach"]}
    assert client.post("/api/v1/images/bulk-tags", json=body, headers=setup.restricted_user).status_code == 404


def check_autocomplete_hides(client: TestClient, setup: Setup) -> None:
    response = client.get("/api/v1/tags", params={"prefix": "rating:"}, headers=setup.restricted_user)
    assert "rating:private" not in [item["tag"] for item in response.json()["items"]]


def expect_admins_only(method: str) -> Callable[[TestClient, Setup], None]:
    def check(client: TestClient, setup: Setup) -> None:
        body = {"new_tag": "rating:public"} if method == "PATCH" else None
        response = client.request(method, "/api/v1/tags/rating/private", json=body, headers=setup.restricted_user)
        assert response.status_code == 403
        assert "rating:private" in tags_of(client, setup, setup.private_id)

    return check


def check_albums_leave_it_out(client: TestClient, setup: Setup) -> None:
    for image_id in (setup.private_id, setup.safe_id):  # the private one first: for an admin, the cover
        client.patch(f"/api/v1/images/{image_id}", json={"add_tags": ["album:trip"]}, headers=setup.admin)

    response = client.get("/api/v1/albums", params={"include_hidden": True}, headers=setup.restricted_user)

    albums = [(album["tag"], album["image_count"], album["cover"]["id"]) for album in response.json()["items"]]
    assert albums == [("album:trip", 1, setup.safe_id)]


def expect_video_not_found(path: str, method: str = "GET") -> Callable[[TestClient, Setup], None]:
    """For a route only videos have: the private image is made a video first, so a 404 means it is hidden."""

    def check(client: TestClient, setup: Setup) -> None:
        with client.app.state.database.session() as session:
            session.execute(
                update(MediaModel).where(MediaModel.id == setup.private_id).values(media_kind="video", duration_ms=2000)
            )
            session.commit()

        url = path.format(image_id=setup.private_id)
        assert client.request(method, url, headers=setup.admin).status_code != 404  # 200, or 503 without FFmpeg
        assert client.request(method, url, headers=setup.restricted_user).status_code == 404

    return check


def check_creating_is_for_admins(client: TestClient, setup: Setup) -> None:
    response = client.post("/api/v1/tags", json={"tag": "rating:public"}, headers=setup.restricted_user)
    assert response.status_code == 403


# Every route that reads or changes images, and how it must treat a restricted image. A new image route
# must be added here; test_every_image_route_is_checked fails until it is.
ACCESS_RULES = {
    ("GET", "/api/v1/images"): check_search_excludes,
    ("GET", "/api/v1/images/count"): check_count_excludes,
    ("POST", "/api/v1/images"): check_upload_refuses,
    ("POST", "/api/v1/images/bulk-tags"): check_bulk_refuses,
    ("GET", "/api/v1/images/{image_id}"): expect_not_found("GET", "/api/v1/images/{image_id}"),
    ("GET", "/api/v1/images/{image_id}/tag-counts"): expect_not_found("GET", "/api/v1/images/{image_id}/tag-counts"),
    ("PATCH", "/api/v1/images/{image_id}"): expect_not_found("PATCH", "/api/v1/images/{image_id}"),
    ("GET", "/api/v1/images/{image_id}/file"): expect_not_found("GET", "/api/v1/images/{image_id}/file"),
    ("GET", "/api/v1/images/{image_id}/thumbnail"): expect_not_found("GET", "/api/v1/images/{image_id}/thumbnail"),
    ("GET", "/api/v1/images/{image_id}/storyboard"): expect_video_not_found("/api/v1/images/{image_id}/storyboard"),
    ("GET", "/api/v1/images/{image_id}/storyboard/sprite"): expect_video_not_found(
        "/api/v1/images/{image_id}/storyboard/sprite"
    ),
    ("POST", "/api/v1/images/{image_id}/stream"): expect_video_not_found("/api/v1/images/{image_id}/stream", "POST"),
    ("POST", "/api/v1/images/{image_id}/rotation"): expect_not_found("POST", "/api/v1/images/{image_id}/rotation"),
    ("GET", "/api/v1/tags"): check_autocomplete_hides,
    ("POST", "/api/v1/tags"): check_creating_is_for_admins,
    ("GET", "/api/v1/albums"): check_albums_leave_it_out,
    ("PATCH", "/api/v1/tags/{namespace}/{name}"): expect_admins_only("PATCH"),
    ("DELETE", "/api/v1/tags/{namespace}/{name}"): expect_admins_only("DELETE"),
}


@pytest.mark.parametrize("route", list(ACCESS_RULES), ids=[f"{method} {path}" for method, path in ACCESS_RULES])
def test_restricted_image_stays_hidden_on_every_route(
    client: TestClient, setup: Setup, route: tuple[str, str]
) -> None:
    ACCESS_RULES[route](client, setup)


def test_every_image_route_is_checked(client: TestClient) -> None:
    """Reads the routes from the API schema, which lists every REST route the app serves."""
    image_routes = {
        (method.upper(), path)
        for path, operations in client.app.openapi()["paths"].items()
        if path.startswith(("/api/v1/images", "/api/v1/tags", "/api/v1/albums"))
        for method in operations
    }

    assert image_routes == set(ACCESS_RULES), "Add the new image route to ACCESS_RULES with its expected behaviour."
