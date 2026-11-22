# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

import pytest
from sqlalchemy import delete

from photobook_db.db import Database
from photobook_db.db.models import User
from photobook_db.services.auth import AuthService, Role
from photobook_db.services.preferences import PreferencesService
from photobook_db.utils.problems import Problem

NAMESPACES = {"category", "author", "character", "rating", "album", "general"}


@pytest.fixture
def preferences(database: Database) -> PreferencesService:
    return PreferencesService(database, lambda: NAMESPACES)


@pytest.fixture
def user_ids(database: Database) -> list[int]:
    auth = AuthService(database)
    return [auth.create_user(name, "correct horse battery", Role.USER).id for name in ("restricted_user", "teen")]


def invalid(action: object) -> dict[str, str]:
    with pytest.raises(Problem) as raised:
        action()  # type: ignore[operator]

    assert raised.value.code == "validation_error"
    return raised.value.extra["errors"][0]


def test_no_hidden_filters_until_set(preferences: PreferencesService, user_ids: list[int]) -> None:
    assert preferences.hidden_filters(user_ids[0]) == []


def test_keeps_and_replaces_the_whole_list(preferences: PreferencesService, user_ids: list[int]) -> None:
    preferences.set_hidden_filters(user_ids[0], ["rating:private", " category:refs "])
    preferences.set_hidden_filters(user_ids[0], ["rating:private|restricted"])

    assert preferences.hidden_filters(user_ids[0]) == ["rating:private|restricted"]


def test_each_user_has_their_own(preferences: PreferencesService, user_ids: list[int]) -> None:
    preferences.set_hidden_filters(user_ids[0], ["rating:private"])

    assert preferences.hidden_filters(user_ids[1]) == []


def test_go_with_the_user(preferences: PreferencesService, user_ids: list[int], database: Database) -> None:
    preferences.set_hidden_filters(user_ids[0], ["rating:private"])

    with database.session() as session:
        session.execute(delete(User).where(User.id == user_ids[0]))
        session.commit()

    assert preferences.hidden_filters(user_ids[0]) == []


@pytest.mark.parametrize(
    ("hidden_filters", "field", "message"),
    [
        (["rating:private category:refs"], "body.hidden_filters.0", "One search term: no spaces"),
        (["rating:public", ""], "body.hidden_filters.1", "One search term: no spaces"),
        (["-rating:private"], "body.hidden_filters.0", "Write the term without a leading '-'"),
        (["a,,b"], "body.hidden_filters.0", "Expected a value (at position 2)"),
        ([f"tag_{number}" for number in range(51)], "body.hidden_filters", "At most 50 terms."),
    ],
)
def test_refuses_what_is_not_one_valid_term(
    preferences: PreferencesService, user_ids: list[int], hidden_filters: list[str], field: str, message: str
) -> None:
    error = invalid(lambda: preferences.set_hidden_filters(user_ids[0], hidden_filters))

    assert error["field"] == field
    assert error["message"].startswith(message)
    assert preferences.hidden_filters(user_ids[0]) == []  # nothing stored
