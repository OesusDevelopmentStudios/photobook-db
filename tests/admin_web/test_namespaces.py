# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from photobook_db.db.models import Namespace, Tag
from photobook_db.services.server_settings import SettingsChanges
from photobook_db.services.tags import NamespaceInfo, TagsService

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
    return re.findall(r'action="/admin/namespaces/(\w+)/delete"', page)


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


def test_namespace_with_tags_is_not_removed(admin_browser: TestClient, tags: TagsService) -> None:
    tags.create_namespace("weather", single_value=True)
    with admin_browser.app.state.database.session() as session:
        weather = session.scalars(select(Namespace).where(Namespace.name == "weather")).one()
        session.add(Tag(namespace_id=weather.id, name="sunny"))
        session.commit()

    response = post(admin_browser, "/admin/namespaces/weather/delete")

    assert response.status_code == 409
    assert "Tags in this namespace exist; rename or delete them first." in response.text
    assert namespace(tags, "weather") is not None
