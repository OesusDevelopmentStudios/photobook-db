# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

"""/admin/users: user management. Accounts, their restricted tags and their devices (logins). New accounts are
standard users; the one admin account can be renamed and given a new password, but not removed."""

from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from photobook_db.admin_web._messages import problem_message
from photobook_db.admin_web._session import AdminForm, AdminPage, AdminSession, PageError
from photobook_db.admin_web._templates import templates
from photobook_db.services.access import AccessService
from photobook_db.services.auth import PASSWORD_MIN_LENGTH, AuthService, Role, UserInfo
from photobook_db.utils.problems import Problem

router = APIRouter(prefix="/users")


@dataclass(frozen=True)
class UserForm:
    """What the form shows: the stored values, or what the admin entered if it could not be saved.
    Passwords are never shown again."""

    username: str = ""
    restricted_tags: str = ""  # one term per line


# --- The list

@router.get("", response_class=HTMLResponse)
def users_page(request: Request, session: AdminPage, search: str = "", deleted: bool = False) -> HTMLResponse:
    access = _access(request)
    wanted = search.strip().lower()  # usernames are lowercase
    users = [user for user in _auth(request).list_users() if wanted in user.username]
    context = {
        "session": session,
        "users": [(user, access.restricted_terms(user.id)) for user in users],
        "search": search,
        "deleted": deleted,
    }
    return templates.TemplateResponse(request, "users.html", context)


# --- New users

@router.get("/new", response_class=HTMLResponse)
def new_user_page(request: Request, session: AdminPage) -> HTMLResponse:
    return _new_user_page(request, session, UserForm())


@router.post("/new", response_class=HTMLResponse)
def create_user(
    request: Request,
    session: AdminForm,
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
    password_repeated: Annotated[str, Form()],
    restricted_tags: Annotated[str, Form()] = "",
) -> Response:
    terms = _terms(restricted_tags)
    try:
        _check_passwords_match(password, password_repeated)
        checked_terms = _access(request).checked_terms(terms)  # before the account is created
        user = _auth(request).create_user(username, password)
        _access(request).set_restricted_terms(user.id, checked_terms)
    except Problem as problem:
        form = UserForm(username, restricted_tags)
        return _new_user_page(request, session, form, _message(problem, terms), problem.status)

    return RedirectResponse(f"/admin/users/{user.id}?saved=true", status_code=303)


# --- One user

@router.get("/{user_id}", response_class=HTMLResponse)
def user_page(request: Request, session: AdminPage, user_id: int, saved: bool = False) -> HTMLResponse:
    user = _existing_user(request, user_id)
    form = UserForm(user.username, "\n".join(_access(request).restricted_terms(user.id)))
    return _user_page(request, session, user, form, saved=saved)


@router.post("/{user_id}", response_class=HTMLResponse)
def update_user(
    request: Request,
    session: AdminForm,
    user_id: int,
    username: Annotated[str, Form()],
    restricted_tags: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
    password_repeated: Annotated[str, Form()] = "",
) -> Response:
    user = _existing_user(request, user_id)
    restricted = user.role == Role.USER  # the admin account has no restricted tags field
    terms = _terms(restricted_tags)
    try:
        _check_passwords_match(password, password_repeated)
        checked_terms = _access(request).checked_terms(terms) if restricted else []  # before anything changes
        # An empty password field keeps the password. A new one logs the user out everywhere, which
        # includes this browser if the admins change their own.
        _auth(request).update_user(user.id, username=username, password=password or None)
        if restricted:
            _access(request).set_restricted_terms(user.id, checked_terms)
    except Problem as problem:
        form = UserForm(username, restricted_tags)
        return _user_page(request, session, user, form, _message(problem, terms), problem.status)

    return RedirectResponse(f"/admin/users/{user.id}?saved=true", status_code=303)


@router.post("/{user_id}/devices/{token_id}/revoke")
def revoke_device(request: Request, _session: AdminForm, user_id: int, token_id: int) -> RedirectResponse:
    try:
        _auth(request).revoke_token(user_id, token_id)
    except Problem as problem:
        raise PageError(problem.status, "This device is no longer logged in.") from None

    return RedirectResponse(f"/admin/users/{user_id}", status_code=303)


@router.get("/{user_id}/delete", response_class=HTMLResponse)
def confirm_deletion(request: Request, session: AdminPage, user_id: int) -> HTMLResponse:
    """A page of its own, so that a user is not removed by a single slip of the mouse."""
    return _delete_page(request, session, _existing_user(request, user_id))


@router.post("/{user_id}/delete", response_class=HTMLResponse)
def delete_user(request: Request, session: AdminForm, user_id: int) -> Response:
    user = _existing_user(request, user_id)
    try:
        _auth(request).delete_user(user.id)
    except Problem as problem:
        return _delete_page(request, session, user, problem_message(problem), problem.status)

    return RedirectResponse("/admin/users?deleted=true", status_code=303)


# --- Helpers

def _auth(request: Request) -> AuthService:
    return request.app.state.auth


def _access(request: Request) -> AccessService:
    return request.app.state.access


def _existing_user(request: Request, user_id: int) -> UserInfo:
    try:
        return _auth(request).get_user(user_id)
    except Problem:
        raise PageError(404, "This user does not exist (any more).") from None


def _terms(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.strip()]


def _check_passwords_match(password: str, password_repeated: str) -> None:
    if password != password_repeated:
        raise Problem(422, "passwords_differ", "The two passwords differ")


def _message(problem: Problem, terms: list[str]) -> str:
    """Names the restricted tag that is wrong. The service numbers the terms, as the REST API needs."""
    for error in problem.extra.get("errors", []):
        list_field, _, index = error["field"].rpartition(".")
        if list_field.endswith("restricted_tags") and index.isdigit():
            return f"Restricted tag “{terms[int(index)].strip()}”: {error['message']}"

    return problem_message(problem)


def _new_user_page(
    request: Request, session: AdminSession, form: UserForm, error: str | None = None, status_code: int = 200
) -> HTMLResponse:
    context = {"session": session, "form": form, "error": error, "password_min_length": PASSWORD_MIN_LENGTH}
    return templates.TemplateResponse(request, "user_new.html", context, status_code=status_code)


def _user_page(
    request: Request,
    session: AdminSession,
    user: UserInfo,
    form: UserForm,
    error: str | None = None,
    status_code: int = 200,
    saved: bool = False,
) -> HTMLResponse:
    context = {
        "session": session,
        "user": user,
        "form": form,
        "devices": _auth(request).list_tokens(user.id),
        "error": error,
        "saved": saved,
        "password_min_length": PASSWORD_MIN_LENGTH,
    }
    return templates.TemplateResponse(request, "user.html", context, status_code=status_code)


def _delete_page(
    request: Request, session: AdminSession, user: UserInfo, error: str | None = None, status_code: int = 200
) -> HTMLResponse:
    context = {"session": session, "user": user, "error": error}
    return templates.TemplateResponse(request, "user_delete.html", context, status_code=status_code)
