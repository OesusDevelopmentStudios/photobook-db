# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

"""/admin/namespaces: the part before ':' in a tag, e.g. 'author' in 'author:sample_artist', and a page per
namespace with its tags."""

from dataclasses import dataclass
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from photobook_db.admin_web._messages import problem_message
from photobook_db.admin_web._session import AdminForm, AdminPage, AdminSession, PageError
from photobook_db.admin_web._templates import templates
from photobook_db.services.tag_admin import TagAdmin
from photobook_db.services.tags import NamespaceInfo, TagName, TagsService
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


@router.get("/{name}/delete", response_class=HTMLResponse)
def confirm_deletion(request: Request, session: AdminPage, name: str) -> HTMLResponse:
    """A page of its own: the namespace's tags go with it, and this cannot be undone."""
    try:
        _tags(request).check_namespace_removable(name)
    except Problem as problem:
        return _list_page(request, session, error=problem_message(problem), status_code=problem.status)

    tag_count, media_count = _tags(request).namespace_usage(name)
    context = {"session": session, "name": name, "tag_count": tag_count, "media_count": media_count}
    return templates.TemplateResponse(request, "namespace_delete.html", context)


@router.post("/{name}/delete", response_class=HTMLResponse)
def delete_namespace(request: Request, session: AdminForm, name: str) -> Response:
    try:
        _tag_admin(request).delete_namespace(name)
    except Problem as problem:
        return _list_page(request, session, error=problem_message(problem), status_code=problem.status)

    return RedirectResponse("/admin/namespaces?deleted=true", status_code=303)


# --- One namespace and its tags

@dataclass(frozen=True)
class PageNotes:
    """What a namespace page says above its tags: how the last change went, or why it was refused."""

    error: str | None = None
    status_code: int = 200
    added: str | None = None  # the name of a tag just added
    renamed: str | None = None  # the new tag, e.g. "author:sample_artist"
    deleted: bool = False
    relayout_job: int | None = None  # moves the files a change concerns


@dataclass(frozen=True)
class Renaming:
    """A rename that was refused: its row stays open with what was typed."""

    tag_name: str
    new_tag: str


@router.get("/{name}/tags", response_class=HTMLResponse)
def namespace_tags_page(
    request: Request,
    session: AdminPage,
    name: str,
    search: str = "",
    cursor: str | None = None,
    added: str | None = None,
    renamed: str | None = None,
    deleted: bool = False,
    relayout_job: int | None = None,
) -> HTMLResponse:
    notes = PageNotes(added=added, renamed=renamed, deleted=deleted, relayout_job=relayout_job)
    return tags_page(request, session, name, search, cursor, notes)


@router.post("/{name}/tags", response_class=HTMLResponse)
def add_tag(request: Request, session: AdminForm, name: str, tag_name: Annotated[str, Form()]) -> Response:
    """A tag no media item has yet, so that it is suggested to users."""
    try:
        tag = _tag_admin(request).create_tag(f"{_namespace(request, name).name}:{tag_name}")
    except Problem as problem:
        notes = PageNotes(error=problem_message(problem), status_code=problem.status)
        return tags_page(request, session, name, notes=notes, new_tag_name=tag_name)

    return RedirectResponse(f"{tags_path(tag.namespace)}?added={quote(tag.name)}", status_code=303)


def tags_path(namespace_name: str) -> str:
    """A namespace's page with its tags."""
    return f"/admin/namespaces/{quote(namespace_name, safe='')}/tags"


def tag_path(tag: TagName) -> str:
    """Where a tag's actions are, e.g. its /delete."""
    return f"{tags_path(tag.namespace)}/{quote(tag.name, safe='')}"


def tags_page(
    request: Request,
    session: AdminSession,
    name: str,
    search: str = "",
    cursor: str | None = None,
    notes: PageNotes = PageNotes(),
    new_tag_name: str = "",
    renaming: Renaming | None = None,
) -> HTMLResponse:
    namespace = _namespace(request, name)
    try:
        page = _tags(request).tag_page(search.strip(), namespace.name, PAGE_SIZE, cursor=cursor)
    except Problem:
        raise PageError(404, "This page of tags does not exist (any more).") from None

    context = {
        "session": session,
        "namespace": namespace,
        "tags": [(match, tag_path(match.tag)) for match in page.items],
        "tags_path": tags_path(namespace.name),
        "next_cursor": page.next_cursor,
        "cursor": cursor,
        "search": search,
        "notes": notes,
        "new_tag_name": new_tag_name,
        "renaming": renaming,
    }
    return templates.TemplateResponse(request, "namespace.html", context, status_code=notes.status_code)


def _tags(request: Request) -> TagsService:
    return request.app.state.tags


def _tag_admin(request: Request) -> TagAdmin:
    return request.app.state.tag_admin


def _namespace(request: Request, name: str) -> NamespaceInfo:
    namespace = next((info for info in _tags(request).list_namespaces() if info.name == name), None)
    if namespace is None:
        raise PageError(404, "This namespace does not exist.")

    return namespace


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
