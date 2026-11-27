# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

import re
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from photobook_db.db import Database
from photobook_db.db.models import ApiToken, User
from photobook_db.services.auth import AuthService, Role
from photobook_db.utils.problems import Problem

PASSWORD = "correct horse battery"


class TestClock:
    """Time that only moves when a test moves it."""

    __test__ = False  # not a test class for pytest

    def __init__(self) -> None:
        self.current = datetime(2026, 11, 12, 12, 0, 0)

    def now(self) -> datetime:
        return self.current

    def advance(self, duration: timedelta) -> None:
        self.current += duration


@pytest.fixture
def clock() -> TestClock:
    return TestClock()


@pytest.fixture
def auth(database: Database, clock: TestClock) -> AuthService:
    return AuthService(database, now=clock.now)


def raised_problem(action: object) -> Problem:
    with pytest.raises(Problem) as raised:
        action()  # type: ignore[operator]
    return raised.value


# --- First admin account

def test_setup_is_required_until_an_admin_exists(auth: AuthService) -> None:
    assert auth.is_setup_required()

    auth.create_user("restricted_user", PASSWORD, Role.USER)
    assert auth.is_setup_required()  # a normal user is not enough

    auth.create_user("admin_user", PASSWORD, Role.ADMIN)
    assert not auth.is_setup_required()
    assert auth.setup_code() is None


def test_setup_code_is_readable_and_stays_the_same(auth: AuthService) -> None:
    code = auth.setup_code()

    assert code is not None
    assert re.fullmatch(r"[2-9A-HJKMNP-Z]{4}-[2-9A-HJKMNP-Z]{4}-[2-9A-HJKMNP-Z]{4}", code)
    assert auth.setup_code() == code


def test_complete_setup_creates_the_first_admin(auth: AuthService) -> None:
    code = auth.setup_code()
    assert code is not None

    admin = auth.complete_setup(code.lower().replace("-", " "), "Admin_User", PASSWORD)  # typed casually

    assert admin.username == "admin_user"
    assert admin.role == Role.ADMIN
    assert not auth.is_setup_required()


def test_complete_setup_refuses_wrong_code(auth: AuthService) -> None:
    problem = raised_problem(lambda: auth.complete_setup("AAAA-BBBB-CCCC", "admin_user", PASSWORD))

    assert (problem.status, problem.code) == (403, "wrong_setup_code")
    assert auth.is_setup_required()


def test_complete_setup_only_once(auth: AuthService) -> None:
    code = auth.setup_code()
    assert code is not None
    auth.complete_setup(code, "admin_user", PASSWORD)

    problem = raised_problem(lambda: auth.complete_setup(code, "intruder", PASSWORD))

    assert (problem.status, problem.code) == (409, "setup_done")


# --- Users

def test_usernames_are_lowercase_and_unique(auth: AuthService) -> None:
    user = auth.create_user("  Restricted_User ", PASSWORD, Role.USER)

    problem = raised_problem(lambda: auth.create_user("RESTRICTED_USER", PASSWORD, Role.USER))

    assert user.username == "restricted_user"
    assert (problem.status, problem.code) == (409, "already_exists")


@pytest.mark.parametrize("username", ["", "with space", "slash/name", "x" * 33, "émile"])
def test_refuses_invalid_username(auth: AuthService, username: str) -> None:
    problem = raised_problem(lambda: auth.create_user(username, PASSWORD, Role.USER))

    assert (problem.status, problem.code) == (422, "validation_error")
    assert problem.extra["errors"][0]["field"] == "username"


def test_refuses_short_password(auth: AuthService) -> None:
    problem = raised_problem(lambda: auth.create_user("restricted_user", "short", Role.USER))

    assert problem.extra["errors"][0]["field"] == "password"


def test_stores_only_an_argon2id_hash_of_the_password(auth: AuthService, database: Database) -> None:
    auth.create_user("restricted_user", PASSWORD, Role.USER)

    with database.session() as session:
        password_hash = session.scalars(select(User.password_hash)).one()

    assert password_hash.startswith("$argon2id$")
    assert PASSWORD not in password_hash


# --- Logins

def test_login_returns_token_that_authenticates(auth: AuthService) -> None:
    auth.create_user("restricted_user", PASSWORD, Role.USER)

    new_token = auth.create_token("Restricted_User", PASSWORD, "laptop (photobook 0.1.0)")
    authentication = auth.authenticate(new_token.token)

    assert re.fullmatch(r"pk_[A-Za-z0-9_-]{43}", new_token.token)
    assert new_token.user.username == "restricted_user"
    assert authentication.user == new_token.user
    assert authentication.token_id == new_token.info.id


def test_stores_only_the_tokens_sha256(auth: AuthService, database: Database) -> None:
    auth.create_user("restricted_user", PASSWORD, Role.USER)
    new_token = auth.create_token("restricted_user", PASSWORD, "laptop")

    with database.session() as session:
        stored = session.scalars(select(ApiToken)).one()

    assert len(stored.token_sha256) == 64
    assert new_token.token not in stored.token_sha256


@pytest.mark.parametrize(("username", "password"), [("restricted_user", "wrong password"), ("nobody", PASSWORD)])
def test_wrong_username_or_password_get_the_same_answer(auth: AuthService, username: str, password: str) -> None:
    auth.create_user("restricted_user", PASSWORD, Role.USER)

    problem = raised_problem(lambda: auth.create_token(username, password, "laptop"))

    assert (problem.status, problem.code) == (401, "not_authenticated")
    assert problem.detail == "Wrong username or password, or the token is unknown or revoked."


def test_unknown_token_is_not_authenticated(auth: AuthService) -> None:
    problem = raised_problem(lambda: auth.authenticate("pk_unknown"))

    assert (problem.status, problem.code) == (401, "not_authenticated")


def test_using_a_token_updates_last_used(auth: AuthService, clock: TestClock) -> None:
    auth.create_user("restricted_user", PASSWORD, Role.USER)
    new_token = auth.create_token("restricted_user", PASSWORD, "laptop")

    clock.advance(timedelta(hours=2))
    auth.authenticate(new_token.token)

    assert auth.list_tokens(new_token.user.id)[0].last_used_at == clock.now()


def test_lists_only_own_tokens(auth: AuthService) -> None:
    auth.create_user("restricted_user", PASSWORD, Role.USER)
    auth.create_user("admin_user", PASSWORD, Role.ADMIN)
    laptop = auth.create_token("restricted_user", PASSWORD, "laptop")
    auth.create_token("restricted_user", PASSWORD, "phone")
    auth.create_token("admin_user", PASSWORD, "desktop")

    devices = [token.device_name for token in auth.list_tokens(laptop.user.id)]

    assert devices == ["laptop", "phone"]


def test_revoked_token_no_longer_authenticates(auth: AuthService) -> None:
    auth.create_user("restricted_user", PASSWORD, Role.USER)
    new_token = auth.create_token("restricted_user", PASSWORD, "laptop")

    auth.revoke_token(new_token.user.id, new_token.info.id)

    assert raised_problem(lambda: auth.authenticate(new_token.token)).status == 401


def test_a_login_is_found_by_its_id_while_it_lasts(auth: AuthService, clock: TestClock) -> None:
    auth.create_user("restricted_user", PASSWORD, Role.USER)
    device = auth.create_token("restricted_user", PASSWORD, "laptop")
    web_login = auth.create_token("restricted_user", PASSWORD, "Web admin", lifetime=timedelta(hours=1))

    assert auth.authenticate_token_id(device.info.id).user == device.user
    auth.revoke_token(device.user.id, device.info.id)
    clock.advance(timedelta(hours=1))

    assert raised_problem(lambda: auth.authenticate_token_id(device.info.id)).status == 401  # revoked
    assert raised_problem(lambda: auth.authenticate_token_id(web_login.info.id)).status == 401  # expired
    assert raised_problem(lambda: auth.authenticate_token_id(9999)).status == 401  # never existed


def test_cannot_revoke_another_users_token(auth: AuthService) -> None:
    auth.create_user("restricted_user", PASSWORD, Role.USER)
    admin = auth.create_user("admin_user", PASSWORD, Role.ADMIN)
    restricted_users_token = auth.create_token("restricted_user", PASSWORD, "laptop")

    problem = raised_problem(lambda: auth.revoke_token(admin.id, restricted_users_token.info.id))

    assert (problem.status, problem.code) == (404, "not_found")
    auth.authenticate(restricted_users_token.token)  # still valid


def test_token_with_a_lifetime_expires(auth: AuthService, clock: TestClock) -> None:
    auth.create_user("admin_user", PASSWORD, Role.ADMIN)
    web_login = auth.create_token("admin_user", PASSWORD, "Web admin", lifetime=timedelta(hours=12))
    device = auth.create_token("admin_user", PASSWORD, "laptop")

    clock.advance(timedelta(hours=11, minutes=59))
    auth.authenticate(web_login.token)
    clock.advance(timedelta(minutes=1))

    assert raised_problem(lambda: auth.authenticate(web_login.token)).status == 401
    auth.authenticate(device.token)  # device tokens do not expire
    assert web_login.info.expires_at == datetime(2026, 11, 13, 0, 0) and device.info.expires_at is None


def test_expired_tokens_are_removed_at_the_next_login(auth: AuthService, clock: TestClock) -> None:
    auth.create_user("admin_user", PASSWORD, Role.ADMIN)
    auth.create_token("admin_user", PASSWORD, "Web admin", lifetime=timedelta(hours=12))
    clock.advance(timedelta(hours=13))

    new_login = auth.create_token("admin_user", PASSWORD, "Web admin", lifetime=timedelta(hours=12))

    assert [token.id for token in auth.list_tokens(new_login.user.id)] == [new_login.info.id]


def test_admins_only_login_refuses_other_users(auth: AuthService) -> None:
    auth.create_user("restricted_user", PASSWORD, Role.USER)

    problem = raised_problem(lambda: auth.create_token("restricted_user", PASSWORD, "Web admin", admins_only=True))

    assert (problem.status, problem.code) == (403, "forbidden")
    assert auth.list_tokens(auth.list_users()[0].id) == []


# --- Too many failed logins

def fail_logins(auth: AuthService, count: int) -> None:
    for _ in range(count):
        raised_problem(lambda: auth.create_token("restricted_user", "wrong password", "laptop"))


def test_blocks_logins_after_ten_failures(auth: AuthService) -> None:
    auth.create_user("restricted_user", PASSWORD, Role.USER)
    fail_logins(auth, 10)

    problem = raised_problem(
        lambda: auth.create_token("restricted_user", PASSWORD, "laptop")
    )  # even the right password

    assert (problem.status, problem.code) == (429, "too_many_attempts")
    assert problem.headers == {"Retry-After": "900"}


def test_allows_logins_again_after_fifteen_minutes(auth: AuthService, clock: TestClock) -> None:
    auth.create_user("restricted_user", PASSWORD, Role.USER)
    fail_logins(auth, 10)

    clock.advance(timedelta(minutes=15))

    auth.create_token("restricted_user", PASSWORD, "laptop")


def test_successful_login_resets_the_failure_count(auth: AuthService) -> None:
    auth.create_user("restricted_user", PASSWORD, Role.USER)
    fail_logins(auth, 9)
    auth.create_token("restricted_user", PASSWORD, "laptop")

    fail_logins(auth, 9)

    auth.create_token("restricted_user", PASSWORD, "laptop")  # 18 failures in all, but never 10 in a row


# --- Managing users

def test_lists_users_by_name(auth: AuthService) -> None:
    auth.create_user("restricted_user", PASSWORD, Role.USER)
    auth.create_user("admin_user", PASSWORD, Role.ADMIN)

    assert [user.username for user in auth.list_users()] == ["admin_user", "restricted_user"]


def test_new_password_replaces_the_old_and_logs_out_everywhere(auth: AuthService) -> None:
    restricted_user = auth.create_user("restricted_user", PASSWORD, Role.USER)
    old_token = auth.create_token("restricted_user", PASSWORD, "laptop")

    auth.update_user(restricted_user.id, password="a brand new password")

    assert raised_problem(lambda: auth.authenticate(old_token.token)).status == 401
    assert raised_problem(lambda: auth.create_token("restricted_user", PASSWORD, "laptop")).status == 401
    auth.create_token("restricted_user", "a brand new password", "laptop")


def test_new_users_are_standard_users(auth: AuthService) -> None:
    assert auth.create_user("restricted_user", PASSWORD).role == Role.USER


def test_there_is_only_one_admin_account(auth: AuthService, database: Database) -> None:
    auth.create_user("admin_user", PASSWORD, Role.ADMIN)

    problem = raised_problem(lambda: auth.create_user("second", PASSWORD, Role.ADMIN))

    assert (problem.status, problem.code) == (409, "admin_exists")
    with database.session() as session:  # the database refuses a second one too
        session.add(User(username="second", password_hash="x", role="admin", created_at=datetime(2026, 11, 15)))
        with pytest.raises(IntegrityError):
            session.commit()


def test_renaming_keeps_the_logins(auth: AuthService) -> None:
    restricted_user = auth.create_user("restricted_user", PASSWORD, Role.USER)
    token = auth.create_token("restricted_user", PASSWORD, "laptop")

    renamed = auth.update_user(restricted_user.id, username=" Username ")

    assert renamed.username == "username"
    assert auth.authenticate(token.token).user == renamed
    auth.create_token("username", PASSWORD, "laptop")


@pytest.mark.parametrize("username, code", [("admin_user", "already_exists"), ("with space", "validation_error")])
def test_renaming_refuses_taken_or_invalid_names(auth: AuthService, username: str, code: str) -> None:
    auth.create_user("admin_user", PASSWORD, Role.ADMIN)
    restricted_user = auth.create_user("restricted_user", PASSWORD, Role.USER)

    assert raised_problem(lambda: auth.update_user(restricted_user.id, username=username)).code == code
    assert auth.get_user(restricted_user.id).username == "restricted_user"


def test_deleting_a_user_removes_their_tokens(auth: AuthService) -> None:
    restricted_user = auth.create_user("restricted_user", PASSWORD, Role.USER)
    token = auth.create_token("restricted_user", PASSWORD, "laptop")

    auth.delete_user(restricted_user.id)

    assert auth.list_users() == []
    assert raised_problem(lambda: auth.authenticate(token.token)).status == 401


def test_the_admin_account_cannot_be_deleted(auth: AuthService) -> None:
    admin = auth.create_user("admin_user", PASSWORD, Role.ADMIN)

    problem = raised_problem(lambda: auth.delete_user(admin.id))

    assert (problem.status, problem.code) == (409, "admin_not_deletable")
    assert auth.list_users() == [admin]


def test_finds_a_user_by_id(auth: AuthService) -> None:
    restricted_user = auth.create_user("restricted_user", PASSWORD, Role.USER)

    assert auth.get_user(restricted_user.id) == restricted_user


@pytest.mark.parametrize("action", ["get", "update", "delete"])
def test_unknown_user_is_not_found(auth: AuthService, action: str) -> None:
    def use_unknown_user() -> None:
        if action == "get":
            auth.get_user(999)
        elif action == "update":
            auth.update_user(999, username="restricted_user")
        else:
            auth.delete_user(999)

    problem = raised_problem(use_unknown_user)

    assert (problem.status, problem.code) == (404, "not_found")


def test_new_password_must_be_long_enough(auth: AuthService) -> None:
    restricted_user = auth.create_user("restricted_user", PASSWORD, Role.USER)

    assert raised_problem(lambda: auth.update_user(restricted_user.id, password="short")).code == "validation_error"


def test_changing_your_own_password_keeps_only_the_current_login(auth: AuthService) -> None:
    restricted_user = auth.create_user("restricted_user", PASSWORD, Role.USER)
    laptop = auth.create_token("restricted_user", PASSWORD, "laptop")
    phone = auth.create_token("restricted_user", PASSWORD, "phone")

    auth.change_own_password(restricted_user.id, laptop.info.id, PASSWORD, "a brand new password")

    assert [token.device_name for token in auth.list_tokens(restricted_user.id)] == ["laptop"]
    assert raised_problem(lambda: auth.authenticate(phone.token)).status == 401
    assert raised_problem(
        lambda: auth.change_own_password(restricted_user.id, laptop.info.id, PASSWORD, "x" * 12)
    ).code == (
        "wrong_password"  # the old password is gone
    )
