# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

import pytest

from photobook_db.db import Database
from photobook_db.db.models import Namespace, Tag
from photobook_db.services.tags import TagName, TagsService
from photobook_db.utils.problems import Problem

PATH_TEMPLATE_NAMESPACES = frozenset({"category", "author"})


@pytest.fixture
def tags(database: Database) -> TagsService:
    return TagsService(database, lambda: PATH_TEMPLATE_NAMESPACES)


def raised_problem(action: object) -> Problem:
    with pytest.raises(Problem) as raised:
        action()  # type: ignore[operator]
    return raised.value


def add_tag(database: Database, namespace_name: str, tag_name: str) -> None:
    with database.session() as session:
        namespace = session.query(Namespace).filter_by(name=namespace_name).one()
        session.add(Tag(namespace_id=namespace.id, name=tag_name))
        session.commit()


def test_refuses_tags_only_the_server_sets(tags: TagsService) -> None:
    problem = raised_problem(lambda: tags.resolve_tags(["summer", "Meta:Hi Res"]))

    assert (problem.status, problem.code, problem.extra["tags"]) == (422, "tag_read_only", ["meta:hi_res"])


# --- Namespaces

def test_lists_the_builtin_namespaces(tags: TagsService) -> None:
    namespaces = {namespace.name: namespace for namespace in tags.list_namespaces()}

    assert list(namespaces) == ["category", "author", "character", "rating", "album", "general", "meta"]
    assert all(namespace.is_builtin for namespace in namespaces.values())
    assert [name for name, namespace in namespaces.items() if not namespace.user_editable] == ["meta"]
    assert {name for name, namespace in namespaces.items() if namespace.in_path_template} == {"category", "author"}
    assert {name: namespace.allowed_values for name, namespace in namespaces.items() if namespace.allowed_values} == {
        "category": ("photo", "artwork"),
        "rating": ("public", "private"),
    }


def test_creates_a_namespace(tags: TagsService) -> None:
    created = tags.create_namespace(" Weather ", single_value=True)

    assert (created.name, created.single_value, created.is_builtin) == ("weather", True, False)
    assert tags.list_namespaces()[-1] == created


def test_existing_namespace_is_a_conflict(tags: TagsService) -> None:
    problem = raised_problem(lambda: tags.create_namespace("Album", single_value=False))

    assert (problem.status, problem.code) == (409, "already_exists")


def test_invalid_namespace_name_is_refused(tags: TagsService) -> None:
    assert raised_problem(lambda: tags.create_namespace("year", single_value=False)).status == 422


def test_deletes_an_unused_custom_namespace(tags: TagsService) -> None:
    tags.create_namespace("weather", single_value=False)

    tags.delete_namespace("weather")

    assert "weather" not in tags.namespace_names()


def test_builtin_namespace_cannot_be_deleted(tags: TagsService) -> None:
    problem = raised_problem(lambda: tags.delete_namespace("album"))

    assert (problem.status, problem.code) == (409, "namespace_in_use")
    assert problem.detail == "Built-in namespaces cannot be deleted."


def test_namespace_in_the_path_template_cannot_be_deleted(database: Database) -> None:
    tags = TagsService(database, lambda: frozenset({"category", "weather"}))
    tags.create_namespace("weather", single_value=True)

    problem = raised_problem(lambda: tags.delete_namespace("weather"))

    assert problem.detail == "The library's path template uses this namespace."


def test_namespace_with_tags_is_not_deleted_on_its_own(tags: TagsService, database: Database) -> None:
    """The tag admin takes the tags first, so that images, restrictions and hidden filters follow."""
    tags.create_namespace("weather", single_value=False)
    add_tag(database, "weather", "sunny")

    problem = raised_problem(lambda: tags.delete_namespace("weather"))

    assert (problem.code, problem.detail) == ("namespace_in_use", "It still has tags.")


def test_tells_how_much_a_namespace_is_used(tags: TagsService, database: Database) -> None:
    tags.create_namespace("weather", single_value=False)
    add_tag(database, "weather", "sunny")
    add_tag(database, "weather", "rainy")

    assert tags.namespace_usage("weather") == (2, 0)  # no image has them


def test_unknown_namespace_is_not_found(tags: TagsService) -> None:
    assert raised_problem(lambda: tags.delete_namespace("weather")).status == 404


# --- Parsing tags

def test_parses_and_normalises_a_tag(tags: TagsService) -> None:
    assert tags.parse_tag("Author:Sample Artist") == TagName("author", "sample_artist")


def test_new_namespace_becomes_a_tag_prefix(tags: TagsService) -> None:
    assert tags.parse_tag("weather:sunny") == TagName("general", "weather:sunny")

    tags.create_namespace("weather", single_value=False)

    assert tags.parse_tag("weather:sunny") == TagName("weather", "sunny")


# --- Resolving the tags given for an image

def test_resolve_keeps_each_tag_once_in_the_given_order(tags: TagsService) -> None:
    resolved = tags.resolve_tags(["summer", "Beach", "general:summer"])

    assert [str(tag) for tag in resolved] == ["general:summer", "general:beach"]


def test_resolve_keeps_the_last_tag_of_a_single_value_namespace(tags: TagsService) -> None:
    resolved = tags.resolve_tags(["category:photo", "summer", "category:artwork", "rating:public"])

    assert [str(tag) for tag in resolved] == ["general:summer", "category:artwork", "rating:public"]


def test_resolve_keeps_every_tag_of_a_multi_value_namespace(tags: TagsService) -> None:
    resolved = tags.resolve_tags(["author:first_artist", "author:second_artist"])

    assert [str(tag) for tag in resolved] == ["author:first_artist", "author:second_artist"]


# --- Ratings

def test_ratings_are_public_and_private_by_default(tags: TagsService) -> None:
    assert tags.fixed_values()["rating"] == ["public", "private"]
    assert tags.resolve_tags(["Rating:PRIVATE"]) == [TagName("rating", "private")]


def test_rating_that_is_not_listed_is_refused(tags: TagsService) -> None:
    problem = raised_problem(lambda: tags.resolve_tags(["summer", "rating:privat"]))

    assert (problem.status, problem.code, problem.extra["tag"]) == (422, "invalid_tag", "rating:privat")
    assert "public, private" in problem.detail


def test_a_new_rating_is_a_value_an_admin_adds(tags: TagsService) -> None:
    [secret] = tags.resolve_tags(["rating:Secret"], new_values_allowed=True)
    tags.create_tag(secret)

    assert tags.fixed_values()["rating"] == ["public", "private", "secret"]
    assert tags.resolve_tags(["rating:secret"]) == [TagName("rating", "secret")]
    assert next(info for info in tags.list_namespaces() if info.name == "rating").allowed_values == (
        "public", "private", "secret"
    )
