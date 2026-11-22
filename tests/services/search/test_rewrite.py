# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

import pytest

from photobook_db.services.search import parse_query, renamed_in_term, term_text, without_tag_in_term
from photobook_db.services.tags import TagName

NAMESPACES = {"general", "category", "author", "rating", "character"}
PRIVATE = TagName("rating", "private")


@pytest.mark.parametrize(
    "term",
    [
        "rating:private",
        "rating:private|restricted",
        "category:photo,rating:private",
        "category:(photo|artwork),-rating:private",
        "(category:photo,author:anon)|category:artwork",
        "general:beach|-(rating:private|restricted)",
        "(category:photo|category:artwork)|general:beach",
        "character:*",
        "author:an*",
        "year:2024",
        "date:2024-05-01..2024-06-30,general:beach",  # a prefix applies to the values after it
    ],
)
def test_text_parses_back_to_the_same_term(term: str) -> None:
    [tree] = parse_query(term, NAMESPACES).operands

    assert parse_query(term_text(tree), NAMESPACES).operands == (tree,)


@pytest.mark.parametrize(
    "term, renamed",
    [
        ("rating:private", "rating:explicit"),
        ("rating:private|restricted", "rating:explicit|rating:restricted"),
        ("rating:explicit|private", "rating:explicit"),  # the two became one
        ("category:photo,-rating:private", "category:photo,-rating:explicit"),
        ("rating:public|restricted", "rating:public|restricted"),  # not named: kept as written
        ("rating:ns*", "rating:ns*"),  # a pattern, not the tag
    ],
)
def test_rename_replaces_the_tag(term: str, renamed: str) -> None:
    assert renamed_in_term(term, PRIVATE, TagName("rating", "explicit"), NAMESPACES) == renamed


def test_rename_can_change_the_namespace() -> None:
    assert renamed_in_term(
        "general:nude|rating:private", TagName("general", "nude"), PRIVATE, NAMESPACES
    ) == "rating:private"


@pytest.mark.parametrize(
    "term, remaining",
    [
        ("rating:private", None),
        ("rating:private|restricted", "rating:restricted"),
        ("rating:(private|restricted),category:photo", "rating:restricted,category:photo"),
        ("category:photo,rating:private", None),
        ("category:photo,-rating:private", None),
        ("general:beach|-rating:private", "general:beach"),
        ("rating:private|-general:beach", "(-general:beach)"),  # no leading '-': stored terms refuse it
        ("character:*", "character:*"),
        ("rating:public", "rating:public"),
    ],
)
def test_delete_drops_the_tag_from_an_or_and_otherwise_the_term(term: str, remaining: str | None) -> None:
    assert without_tag_in_term(term, PRIVATE, NAMESPACES) == remaining
