# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

"""An album's images: GET /images and /images/count with album=, and album order (sort=album)."""

import io
import json
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select, update

from photobook_db.db.models import Namespace, Tag, image_tag
from photobook_db.services.auth import Role

PASSWORD = "correct horse battery"


@pytest.fixture
def headers(client: TestClient) -> dict[str, str]:
    client.app.state.auth.create_user("admin_user", PASSWORD, Role.ADMIN)
    login = {"username": "admin_user", "password": PASSWORD, "device_name": "t"}
    return {"Authorization": f"Bearer {client.post('/api/v1/auth/tokens', json=login).json()['token']}"}


def upload(client: TestClient, headers: dict[str, str], file_name: str, colour: str, tags: list[str]) -> int:
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), colour).save(buffer, format="JPEG")
    metadata = json.dumps({"original_filename": file_name, "tags": tags})
    response = client.post(
        "/api/v1/images", headers=headers, files={"file": (file_name, buffer.getvalue())}, data={"metadata": metadata}
    )
    assert response.status_code == 201
    return response.json()["id"]


def added_to_album(client: TestClient, image_id: int, album: str, when: datetime) -> None:
    """As if the image had been added to the album at `when`."""
    album_tag = select(Tag.id).join(Namespace).where(Namespace.name == "album", Tag.name == album)
    with client.app.state.database.session() as session:
        session.execute(
            update(image_tag)
            .where(image_tag.c.image_id == image_id, image_tag.c.tag_id == album_tag.scalar_subquery())
            .values(added_at=when)
        )
        session.commit()


@pytest.fixture
def trip(client: TestClient, headers: dict[str, str]) -> dict[str, int]:
    """An album "trip": hills.jpg added first, then lake.jpg and beach.jpg at the same moment. city.jpg is elsewhere."""
    ids = {
        "lake": upload(client, headers, "lake.jpg", "blue", ["album:trip", "summer"]),
        "hills": upload(client, headers, "hills.jpg", "green", ["album:trip"]),
        "beach": upload(client, headers, "beach.jpg", "yellow", ["album:trip", "summer"]),
        "city": upload(client, headers, "city.jpg", "grey", ["album:holiday", "summer"]),
    }
    added_to_album(client, ids["hills"], "trip", datetime(2025, 1, 1))
    added_to_album(client, ids["lake"], "trip", datetime(2025, 2, 1))
    added_to_album(client, ids["beach"], "trip", datetime(2025, 2, 1))
    return ids


def found(client: TestClient, headers: dict[str, str], **params: object) -> list[int]:
    return [item["id"] for item in client.get("/api/v1/images", params=params, headers=headers).json()["items"]]


def test_finds_only_the_albums_images(client: TestClient, headers: dict[str, str], trip: dict[str, int]) -> None:
    assert set(found(client, headers, album="trip")) == {trip["lake"], trip["hills"], trip["beach"]}
    assert set(found(client, headers, album="trip", q="summer")) == {trip["lake"], trip["beach"]}
    assert found(client, headers, album="nowhere") == []


def test_counts_the_albums_images(client: TestClient, headers: dict[str, str], trip: dict[str, int]) -> None:
    response = client.get("/api/v1/images/count", params={"album": "trip", "q": "summer"}, headers=headers)

    assert response.json()["count"] == 2


def test_album_order_is_when_they_were_added_then_the_file_name(
    client: TestClient, headers: dict[str, str], trip: dict[str, int]
) -> None:
    in_order = [trip["hills"], trip["beach"], trip["lake"]]  # beach and lake were added at the same moment

    assert found(client, headers, album="trip", sort="album") == in_order
    assert found(client, headers, album="trip", sort="album", order="desc") == in_order  # order does not apply


def test_pages_through_an_album_in_its_order(client: TestClient, headers: dict[str, str], trip: dict[str, int]) -> None:
    pages: list[list[int]] = []
    cursor = None
    while len(pages) < 10:  # a cursor that never ends fails instead of hanging
        params = {"album": "trip", "sort": "album", "limit": 1} | ({} if cursor is None else {"cursor": cursor})
        body = client.get("/api/v1/images", params=params, headers=headers).json()
        pages.append([item["id"] for item in body["items"]])
        cursor = body["next_cursor"]
        if cursor is None:
            break

    assert pages == [[trip["hills"]], [trip["beach"]], [trip["lake"]]]


@pytest.mark.parametrize(
    ("params", "field"),
    [({"sort": "album"}, "query.sort"), ({"album": "trip", "sort": "album", "cursor": "broken"}, "query.cursor")],
    ids=["no album", "damaged cursor"],
)
def test_album_order_needs_an_album_and_its_own_cursor(
    client: TestClient, headers: dict[str, str], trip: dict[str, int], params: dict[str, str], field: str
) -> None:
    response = client.get("/api/v1/images", params=params, headers=headers)

    assert (response.status_code, response.json()["errors"][0]["field"]) == (422, field)


def test_a_restricted_image_stays_out_of_its_album(
    client: TestClient, headers: dict[str, str], trip: dict[str, int]
) -> None:
    client.app.state.auth.create_user("restricted_user", PASSWORD, Role.USER)
    restricted_user = next(user for user in client.app.state.auth.list_users() if user.username == "restricted_user")
    client.app.state.access.set_restricted_terms(restricted_user.id, ["general:summer"])
    login = {"username": "restricted_user", "password": PASSWORD, "device_name": "t"}
    restricted = {"Authorization": f"Bearer {client.post('/api/v1/auth/tokens', json=login).json()['token']}"}

    assert found(client, restricted, album="trip", sort="album") == [trip["hills"]]
