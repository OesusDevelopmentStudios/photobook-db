# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

import hashlib
import io
import json
import os
import threading
from pathlib import PurePosixPath

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import update

from photobook_db.api.v1 import _upload_receiver
from photobook_db.config import Config
from photobook_db.db.models import Image as ImageModel
from photobook_db.services.access import Restrictions
from photobook_db.utils.exif_orientation import read_orientation
from photobook_db.services.auth import Role

PASSWORD = "correct horse battery"


@pytest.fixture
def headers(client: TestClient) -> dict[str, str]:
    client.app.state.auth.create_user("restricted_user", PASSWORD, Role.USER)
    login = {"username": "restricted_user", "password": PASSWORD, "device_name": "t"}
    response = client.post("/api/v1/auth/tokens", json=login)
    return {"Authorization": f"Bearer {response.json()['token']}"}


def jpeg_bytes(size: tuple[int, int] = (40, 30), colour: str = "teal") -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, colour).save(buffer, format="JPEG")
    return buffer.getvalue()


def noise_jpeg_bytes(size: tuple[int, int]) -> bytes:
    """Random pixels compress badly, so the file is large for its size in pixels."""
    buffer = io.BytesIO()
    Image.effect_noise(size, 100).convert("RGB").save(buffer, format="JPEG")
    return buffer.getvalue()


def upload(
    client: TestClient,
    headers: dict[str, str],
    content: bytes,
    metadata: dict[str, object] | str | None = None,
):  # -> httpx Response
    default_metadata = {"original_filename": "beach.jpg"}
    metadata_text = metadata if isinstance(metadata, str) else json.dumps(metadata or default_metadata)
    return client.post(
        "/api/v1/images",
        headers=headers,
        files={"file": ("beach.jpg", content, "image/jpeg")},
        data={"metadata": metadata_text},
    )


def incoming_files(config: Config) -> list[str]:
    incoming_dir = config.library_dir / ".incoming"
    return os.listdir(incoming_dir) if incoming_dir.exists() else []


@pytest.fixture
def uploaded(client: TestClient, headers: dict[str, str]) -> dict:
    content = jpeg_bytes()
    metadata = {"original_filename": "beach.jpg", "tags": ["category:photo", "summer"]}
    response = upload(client, headers, content, metadata)
    assert response.status_code == 201
    return response.json() | {"content": content}


# --- Upload

def test_upload_returns_the_image_object(client: TestClient, headers: dict[str, str]) -> None:
    content = jpeg_bytes()
    metadata = {
        "original_filename": "beach.jpg",
        "file_mtime": "2018-07-14T16:02:11",
        "client_sha256": hashlib.sha256(content).hexdigest(),
        "tags": ["category:photo", "summer"],
    }

    response = upload(client, headers, content, metadata)

    assert response.status_code == 201
    image = response.json()
    assert response.headers["location"] == f"/api/v1/images/{image['id']}"
    assert image["sha256"] == hashlib.sha256(content).hexdigest()
    assert (image["mime_type"], image["width"], image["height"]) == ("image/jpeg", 40, 30)
    assert (image["taken_at"], image["taken_at_source"]) == ("2018-07-14T16:02:11", "file_mtime")
    assert image["tags"] == ["category:photo", "general:summer"]
    assert image["imported_at"].endswith("Z")
    assert image["links"] == {
        "self": f"/api/v1/images/{image['id']}",
        "file": f"/api/v1/images/{image['id']}/file",
        "thumbnail": f"/api/v1/images/{image['id']}/thumbnail",
    }


def test_upload_needs_a_login(client: TestClient) -> None:
    assert upload(client, {}, jpeg_bytes()).status_code == 401


def test_duplicate_upload_names_the_existing_image(
    client: TestClient, headers: dict[str, str], uploaded: dict
) -> None:
    response = upload(client, headers, uploaded["content"])

    assert response.status_code == 409
    assert response.json()["existing_image_id"] == uploaded["id"]


@pytest.mark.parametrize(
    ("content", "metadata", "status", "code"),
    [
        (b"not an image", {"original_filename": "a.jpg"}, 415, "unsupported_media_type"),
        (None, {"original_filename": "a.jpg", "client_sha256": "0" * 64}, 422, "checksum_mismatch"),
        (None, {"original_filename": "a.jpg", "tags": ["a|b"]}, 422, "invalid_tag"),
    ],
)
def test_refused_uploads_leave_nothing_behind(
    client: TestClient,
    headers: dict[str, str],
    config: Config,
    content: bytes | None,
    metadata: dict,
    status: int,
    code: str,
) -> None:
    response = upload(client, headers, content or jpeg_bytes(), metadata)

    assert (response.status_code, response.json()["code"]) == (status, code)
    assert incoming_files(config) == []


@pytest.mark.parametrize(
    ("metadata", "field"),
    [
        ("{not json", "metadata"),
        ({"tags": ["summer"]}, "metadata.original_filename"),
        ({"original_filename": "a.jpg", "client_sha256": "short"}, "metadata.client_sha256"),
    ],
)
def test_invalid_metadata_names_the_field(
    client: TestClient, headers: dict[str, str], config: Config, metadata: dict | str, field: str
) -> None:
    response = upload(client, headers, jpeg_bytes(), metadata)

    assert response.status_code == 422
    assert response.json()["errors"][0]["field"].startswith(field)
    assert incoming_files(config) == []


def test_missing_part_is_named(client: TestClient, headers: dict[str, str]) -> None:
    response = client.post(
        "/api/v1/images",
        headers=headers,
        data={"metadata": '{"original_filename": "a.jpg"}'},
        files={"other": ("x", b"x")},
    )

    assert response.status_code == 422
    assert response.json()["errors"][0] == {"field": "file", "message": "This part is missing."}


def test_body_must_be_multipart(client: TestClient, headers: dict[str, str]) -> None:
    response = client.post("/api/v1/images", headers=headers, json={"original_filename": "a.jpg"})

    assert response.status_code == 422
    assert response.json()["errors"][0]["field"] == "body"


@pytest.mark.parametrize(
    ("image_size", "parsing_started"),
    [((300, 200), True), ((1000, 800), False)],
    ids=["found while receiving", "refused by its announced size"],
)
def test_too_large_upload_is_refused(
    client: TestClient,
    headers: dict[str, str],
    config: Config,
    monkeypatch: pytest.MonkeyPatch,
    image_size: tuple[int, int],
    parsing_started: bool,
) -> None:
    client.app.state.settings.set("max_upload_bytes", 1000)
    content = noise_jpeg_bytes(image_size)
    parsers_created = []
    real_parser = _upload_receiver.MultipartParser
    monkeypatch.setattr(
        _upload_receiver, "MultipartParser", lambda *arguments: parsers_created.append(1) or real_parser(*arguments)
    )

    response = upload(client, headers, content)

    assert response.status_code == 413
    assert (response.json()["code"], response.json()["limit_bytes"]) == ("file_too_large", 1000)
    assert bool(parsers_created) is parsing_started
    assert incoming_files(config) == []


# --- Reading images back

def test_get_image_returns_the_same_object(client: TestClient, headers: dict[str, str], uploaded: dict) -> None:
    response = client.get(f"/api/v1/images/{uploaded['id']}", headers=headers)

    assert response.status_code == 200
    assert response.json() == {key: value for key, value in uploaded.items() if key != "content"}


def test_unknown_image_is_not_found(client: TestClient, headers: dict[str, str]) -> None:
    for path in ("/api/v1/images/999", "/api/v1/images/999/file", "/api/v1/images/999/thumbnail"):
        assert client.get(path, headers=headers).status_code == 404


def test_file_is_the_original(client: TestClient, headers: dict[str, str], uploaded: dict) -> None:
    response = client.get(f"/api/v1/images/{uploaded['id']}/file", headers=headers)

    assert response.status_code == 200
    assert response.content == uploaded["content"]
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["etag"] == f'"{uploaded["sha256"]}-0"'  # revision 0: as uploaded
    assert response.headers["content-disposition"] == 'inline; filename="beach.jpg"'


def test_file_unchanged_since_last_time_is_304(
    client: TestClient, headers: dict[str, str], uploaded: dict
) -> None:
    response = client.get(
        f"/api/v1/images/{uploaded['id']}/file", headers=headers | {"If-None-Match": f'"{uploaded["sha256"]}-0"'}
    )

    assert response.status_code == 304
    assert response.content == b""


def test_file_supports_ranges(client: TestClient, headers: dict[str, str], uploaded: dict) -> None:
    response = client.get(f"/api/v1/images/{uploaded['id']}/file", headers=headers | {"Range": "bytes=0-9"})

    assert response.status_code == 206
    assert response.content == uploaded["content"][:10]


@pytest.mark.parametrize("size", [256, 1024])
def test_thumbnail_is_cacheable_webp(client: TestClient, headers: dict[str, str], uploaded: dict, size: int) -> None:
    response = client.get(f"/api/v1/images/{uploaded['id']}/thumbnail", params={"size": size}, headers=headers)

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/webp"
    assert response.headers["cache-control"] == "private, max-age=31536000, immutable"
    assert response.headers["etag"] == f'"{uploaded["sha256"]}-0-{size}"'
    assert Image.open(io.BytesIO(response.content)).format == "WEBP"


def test_thumbnail_size_must_be_256_or_1024(client: TestClient, headers: dict[str, str], uploaded: dict) -> None:
    response = client.get(f"/api/v1/images/{uploaded['id']}/thumbnail", params={"size": 512}, headers=headers)

    assert response.status_code == 422


def test_thumbnail_unchanged_since_last_time_is_304(
    client: TestClient, headers: dict[str, str], uploaded: dict
) -> None:
    etag = f'"{uploaded["sha256"]}-0-256"'

    response = client.get(f"/api/v1/images/{uploaded['id']}/thumbnail", headers=headers | {"If-None-Match": etag})

    assert response.status_code == 304


def test_missing_thumbnail_is_made_again(client: TestClient, headers: dict[str, str], uploaded: dict) -> None:
    client.app.state.thumbnails.delete_thumbnails(uploaded["sha256"])

    response = client.get(f"/api/v1/images/{uploaded['id']}/thumbnail", headers=headers)

    assert response.status_code == 200
    assert client.app.state.thumbnails.thumbnail_path(uploaded["sha256"], 256).exists()


# After the file changed, e.g. a saved rotation: new ETags, and thumbnails of their own
def test_a_new_revision_has_new_etags_and_thumbnails(
    client: TestClient, headers: dict[str, str], uploaded: dict
) -> None:
    assert uploaded["revision"] == 0
    with client.app.state.database.session() as session:
        session.execute(update(ImageModel).where(ImageModel.id == uploaded["id"]).values(revision=1))
        session.commit()

    image = client.get(f"/api/v1/images/{uploaded['id']}", headers=headers).json()
    file = client.get(f"/api/v1/images/{uploaded['id']}/file", headers=headers)
    thumbnail = client.get(f"/api/v1/images/{uploaded['id']}/thumbnail", params={"rev": 1}, headers=headers)

    assert image["revision"] == 1
    assert file.headers["etag"] == f'"{uploaded["sha256"]}-1"'
    assert thumbnail.headers["etag"] == f'"{uploaded["sha256"]}-1-256"'
    assert client.app.state.thumbnails.thumbnail_path(uploaded["sha256"], 256, revision=1).exists()


# --- Turning images


def rotate(client: TestClient, headers: dict[str, str], image_id: int, degrees: int):  # -> httpx Response
    return client.post(f"/api/v1/images/{image_id}/rotation", json={"degrees": degrees}, headers=headers)


def image_file(client: TestClient, headers: dict[str, str], image_id: int) -> bytes:
    return client.get(f"/api/v1/images/{image_id}/file", headers=headers).content


def jpeg_image_data(content: bytes) -> bytes:
    """Everything from the start of the scan: the compressed pixels"""
    return content[content.index(b"\xff\xda"):]


def test_turns_a_jpeg_in_its_exif_orientation(client: TestClient, headers: dict[str, str], uploaded: dict) -> None:
    response = rotate(client, headers, uploaded["id"], 90)

    assert response.status_code == 200
    image = response.json()
    assert (image["width"], image["height"]) == (30, 40)
    assert image["revision"] == 1
    assert image["sha256"] == uploaded["sha256"]  # still a duplicate of the uploaded file
    assert image["storage_path"] == uploaded["storage_path"]
    content = image_file(client, headers, uploaded["id"])
    assert read_orientation(content) == 6
    assert jpeg_image_data(content) == jpeg_image_data(uploaded["content"])  # nothing lost
    assert image["file_size"] == len(content)


def test_makes_new_thumbnails_after_a_turn(client: TestClient, headers: dict[str, str], uploaded: dict) -> None:
    thumbnails = client.app.state.thumbnails

    rotate(client, headers, uploaded["id"], 90)

    response = client.get(f"/api/v1/images/{uploaded['id']}/thumbnail", params={"rev": 1}, headers=headers)
    assert Image.open(io.BytesIO(response.content)).size == (30, 40)
    assert thumbnails.thumbnail_path(uploaded["sha256"], 256, revision=1).exists()
    assert not thumbnails.thumbnail_path(uploaded["sha256"], 256).exists()  # the old ones are gone


# A tag change moves the file while a turn waits for its turn: the turn reads and writes the file at its new
# place, not where it was.
def test_a_turn_waits_while_files_change(client: TestClient, uploaded: dict) -> None:
    storage = client.app.state.storage
    moved_to = PurePosixPath("elsewhere") / PurePosixPath(uploaded["storage_path"]).name
    revisions: list[int] = []

    def turn() -> None:
        revisions.append(client.app.state.rotation.rotate(uploaded["id"], 90, Restrictions([])).revision)

    with storage.changing_files():
        turner = threading.Thread(target=turn)
        turner.start()
        turner.join(timeout=0.3)
        assert turner.is_alive()
        storage.move_image_file(uploaded["id"], moved_to)

    turner.join(timeout=5)
    assert revisions == [1]
    assert not storage.absolute_path(uploaded["storage_path"]).exists()
    assert read_orientation(storage.absolute_path(moved_to).read_bytes()) == 6


def test_turns_add_up(client: TestClient, headers: dict[str, str], uploaded: dict) -> None:
    rotate(client, headers, uploaded["id"], 90)

    image = rotate(client, headers, uploaded["id"], 90).json()

    assert (image["width"], image["height"]) == (40, 30)
    assert image["revision"] == 2
    assert read_orientation(image_file(client, headers, uploaded["id"])) == 3


def test_turns_the_pixels_of_a_png(client: TestClient, headers: dict[str, str]) -> None:
    original = Image.new("RGB", (40, 30), "red")
    original.paste("blue", (20, 0, 40, 30))
    buffer = io.BytesIO()
    original.save(buffer, format="PNG")
    uploaded = upload(client, headers, buffer.getvalue(), {"original_filename": "flag.png"}).json()

    image = rotate(client, headers, uploaded["id"], 270).json()

    assert (image["width"], image["height"]) == (30, 40)
    turned = Image.open(io.BytesIO(image_file(client, headers, uploaded["id"])))
    assert turned.format == "PNG"
    assert turned.tobytes() == original.transpose(Image.Transpose.ROTATE_90).tobytes()  # lossless


def test_refuses_to_turn_a_webp(client: TestClient, headers: dict[str, str]) -> None:
    buffer = io.BytesIO()
    Image.new("RGB", (40, 30), "teal").save(buffer, format="WEBP")
    uploaded = upload(client, headers, buffer.getvalue(), {"original_filename": "sea.webp"}).json()

    response = rotate(client, headers, uploaded["id"], 90)

    assert response.status_code == 422
    assert response.json()["code"] == "rotation_not_supported"
    assert image_file(client, headers, uploaded["id"]) == buffer.getvalue()
    assert client.get(f"/api/v1/images/{uploaded['id']}", headers=headers).json()["revision"] == 0


def test_turns_only_by_quarters(client: TestClient, headers: dict[str, str], uploaded: dict) -> None:
    assert rotate(client, headers, uploaded["id"], 45).status_code == 422


def test_cannot_turn_an_unknown_image(client: TestClient, headers: dict[str, str]) -> None:
    assert rotate(client, headers, 999, 90).status_code == 404


# --- Searching

def test_search_needs_a_login(client: TestClient) -> None:
    assert client.get("/api/v1/images").status_code == 401


def test_search_returns_image_objects(client: TestClient, headers: dict[str, str], uploaded: dict) -> None:
    response = client.get("/api/v1/images", params={"q": "category:photo summer"}, headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert [item["id"] for item in body["items"]] == [uploaded["id"]]
    assert body["items"][0]["tags"] == ["category:photo", "general:summer"]
    assert body["next_cursor"] is None
    assert body["effective_query"] == "category:photo summer"


def test_search_pages_with_the_cursor(client: TestClient, headers: dict[str, str]) -> None:
    for colour in ("red", "green", "blue"):
        upload(client, headers, jpeg_bytes(colour=colour))

    first = client.get("/api/v1/images", params={"limit": 2}, headers=headers).json()
    second = client.get("/api/v1/images", params={"limit": 2, "cursor": first["next_cursor"]}, headers=headers).json()

    assert len(first["items"]) == 2 and len(second["items"]) == 1
    assert second["next_cursor"] is None


def test_count_needs_a_login(client: TestClient) -> None:
    assert client.get("/api/v1/images/count").status_code == 401


def test_count_says_how_many_a_search_finds(client: TestClient, headers: dict[str, str], uploaded: dict) -> None:
    for colour in ("red", "green"):
        upload(client, headers, jpeg_bytes(colour=colour))

    everything = client.get("/api/v1/images/count", headers=headers)
    photos = client.get("/api/v1/images/count", params={"q": "category:photo summer"}, headers=headers)

    assert everything.status_code == 200
    assert everything.json() == {"count": 3, "effective_query": ""}
    assert photos.json() == {"count": 1, "effective_query": "category:photo summer"}


def test_count_refuses_an_invalid_query(client: TestClient, headers: dict[str, str]) -> None:
    response = client.get("/api/v1/images/count", params={"q": "summer beach,,sand"}, headers=headers)

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_query"


def test_invalid_query_says_where(client: TestClient, headers: dict[str, str]) -> None:
    response = client.get("/api/v1/images", params={"q": "summer beach,,sand"}, headers=headers)

    assert response.status_code == 400
    assert (response.json()["code"], response.json()["position"]) == ("invalid_query", 13)


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 501}, {"sort": "size"}, {"sort": "random"}])
def test_invalid_search_parameters(client: TestClient, headers: dict[str, str], params: dict) -> None:
    assert client.get("/api/v1/images", params=params, headers=headers).status_code == 422


# --- Changing tags

def test_changing_tags_needs_a_login(client: TestClient) -> None:
    assert client.patch("/api/v1/images/1", json={"add_tags": ["x"]}).status_code == 401
    assert client.post("/api/v1/images/bulk-tags", json={"image_ids": [1], "add_tags": ["x"]}).status_code == 401


def test_patch_adds_and_removes_tags(client: TestClient, headers: dict[str, str], uploaded: dict) -> None:
    response = client.patch(
        f"/api/v1/images/{uploaded['id']}", json={"add_tags": ["Sunset"], "remove_tags": ["summer"]}, headers=headers
    )

    assert response.status_code == 200
    assert response.json()["tags"] == ["category:photo", "general:sunset"]


def test_patch_moves_the_file_with_a_folder_forming_tag(
    client: TestClient, headers: dict[str, str], uploaded: dict
) -> None:
    response = client.patch(
        f"/api/v1/images/{uploaded['id']}", json={"add_tags": ["category:artwork"]}, headers=headers
    )

    assert response.json()["storage_path"].startswith("artwork/")
    file_response = client.get(f"/api/v1/images/{uploaded['id']}/file", headers=headers)
    assert file_response.content == uploaded["content"]


@pytest.mark.parametrize(
    ("path_id", "body", "status", "code"),
    [
        (None, {"add_tags": ["a|b"]}, 422, "invalid_tag"),
        (999, {"add_tags": ["summer"]}, 404, "not_found"),
    ],
)
def test_patch_refusals(
    client: TestClient, headers: dict[str, str], uploaded: dict, path_id: int | None, body: dict, status: int, code: str
) -> None:
    response = client.patch(f"/api/v1/images/{path_id or uploaded['id']}", json=body, headers=headers)

    assert (response.status_code, response.json()["code"]) == (status, code)


def test_bulk_changes_every_image(client: TestClient, headers: dict[str, str]) -> None:
    ids = [upload(client, headers, jpeg_bytes(colour=colour)).json()["id"] for colour in ("red", "green")]

    body = {"image_ids": ids, "add_tags": ["album:trip"]}

    response = client.post("/api/v1/images/bulk-tags", json=body, headers=headers)

    assert response.json() == {"updated": 2}
    for image_id in ids:
        assert client.get(f"/api/v1/images/{image_id}", headers=headers).json()["tags"] == ["album:trip"]


def test_bulk_with_an_unknown_id_changes_nothing(client: TestClient, headers: dict[str, str], uploaded: dict) -> None:
    body = {"image_ids": [uploaded["id"], 999], "add_tags": ["album:trip"]}

    response = client.post("/api/v1/images/bulk-tags", json=body, headers=headers)

    assert response.status_code == 404
    assert "album:trip" not in client.get(f"/api/v1/images/{uploaded['id']}", headers=headers).json()["tags"]


@pytest.mark.parametrize("image_ids", [[], list(range(1, 1002))], ids=["none", "over 1000"])
def test_bulk_takes_1_to_1000_images(client: TestClient, headers: dict[str, str], image_ids: list[int]) -> None:
    body = {"image_ids": image_ids, "add_tags": ["x"]}

    response = client.post("/api/v1/images/bulk-tags", json=body, headers=headers)

    assert response.status_code == 422
    assert response.json()["errors"][0]["field"] == "body.image_ids"
