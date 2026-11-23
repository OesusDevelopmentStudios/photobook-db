# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from photobook_db.db.models import Namespace, Tag
from photobook_db.services.server_settings import SettingsChanges
from photobook_db.services.tags import NamespaceInfo, TagName, TagsService

from .conftest import csrf_token_in


@pytest.fixture
def tags(admin_browser: TestClient) -> TagsService:
    return admin_browser.app.state.tags


def post(browser: TestClient, path: str, **fields: str):  # -> Response
    form = {"csrf_token": csrf_token_in(browser.get("/admin/namespaces").text)} | fields
    return browser.post(path, data=form, follow_redirects=False)


def namespace(tags: TagsService, name: str) -> NamespaceInfo | None:
    return next((namespace for namespace in tags.list_namespaces() if namespace.name == name), None)


def removable(page: str) -> list[str]:
    """The namespaces whose Remove button works."""
    return re.findall(r'href="/admin/namespaces/(\w+)/delete"', page)


def test_needs_a_login(client: TestClient) -> None:
    assert client.get("/admin/namespaces", follow_redirects=False).status_code == 303


# --- The list

def test_lists_the_namespaces_and_which_can_be_removed(admin_browser: TestClient, tags: TagsService) -> None:
    tags.create_namespace("weather", single_value=True)
    tags.create_namespace("species", single_value=False)
    admin_browser.app.state.server_settings.change(SettingsChanges(path_template="{species}/{year}"), None)

    page = admin_browser.get("/admin/namespaces").text

    for name in ("category", "author", "character", "rating", "album", "general", "weather", "species"):
        assert f"<code>{name}:</code>" in page
    assert removable(page) == ["weather"]
    assert 'title="Built-in namespaces cannot be removed."' in page
    assert 'title="The path template uses this namespace. Change the template first."' in page
    assert '<a href="/admin/namespaces" aria-current="page">Namespaces</a>' in page


# --- New namespaces

@pytest.mark.parametrize("ticked, single_value", [({"single_value": "true"}, True), ({}, False)])
def test_creates_a_namespace(
    admin_browser: TestClient, tags: TagsService, ticked: dict[str, str], single_value: bool
) -> None:
    assert 'name="single_value"' in admin_browser.get("/admin/namespaces/new").text

    response = post(admin_browser, "/admin/namespaces/new", name=" Weather ", **ticked)

    assert (response.status_code, response.headers["location"]) == (303, "/admin/namespaces?created=true")
    created = namespace(tags, "weather")
    assert created is not None and created.single_value == single_value and not created.is_builtin


@pytest.mark.parametrize(
    "name, status, message",
    [
        ("author", 409, "Namespace exists."),
        ("year", 422, "Name: &#39;year&#39; is a search field"),
        ("two words", 422, "Name: 1 to 32 characters"),
    ],
    ids=["exists", "reserved", "invalid"],
)
def test_namespace_that_cannot_be_created_shows_why(
    admin_browser: TestClient, tags: TagsService, name: str, status: int, message: str
) -> None:
    before = tags.list_namespaces()

    response = post(admin_browser, "/admin/namespaces/new", name=name, single_value="true")

    assert response.status_code == status
    assert message in response.text
    assert f'value="{name}"' in response.text and " checked>" in response.text  # the form keeps what was entered
    assert tags.list_namespaces() == before


# --- Removing

def test_removes_an_unused_namespace(admin_browser: TestClient, tags: TagsService) -> None:
    tags.create_namespace("weather", single_value=True)

    response = post(admin_browser, "/admin/namespaces/weather/delete")

    assert (response.status_code, response.headers["location"]) == (303, "/admin/namespaces?deleted=true")
    assert namespace(tags, "weather") is None


def test_asks_before_removing_a_namespace_and_its_tags(admin_browser: TestClient, tags: TagsService) -> None:
    tags.create_namespace("weather", single_value=True)
    with admin_browser.app.state.database.session() as session:
        weather = session.scalars(select(Namespace).where(Namespace.name == "weather")).one()
        session.add_all([Tag(namespace_id=weather.id, name="sunny"), Tag(namespace_id=weather.id, name="rainy")])
        session.commit()

    question = admin_browser.get("/admin/namespaces/weather/delete").text

    assert "This will remove 2 tags and affect\n0 images." in question

    response = post(admin_browser, "/admin/namespaces/weather/delete")

    assert (response.status_code, response.headers["location"]) == (303, "/admin/namespaces?deleted=true")
    assert namespace(tags, "weather") is None
    assert not tags.tag_exists(TagName("weather", "sunny"))


def test_a_builtin_namespace_is_not_offered_for_removal(admin_browser: TestClient) -> None:
    response = admin_browser.get("/admin/namespaces/album/delete")

    assert response.status_code == 409
    assert "Built-in namespaces cannot be deleted." in response.text


# --- One namespace's page with its tags

def add_tags(admin_browser: TestClient, namespace_name: str, names: list[str]) -> None:
    """Tags no image has, straight into the database."""
    with admin_browser.app.state.database.session() as session:
        namespace_id = session.scalar(select(Namespace.id).where(Namespace.name == namespace_name))
        session.add_all(Tag(namespace_id=namespace_id, name=name) for name in names)
        session.commit()


def listed_tags(page: str) -> list[str]:
    return re.findall(r"<tr>\s*<td><code>([^<]+)</code></td>", page)


def test_each_namespace_has_an_edit_button(admin_browser: TestClient) -> None:
    page = admin_browser.get("/admin/namespaces").text

    assert re.search(r'<a class="button secondary" href="/admin/namespaces/author/tags">\s*<svg', page)
    assert "<td><code>author:</code></td>" in page  # the name itself is no link
    assert '<a class="button secondary" href="/admin/namespaces/meta/tags">View</a>' in page  # nothing to edit


def test_shows_the_namespace_and_its_tags(admin_browser: TestClient) -> None:
    add_tags(admin_browser, "author", ["sample_artist", "anon_one"])

    page = admin_browser.get("/admin/namespaces/author/tags").text

    assert "Namespace <code>author:</code>" in page
    assert "<dt>Folders</dt><dd>in the path template</dd>" in page
    assert listed_tags(page) == ["anon_one", "sample_artist"]  # the same count: by name


def test_finds_tags_starting_with_the_search(admin_browser: TestClient) -> None:
    add_tags(admin_browser, "general", ["harbour", "hills", "lake"])

    page = admin_browser.get("/admin/namespaces/general/tags", params={"search": "h"}).text

    assert listed_tags(page) == ["harbour", "hills"]


def test_pages_through_many_tags(admin_browser: TestClient) -> None:
    add_tags(admin_browser, "general", [f"tag_{number:03}" for number in range(101)])

    first = admin_browser.get("/admin/namespaces/general/tags").text
    next_path = re.search(r'href="([^"]+)">Next page</a>', first)

    assert len(listed_tags(first)) == 100 and "First page" not in first
    assert next_path is not None
    second = admin_browser.get(next_path.group(1).replace("&amp;", "&")).text
    assert listed_tags(second) == ["tag_100"]
    assert "Next page" not in second and "First page" in second


def test_adds_a_tag_that_users_then_get_as_a_suggestion(admin_browser: TestClient) -> None:
    response = post(admin_browser, "/admin/namespaces/general/tags", tag_name=" Old Harbour ")

    assert (response.status_code, response.headers["location"]) == (
        303, "/admin/namespaces/general/tags?added=old_harbour"
    )
    page = admin_browser.get(response.headers["location"]).text
    assert "The tag <code>general:old_harbour</code> has been added." in page
    assert listed_tags(page) == ["old_harbour"]


@pytest.mark.parametrize(
    ("tag_name", "status", "message"),
    [("harbour", 409, "exists already"), ("a|b", 422, "Invalid tag")],
)
def test_says_why_a_tag_cannot_be_added(admin_browser: TestClient, tag_name: str, status: int, message: str) -> None:
    add_tags(admin_browser, "general", ["harbour"])

    response = post(admin_browser, "/admin/namespaces/general/tags", tag_name=tag_name)

    assert response.status_code == status
    assert message in response.text
    assert f'value="{tag_name}"' in response.text  # kept, to correct


def test_the_meta_namespace_takes_no_new_tags(admin_browser: TestClient) -> None:
    page = admin_browser.get("/admin/namespaces/meta/tags").text

    assert "cannot be added, renamed or deleted here" in page
    assert 'name="tag_name"' not in page
    assert post(admin_browser, "/admin/namespaces/meta/tags", tag_name="video").status_code == 422


@pytest.mark.parametrize("path", ["/admin/namespaces/nowhere/tags", "/admin/namespaces/general/tags?cursor=broken"])
def test_unknown_namespace_or_page_is_not_found(admin_browser: TestClient, path: str) -> None:
    assert admin_browser.get(path).status_code == 404


# --- Fixed values: the rating page

def test_the_rating_page_lists_its_values(admin_browser: TestClient) -> None:
    page = admin_browser.get("/admin/namespaces/rating/tags").text

    assert "<dt>Values</dt><dd>fixed: images may have only the values listed here</dd>" in page
    assert "<h2>Add a value</h2>" in page and "<h2>Values</h2>" in page
    assert listed_tags(page) == ["private", "public"]  # no image has either: by name
    assert "deleting one takes it off every image" in page


def test_adds_a_rating_value(admin_browser: TestClient, tags: TagsService) -> None:
    response = post(admin_browser, "/admin/namespaces/rating/tags", tag_name="Secret")

    page = admin_browser.get(response.headers["location"]).text
    assert "The value <code>rating:secret</code> has been added.\nImages may have it now." in page
    assert tags.fixed_values()["rating"] == ["public", "private", "secret"]


def test_asks_before_deleting_a_value(admin_browser: TestClient) -> None:
    page = admin_browser.get("/admin/namespaces/rating/tags/private/delete").text

    assert "Delete value <code>rating:private</code>?" in page
    assert "Images can no longer be given it." in page
