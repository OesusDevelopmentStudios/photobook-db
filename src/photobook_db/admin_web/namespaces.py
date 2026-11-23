# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

"""/admin/namespaces: the part before ':' in a tag, e.g. 'author' in 'author:sample_artist', and a page per
namespace with its tags."""

from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from photobook_db.admin_web._messages import problem_message
from photobook_db.admin_web._session import AdminForm, AdminPage, AdminSession, PageError
from photobook_db.admin_web._templates import templates
from photobook_db.admin_web.tags import tag_page_path
from photobook_db.services.tag_admin import TagAdmin
from photobook_db.services.tags import NamespaceInfo, TagsService
from photobook_db.utils.problems import Problem

router = APIRouter(prefix="/namespaces")

PAGE_SIZE = 100


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


# --- One namespace and its tags

@router.get("/{name}/tags", response_class=HTMLResponse)
def namespace_tags_page(
    request: Request,
    session: AdminPage,
    name: str,
    search: str = "",
    cursor: str | None = None,
    added: str | None = None,
    deleted: bool = False,
) -> HTMLResponse:
    return _tags_page(request, session, name, search, cursor, added=added, deleted=deleted)


@router.post("/{name}/tags", response_class=HTMLResponse)
def add_tag(request: Request, session: AdminForm, name: str, tag_name: Annotated[str, Form()]) -> Response:
    """A tag no image has yet, so that it is suggested to users."""
    try:
        tag = _tag_admin(request).create_tag(f"{_namespace(request, name).name}:{tag_name}")
    except Problem as problem:
        return _tags_page(request, session, name, error=problem_message(problem), status_code=problem.status,
                          new_tag_name=tag_name)

    return RedirectResponse(f"/admin/namespaces/{tag.namespace}/tags?added={quote(tag.name)}", status_code=303)


def _tags(request: Request) -> TagsService:
    return request.app.state.tags


def _tag_admin(request: Request) -> TagAdmin:
    return request.app.state.tag_admin


def _namespace(request: Request, name: str) -> NamespaceInfo:
    namespace = next((info for info in _tags(request).list_namespaces() if info.name == name), None)
    if namespace is None:
        raise PageError(404, "This namespace does not exist.")

    return namespace


def _tags_page(
    request: Request,
    session: AdminSession,
    name: str,
    search: str = "",
    cursor: str | None = None,
    error: str | None = None,
    status_code: int = 200,
    added: str | None = None,
    deleted: bool = False,
    new_tag_name: str = "",
) -> HTMLResponse:
    namespace = _namespace(request, name)
    try:
        page = _tags(request).tag_page(search.strip(), namespace.name, PAGE_SIZE, cursor=cursor)
    except Problem:
        raise PageError(404, "This page of tags does not exist (any more).") from None

    context = {
        "session": session,
        "namespace": namespace,
        "tags": [(match, tag_page_path(match.tag)) for match in page.items],
        "next_cursor": page.next_cursor,
        "first_page": cursor is None,
        "search": search,
        "error": error,
        "added": added,
        "deleted": deleted,
        "new_tag_name": new_tag_name,
    }
    return templates.TemplateResponse(request, "namespace.html", context, status_code=status_code)


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
