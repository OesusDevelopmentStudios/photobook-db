# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

"""Searching images with the query language: parsing queries and turning them into database queries."""

from photobook_db.services.search._parser import (
    DATE_FIELDS,
    All,
    AnyOf,
    DateMatch,
    Node,
    Not,
    TagMatch,
    parse_query,
)
from photobook_db.services.search._conditions import matching_condition
from photobook_db.services.search._hidden_filters import FilteredQuery, apply_hidden_filters, mentioned_tags
from photobook_db.services.search._rewrite import renamed_in_term, term_text, without_tag_in_term
from photobook_db.services.search._terms import MAX_TERMS, TERM_MAX_LENGTH, checked_terms, tree_matches
from photobook_db.services.search._service import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    Order,
    SearchCount,
    SearchPage,
    SearchRequest,
    SearchService,
    Sort,
)

__all__ = [
    "DATE_FIELDS",
    "DEFAULT_LIMIT",
    "MAX_LIMIT",
    "MAX_TERMS",
    "TERM_MAX_LENGTH",
    "All",
    "AnyOf",
    "DateMatch",
    "FilteredQuery",
    "Node",
    "Not",
    "Order",
    "SearchCount",
    "SearchPage",
    "SearchRequest",
    "SearchService",
    "Sort",
    "TagMatch",
    "apply_hidden_filters",
    "checked_terms",
    "matching_condition",
    "mentioned_tags",
    "parse_query",
    "renamed_in_term",
    "term_text",
    "tree_matches",
    "without_tag_in_term",
]
