# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

"""/admin/namespaces: the part before ':' in a tag, e.g. 'author' in 'author:sample_artist'."""

from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from pictorio_db.admin_web._messages import problem_message
from pictorio_db.admin_web._session import AdminForm, AdminPage, AdminSession
from pictorio_db.admin_web._templates import templates
from pictorio_db.services.tags import TagsService
from pictorio_db.utils.problems import Problem

router = APIRouter(prefix="/namespaces")


@router.get("", response_class=HTMLResponse)
def namespaces_page(
    request: Request, session: AdminPage, created: bool = False, deleted: bool = False
) -> HTMLResponse:
    return _list_page(request, session, created=created, deleted=deleted)


@router.get("/new", response_class=HTMLResponse)
def new_namespace_page(request: Request, session: AdminPage) -> HTMLResponse:
    return _new_namespace_page(request, session)


@router.post("/new", response_class=HTMLResponse)
def create_namespace(
    request: Request,
    session: AdminForm,
    name: Annotated[str, Form()],
    single_value: Annotated[bool, Form()] = False,  # an unticked checkbox is not sent at all
) -> Response:
    try:
        _tags(request).create_namespace(name, single_value)
    except Problem as problem:
        return _new_namespace_page(request, session, name, single_value, problem_message(problem), problem.status)

    return RedirectResponse("/admin/namespaces?created=true", status_code=303)


@router.post("/{name}/delete", response_class=HTMLResponse)
def delete_namespace(request: Request, session: AdminForm, name: str) -> Response:
    """Only unused custom namespaces can go, so nothing is lost and no confirmation is needed."""
    try:
        _tags(request).delete_namespace(name)
    except Problem as problem:
        return _list_page(request, session, error=problem_message(problem), status_code=problem.status)

    return RedirectResponse("/admin/namespaces?deleted=true", status_code=303)


def _tags(request: Request) -> TagsService:
    return request.app.state.tags


def _list_page(
    request: Request,
    session: AdminSession,
    error: str | None = None,
    status_code: int = 200,
    created: bool = False,
    deleted: bool = False,
) -> HTMLResponse:
    context = {
        "session": session,
        "namespaces": _tags(request).list_namespaces(),
        "error": error,
        "created": created,
        "deleted": deleted,
    }
    return templates.TemplateResponse(request, "namespaces.html", context, status_code=status_code)


def _new_namespace_page(
    request: Request,
    session: AdminSession,
    name: str = "",
    single_value: bool = False,
    error: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    context = {"session": session, "name": name, "single_value": single_value, "error": error}
    return templates.TemplateResponse(request, "namespace_new.html", context, status_code=status_code)
