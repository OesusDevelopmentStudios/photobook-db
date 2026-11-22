# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

import io
import json
import re

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from photobook_db.services.auth import Role
from photobook_db.services.tags import TagName, TagsService

from .conftest import PASSWORD, csrf_token_in


@pytest.fixture
def tags(admin_browser: TestClient) -> TagsService:
    return admin_browser.app.state.tags


@pytest.fixture
def library(admin_browser: TestClient) -> None:
    """Three images: author:sample_artist on two of them, author:sam_other on one, summer on all."""
    token = admin_browser.app.state.auth.create_token("admin_user", PASSWORD, "test").token
    for colour, author in [("red", "sample_artist"), ("teal", "sample_artist"), ("navy", "sam_other")]:
        buffer = io.BytesIO()
        Image.new("RGB", (8, 8), colour).save(buffer, format="JPEG")
        metadata = json.dumps({"original_filename": "beach.jpg", "tags": [f"author:{author}", "summer", "q&a?#1"]})
        response = admin_browser.post(
            "/api/v1/images",
            headers={"Authorization": f"Bearer {token}"},
            files={"file": ("beach.jpg", buffer.getvalue())},
            data={"metadata": metadata},
        )
        assert response.status_code == 201


def post(browser: TestClient, path: str, **fields: str):  # -> Response
    form = {"csrf_token": csrf_token_in(browser.get("/admin/tags").text)} | fields
    return browser.post(path, data=form, follow_redirects=False)


def listed(page: str) -> list[tuple[str, str]]:
    """The tags and their counts, without the meta tags every image has."""
    found = re.findall(r"<code>([^<]+)</code></a></td>\s*<td>(\d+)</td>", page)
    return [(tag, count) for tag, count in found if not tag.startswith("meta:")]


def test_needs_a_login(client: TestClient) -> None:
    assert client.get("/admin/tags", follow_redirects=False).status_code == 303


# --- Finding tags

def test_lists_the_most_used_tags_first(admin_browser: TestClient, library: None) -> None:
    page = admin_browser.get("/admin/tags").text

    # the same count: by name
    assert listed(page)[:3] == [("general:q&amp;a?#1", "3"), ("general:summer", "3"), ("author:sample_artist", "2")]
    assert '<a href="/admin/tags" aria-current="page">Tags</a>' in page


@pytest.mark.parametrize(
    "query, found",
    [
        ({"search": "author:sam"}, [("author:sample_artist", "2"), ("author:sam_other", "1")]),
        ({"search": "sam", "namespace": "author"}, [("author:sample_artist", "2"), ("author:sam_other", "1")]),
        ({"search": "nothing"}, []),
    ],
    ids=["prefix with namespace", "namespace chosen", "no match"],
)
def test_finds_tags_by_prefix(
    admin_browser: TestClient, library: None, query: dict[str, str], found: list[tuple[str, str]]
) -> None:
    page = admin_browser.get("/admin/tags", params=query).text

    assert listed(page) == found
    assert ("No tags found." in page) == (found == [])


def test_links_to_tags_with_special_characters(admin_browser: TestClient, library: None) -> None:
    assert '<a href="/admin/tags/general/q%26a%3F%231">' in admin_browser.get("/admin/tags").text

    page = admin_browser.get("/admin/tags/general/q%26a%3F%231").text

    assert "3 images have" in page


# --- One tag

def test_shows_a_tag(admin_browser: TestClient, library: None) -> None:
    page = admin_browser.get("/admin/tags/author/sam_other").text

    assert "Tag <code>author:sam_other</code>" in page
    assert "1 image has" in page
    assert 'name="new_tag" required autocomplete="off" spellcheck="false" value="author:sam_other"' in page


def test_unknown_tag_is_a_404_page(admin_browser: TestClient) -> None:
    response = admin_browser.get("/admin/tags/author/nobody")

    assert response.status_code == 404
    assert "This tag does not exist" in response.text


def test_renames_a_tag(admin_browser: TestClient, tags: TagsService, library: None) -> None:
    response = post(admin_browser, "/admin/tags/general/summer/rename", new_tag="Summer Holiday")

    assert response.headers["location"] == "/admin/tags/general/summer_holiday?renamed=true"
    assert tags.image_count(TagName("general", "summer")) is None
    assert tags.image_count(TagName("general", "summer_holiday")) == 3
    assert "The images now have this tag instead" in admin_browser.get(response.headers["location"]).text


def test_merging_into_a_path_tag_links_to_the_job_that_moves_the_files(
    admin_browser: TestClient, tags: TagsService, library: None
) -> None:
    response = post(admin_browser, "/admin/tags/author/sam_other/rename", new_tag="author:sample_artist")

    location = response.headers["location"]
    job_id = int(location.rsplit("relayout_job=", 1)[1])
    assert location.startswith("/admin/tags/author/sample_artist?renamed=true&relayout_job=")
    assert tags.image_count(TagName("author", "sample_artist")) == 3
    assert f'<a href="/admin/import/{job_id}">Job {job_id}</a> moves' in admin_browser.get(location).text


def test_restricted_tags_follow_a_rename(admin_browser: TestClient, library: None) -> None:
    restricted_user = admin_browser.app.state.auth.create_user("restricted_user", PASSWORD, Role.USER)
    admin_browser.app.state.access.set_restricted_terms(restricted_user.id, ["general:summer"])

    post(admin_browser, "/admin/tags/general/summer/rename", new_tag="holiday")

    assert admin_browser.app.state.access.restricted_terms(restricted_user.id) == ["general:holiday"]


def test_invalid_new_tag_shows_why(admin_browser: TestClient, tags: TagsService, library: None) -> None:
    response = post(admin_browser, "/admin/tags/general/summer/rename", new_tag="a|b")

    assert response.status_code == 422
    assert "Invalid tag. A tag name cannot contain &#39;|&#39;" in response.text
    assert 'value="a|b"' in response.text
    assert tags.image_count(TagName("general", "summer")) == 3


# --- Deleting

def test_deletes_a_tag_after_asking(admin_browser: TestClient, tags: TagsService, library: None) -> None:
    assert "The tag is taken off 3 images" in admin_browser.get("/admin/tags/general/summer/delete").text

    response = post(admin_browser, "/admin/tags/general/summer/delete")

    assert (response.status_code, response.headers["location"]) == (303, "/admin/tags?deleted=true")
    assert tags.image_count(TagName("general", "summer")) is None


def test_deleting_a_path_tag_links_to_the_job(admin_browser: TestClient, library: None) -> None:
    location = post(admin_browser, "/admin/tags/author/sam_other/delete").headers["location"]

    assert location.startswith("/admin/tags?deleted=true&relayout_job=")
    assert "moves the affected files" in admin_browser.get(location).text


def test_deleting_an_unknown_tag_is_a_404_page(admin_browser: TestClient) -> None:
    assert post(admin_browser, "/admin/tags/author/nobody/delete").status_code == 404
