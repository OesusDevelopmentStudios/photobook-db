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
            "/api/v1/media",
            headers={"Authorization": f"Bearer {token}"},
            files={"file": ("beach.jpg", buffer.getvalue())},
            data={"metadata": metadata},
        )
        assert response.status_code == 201


def post(browser: TestClient, path: str, **fields: str):  # -> Response
    form = {"csrf_token": csrf_token_in(browser.get("/admin/namespaces").text)} | fields
    return browser.post(path, data=form, follow_redirects=False)


def rows(page: str) -> dict[str, str]:
    """Each tag's row on a namespace page, by the tag's name."""
    found = re.findall(r"<tr>\s*<td><code>([^<]+)</code></td>(.*?)</tr>", page, re.DOTALL)
    return dict(found)


def test_needs_a_login(client: TestClient) -> None:
    assert client.get("/admin/namespaces/general/tags/summer/delete", follow_redirects=False).status_code == 303


# --- Each row's buttons

def test_each_tag_shows_how_many_have_it(admin_browser: TestClient, library: None) -> None:
    page = admin_browser.get("/admin/namespaces/author/tags").text

    assert "<th>Photos and videos</th>" in page
    assert "<td>2</td>" in rows(page)["sample_artist"]
    assert "<td>1</td>" in rows(page)["sam_other"]


def test_each_tag_has_a_rename_field_and_a_delete_button(admin_browser: TestClient, library: None) -> None:
    row = rows(admin_browser.get("/admin/namespaces/author/tags").text)["sam_other"]

    assert '<form method="post" action="/admin/namespaces/author/tags/sam_other/rename"' in row
    assert 'value="sam_other"' in row  # the name only: the page shows the namespace
    assert '<details class="rename">' in row  # closed until the pencil is clicked
    assert 'href="/admin/namespaces/author/tags/sam_other/delete"' in row


def test_tags_with_special_characters_get_working_addresses(admin_browser: TestClient, library: None) -> None:
    row = rows(admin_browser.get("/admin/namespaces/general/tags").text)["q&amp;a?#1"]

    assert 'href="/admin/namespaces/general/tags/q%26a%3F%231/delete"' in row
    question = admin_browser.get("/admin/namespaces/general/tags/q%26a%3F%231/delete").text
    assert "taken off 3 photos and videos" in question


def test_tags_the_server_sets_have_no_buttons(admin_browser: TestClient, library: None) -> None:
    page = admin_browser.get("/admin/namespaces/meta/tags").text

    assert "jpeg" in rows(page)
    assert "/rename" not in page and "/delete" not in page

    response = post(admin_browser, "/admin/namespaces/meta/tags/jpeg/delete")  # sent all the same

    assert response.status_code == 422
    assert "The server sets the meta tags from the files" in response.text


# --- Renaming

def test_renames_a_tag(admin_browser: TestClient, tags: TagsService, library: None) -> None:
    response = post(admin_browser, "/admin/namespaces/general/tags/summer/rename", new_tag="Summer Holiday")

    location = response.headers["location"]
    assert location == "/admin/namespaces/general/tags?search=summer_holiday&renamed=general%3Asummer_holiday"
    assert tags.media_count(TagName("general", "summer")) is None
    assert tags.media_count(TagName("general", "summer_holiday")) == 3
    page = admin_browser.get(location).text
    assert "The photos and videos now have <code>general:summer_holiday</code> instead of the old tag." in page
    assert list(rows(page)) == ["summer_holiday"]


def test_a_new_name_stays_in_the_namespace(admin_browser: TestClient, tags: TagsService, library: None) -> None:
    response = post(admin_browser, "/admin/namespaces/author/tags/sam_other/rename", new_tag="anon_two")

    assert response.headers["location"].startswith("/admin/namespaces/author/tags?search=anon_two&")
    assert tags.media_count(TagName("author", "anon_two")) == 1


def test_a_colon_that_names_no_namespace_is_part_of_the_name(
    admin_browser: TestClient, tags: TagsService, library: None
) -> None:
    post(admin_browser, "/admin/namespaces/general/tags/summer/rename", new_tag="re:zero")

    assert tags.media_count(TagName("general", "re:zero")) == 3


def test_renaming_into_another_namespace_shows_that_one(admin_browser: TestClient, library: None) -> None:
    response = post(admin_browser, "/admin/namespaces/general/tags/summer/rename", new_tag="album:summer")

    assert response.headers["location"].startswith("/admin/namespaces/album/tags?search=summer&")


def test_merging_into_a_path_tag_links_to_the_job_that_moves_the_files(
    admin_browser: TestClient, tags: TagsService, library: None
) -> None:
    response = post(admin_browser, "/admin/namespaces/author/tags/sam_other/rename", new_tag="author:sample_artist")

    location = response.headers["location"]
    job_id = int(location.rsplit("relayout_job=", 1)[1])
    assert tags.media_count(TagName("author", "sample_artist")) == 3
    assert f'<a href="/admin/import/{job_id}">Job {job_id}</a> moves' in admin_browser.get(location).text


def test_restricted_tags_follow_a_rename(admin_browser: TestClient, library: None) -> None:
    restricted_user = admin_browser.app.state.auth.create_user("restricted_user", PASSWORD, Role.USER)
    admin_browser.app.state.access.set_restricted_terms(restricted_user.id, ["general:summer"])

    post(admin_browser, "/admin/namespaces/general/tags/summer/rename", new_tag="holiday")

    assert admin_browser.app.state.access.restricted_terms(restricted_user.id) == ["general:holiday"]


def test_a_refused_rename_keeps_its_row_open_with_what_was_typed(
    admin_browser: TestClient, tags: TagsService, library: None
) -> None:
    response = post(admin_browser, "/admin/namespaces/general/tags/summer/rename", new_tag="a|b", search="sum")

    assert response.status_code == 422
    assert "Invalid tag. A tag name cannot contain &#39;|&#39;" in response.text
    row = rows(response.text)["summer"]
    assert '<details class="rename" open>' in row and 'value="a|b"' in row
    assert 'name="search" value="sum"' in row  # the same page as before
    assert tags.media_count(TagName("general", "summer")) == 3


# --- Deleting

def test_deletes_a_tag_after_asking(admin_browser: TestClient, tags: TagsService, library: None) -> None:
    question = admin_browser.get("/admin/namespaces/general/tags/summer/delete").text
    assert "The tag is taken off 3 photos and videos" in question
    assert '<a class="button secondary" href="/admin/namespaces/general/tags">Cancel</a>' in question

    response = post(admin_browser, "/admin/namespaces/general/tags/summer/delete")

    assert (response.status_code, response.headers["location"]) == (303, "/admin/namespaces/general/tags?deleted=true")
    assert tags.media_count(TagName("general", "summer")) is None
    assert "The tag has been deleted." in admin_browser.get(response.headers["location"]).text


def test_deleting_a_path_tag_links_to_the_job(admin_browser: TestClient, library: None) -> None:
    location = post(admin_browser, "/admin/namespaces/author/tags/sam_other/delete").headers["location"]

    assert location.startswith("/admin/namespaces/author/tags?deleted=true&relayout_job=")
    assert "moves the affected files" in admin_browser.get(location).text


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_deleting_an_unknown_tag_is_a_404_page(admin_browser: TestClient, method: str) -> None:
    path = "/admin/namespaces/author/tags/nobody/delete"
    response = admin_browser.get(path) if method == "GET" else post(admin_browser, path)

    assert response.status_code == 404
    assert "This tag does not exist" in response.text


# --- The Tags page's old addresses

@pytest.mark.parametrize(
    ("old_path", "new_path"),
    [
        ("/admin/tags", "/admin/namespaces"),
        ("/admin/tags/author/sam_other", "/admin/namespaces/author/tags?search=sam_other"),
        ("/admin/tags/author/sam_other/delete", "/admin/namespaces/author/tags/sam_other/delete"),
    ],
)
def test_old_addresses_lead_to_the_namespace_pages(client: TestClient, old_path: str, new_path: str) -> None:
    response = client.get(old_path, follow_redirects=False)

    assert (response.status_code, response.headers["location"]) == (301, new_path)
