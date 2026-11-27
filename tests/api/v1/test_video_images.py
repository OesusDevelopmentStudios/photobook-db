# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

"""Videos are rows of the image table, found only when a client asks for them: a client from before videos never
receives a file it cannot show. The search tests make a video in the database; the upload tests send a real one,
made by FFmpeg (skipped without it)."""

import io
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import update

from photobook_db.db.models import Image as ImageModel
from photobook_db.services.auth import Role
from photobook_db.services.media_probe import MediaProbe

PASSWORD = "correct horse battery"


@pytest.fixture
def headers(client: TestClient) -> dict[str, str]:
    client.app.state.auth.create_user("restricted_user", PASSWORD, Role.USER)
    login = {"username": "restricted_user", "password": PASSWORD, "device_name": "t"}
    return {"Authorization": f"Bearer {client.post('/api/v1/auth/tokens', json=login).json()['token']}"}


def upload(client: TestClient, headers: dict[str, str], file_name: str, colour: str) -> int:
    buffer = io.BytesIO()
    Image.new("RGB", (40, 30), colour).save(buffer, format="JPEG")
    metadata = {"original_filename": file_name, "tags": ["general:coast"]}
    response = client.post(
        "/api/v1/images",
        headers=headers,
        files={"file": (file_name, buffer.getvalue(), "image/jpeg")},
        data={"metadata": json.dumps(metadata)},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


@pytest.fixture
def coast(client: TestClient, headers: dict[str, str]) -> dict[str, int]:
    """A photo and a video of the coast."""
    photo = upload(client, headers, "photo.jpg", "teal")
    video = upload(client, headers, "waves.jpg", "navy")
    with client.app.state.database.session() as session:
        session.execute(
            update(ImageModel)
            .where(ImageModel.id == video)
            .values(
                media_kind="video",
                mime_type="video/mp4",
                taken_at_source="container",
                duration_ms=222400,
                video_codec="h264",
                audio_codec="aac",
                bitrate=8120000,
                frame_rate=29.97,
            )
        )
        session.commit()
    return {"photo": photo, "video": video}


def found(client: TestClient, headers: dict[str, str], **params: object) -> list[int]:
    response = client.get("/api/v1/images", headers=headers, params={"q": "general:coast", **params})
    assert response.status_code == 200, response.text
    return sorted(image["id"] for image in response.json()["items"])


def counted(client: TestClient, headers: dict[str, str], **params: object) -> int:
    response = client.get("/api/v1/images/count", headers=headers, params={"q": "general:coast", **params})
    assert response.status_code == 200, response.text
    return response.json()["count"]


# As a client from before videos searches: without the parameter
def test_finds_images_only_unless_asked(client: TestClient, headers: dict[str, str], coast: dict[str, int]) -> None:
    assert found(client, headers) == [coast["photo"]]
    assert counted(client, headers) == 1


def test_finds_videos_or_both_when_asked(client: TestClient, headers: dict[str, str], coast: dict[str, int]) -> None:
    assert found(client, headers, media="video") == [coast["video"]]
    assert found(client, headers, media="all") == sorted(coast.values())
    assert counted(client, headers, media="video") == 1
    assert counted(client, headers, media="all") == 2


def test_refuses_an_unknown_kind(client: TestClient, headers: dict[str, str], coast: dict[str, int]) -> None:
    response = client.get("/api/v1/images", headers=headers, params={"media": "sound"})

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


def test_a_video_has_its_details(client: TestClient, headers: dict[str, str], coast: dict[str, int]) -> None:
    video = client.get(f"/api/v1/images/{coast['video']}", headers=headers).json()

    assert video["media_kind"] == "video"
    assert video["taken_at_source"] == "container"
    assert video["duration_ms"] == 222400
    assert video["video"] == {"video_codec": "h264", "audio_codec": "aac", "bitrate": 8120000, "frame_rate": 29.97}


# An image's object only gains its kind: a client from before videos sees nothing else new.
def test_only_a_video_links_to_a_storyboard(
    client: TestClient, headers: dict[str, str], coast: dict[str, int]
) -> None:
    video = client.get(f"/api/v1/images/{coast['video']}", headers=headers).json()
    photo = client.get(f"/api/v1/images/{coast['photo']}", headers=headers).json()

    assert video["links"]["storyboard"] == f"/api/v1/images/{coast['video']}/storyboard"
    assert "storyboard" not in photo["links"]


@pytest.mark.parametrize("path", ["storyboard", "storyboard/sprite"])
def test_an_image_has_no_storyboard(
    client: TestClient, headers: dict[str, str], coast: dict[str, int], path: str
) -> None:
    response = client.get(f"/api/v1/images/{coast['photo']}/{path}", headers=headers)

    assert (response.status_code, response.json()["code"]) == (404, "not_found")


@pytest.mark.parametrize("path", ["storyboard", "storyboard/sprite"])
def test_a_server_without_ffmpeg_has_no_storyboards(
    client: TestClient, headers: dict[str, str], coast: dict[str, int], path: str
) -> None:
    client.app.state.storyboards._media_probe = MediaProbe(ffprobe="no-such-ffprobe", ffmpeg="no-such-ffmpeg")

    response = client.get(f"/api/v1/images/{coast['video']}/{path}", headers=headers)

    assert (response.status_code, response.json()["code"]) == (503, "storyboard_unavailable")


def test_an_image_has_no_video_fields(client: TestClient, headers: dict[str, str], coast: dict[str, int]) -> None:
    photo = client.get(f"/api/v1/images/{coast['photo']}", headers=headers).json()

    assert photo["media_kind"] == "image"
    assert "duration_ms" not in photo
    assert "video" not in photo


# --- Uploading videos

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg is not installed")


def video_bytes(tmp_path: Path) -> bytes:
    path = tmp_path / "waves.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=1",
         "-c:v", "mpeg4", str(path)],
        check=True,
    )
    return path.read_bytes()


def upload_video(client: TestClient, headers: dict[str, str], content: bytes):  # -> httpx Response
    return client.post(
        "/api/v1/images",
        headers=headers,
        files={"file": ("waves.mp4", content, "video/mp4")},
        data={"metadata": json.dumps({"original_filename": "waves.mp4", "tags": ["general:coast"]})},
    )


@needs_ffmpeg
def test_uploads_a_video(client: TestClient, headers: dict[str, str], tmp_path: Path) -> None:
    response = upload_video(client, headers, video_bytes(tmp_path))

    assert response.status_code == 201, response.text
    video = response.json()
    assert (video["media_kind"], video["mime_type"]) == ("video", "video/mp4")
    assert video["video"]["video_codec"] == "mpeg4"
    assert "meta:video" in video["tags"]
    assert found(client, headers, media="video") == [video["id"]]


# A cleared thumbnail folder: the poster frame is made again on request.
@needs_ffmpeg
def test_makes_a_videos_thumbnail_again(client: TestClient, headers: dict[str, str], tmp_path: Path) -> None:
    video = upload_video(client, headers, video_bytes(tmp_path)).json()
    client.app.state.thumbnails.delete_thumbnails(video["sha256"])

    response = client.get(f"/api/v1/images/{video['id']}/thumbnail", headers=headers)

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/webp"


@needs_ffmpeg
def test_a_videos_storyboard(client: TestClient, headers: dict[str, str], tmp_path: Path) -> None:
    video = upload_video(client, headers, video_bytes(tmp_path)).json()  # 1 s, 160x120

    response = client.get(video["links"]["storyboard"], headers=headers)

    assert response.status_code == 200
    sprite_url = f"/api/v1/images/{video['id']}/storyboard/sprite"
    assert response.json() == {
        "interval_ms": 1000, "frame_count": 1, "frame_width": 160, "frame_height": 120, "columns": 1,
        "sprite": sprite_url,
    }
    sprite = client.get(sprite_url, headers=headers)
    assert (sprite.status_code, sprite.headers["content-type"]) == (200, "image/webp")
    assert sprite.headers["cache-control"] == "private, max-age=31536000, immutable"
    with Image.open(io.BytesIO(sprite.content)) as picture:
        assert picture.size == (160, 120)
    again = client.get(sprite_url, headers=headers | {"If-None-Match": sprite.headers["etag"]})
    assert again.status_code == 304


@needs_ffmpeg
def test_asking_for_the_sprite_first_makes_it(client: TestClient, headers: dict[str, str], tmp_path: Path) -> None:
    video = upload_video(client, headers, video_bytes(tmp_path)).json()

    sprite = client.get(f"/api/v1/images/{video['id']}/storyboard/sprite", headers=headers)

    assert (sprite.status_code, sprite.headers["content-type"]) == (200, "image/webp")


@needs_ffmpeg
def test_a_video_may_be_larger_than_the_image_limit(
    client: TestClient, headers: dict[str, str], tmp_path: Path
) -> None:
    content = video_bytes(tmp_path)
    client.app.state.settings.set("max_upload_bytes", len(content) // 2)

    response = upload_video(client, headers, content)

    assert response.status_code == 201, response.text


@needs_ffmpeg
def test_a_video_over_the_video_limit_is_refused(client: TestClient, headers: dict[str, str], tmp_path: Path) -> None:
    content = video_bytes(tmp_path)
    client.app.state.settings.set("max_video_upload_bytes", len(content) - 1)  # images keep a larger limit

    response = upload_video(client, headers, content)

    assert response.status_code == 413
    assert (response.json()["code"], response.json()["limit_bytes"]) == ("file_too_large", len(content) - 1)
    assert found(client, headers, media="all") == []


@needs_ffmpeg
def test_a_server_without_ffmpeg_says_why(client: TestClient, headers: dict[str, str], tmp_path: Path) -> None:
    client.app.state.importer._media_probe = MediaProbe(ffprobe="no-such-ffprobe", ffmpeg="no-such-ffmpeg")

    response = upload_video(client, headers, video_bytes(tmp_path))

    assert response.status_code == 415
    assert response.json()["detected_type"] == "video/mp4"
