# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

"""/admin/tags: find tags, and rename, merge or delete them on every image. Restricted tags and hidden filters
follow the change; files move in a job if their folders change."""

from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from photobook_db.admin_web._messages import problem_message
from photobook_db.admin_web._session import AdminForm, AdminPage, AdminSession, PageError
from photobook_db.admin_web._templates import templates
from photobook_db.services.jobs import JobInfo
from photobook_db.services.tag_admin import TagAdmin
from photobook_db.services.tags import TagName, TagsService
from photobook_db.utils.problems import Problem

router = APIRouter(prefix="/tags")

LIST_LIMIT = 100


@router.get("", response_class=HTMLResponse)
def tags_page(
    request: Request,
    session: AdminPage,
    search: str = "",
    namespace: str = "",
    deleted: bool = False,
    relayout_job: int | None = None,
) -> HTMLResponse:
    tags = _tags(request)
    context = {
        "session": session,
        "matches": [
            (match, tag_page_path(match.tag))
            for match in tags.autocomplete(search.strip(), namespace or None, LIST_LIMIT)
        ],
        "namespaces": [info.name for info in tags.list_namespaces()],
        "search": search,
        "namespace": namespace,
        "list_limit": LIST_LIMIT,
        "deleted": deleted,
        "relayout_job": relayout_job,
    }
    return templates.TemplateResponse(request, "tags.html", context)


# --- One tag

@router.get("/{namespace}/{name}", response_class=HTMLResponse)
def tag_page(
    request: Request,
    session: AdminPage,
    namespace: str,
    name: str,
    renamed: bool = False,
    relayout_job: int | None = None,
) -> HTMLResponse:
    tag = TagName(namespace, name)
    return _tag_page(request, session, tag, str(tag), renamed=renamed, relayout_job=relayout_job)


@router.post("/{namespace}/{name}/rename", response_class=HTMLResponse)
def rename_tag(
    request: Request, session: AdminForm, namespace: str, name: str, new_tag: Annotated[str, Form()]
) -> Response:
    tag = TagName(namespace, name)
    try:
        renamed, job = _tag_admin(request).rename_tag(tag, new_tag, session.user.id)
    except Problem as problem:
        return _tag_page(request, session, tag, new_tag, problem_message(problem), problem.status)

    return RedirectResponse(_with_job(f"{tag_page_path(renamed)}?renamed=true", job), status_code=303)


@router.get("/{namespace}/{name}/delete", response_class=HTMLResponse)
def confirm_deletion(request: Request, session: AdminPage, namespace: str, name: str) -> HTMLResponse:
    """A page of its own: deleting takes the tag off every image, and cannot be undone."""
    tag = TagName(namespace, name)
    context = {
        "session": session,
        "tag": tag,
        "tag_path": tag_page_path(tag),
        "image_count": _existing_image_count(request, tag),
    }
    return templates.TemplateResponse(request, "tag_delete.html", context)


@router.post("/{namespace}/{name}/delete")
def delete_tag(request: Request, session: AdminForm, namespace: str, name: str) -> RedirectResponse:
    try:
        job = _tag_admin(request).delete_tag(TagName(namespace, name), session.user.id)
    except Problem as problem:
        if problem.status == 404:
            raise PageError(404, "This tag does not exist (any more).") from None

        raise PageError(problem.status, problem_message(problem)) from None  # e.g. a tag the server sets

    return RedirectResponse(_with_job("/admin/tags?deleted=true", job), status_code=303)


# --- Helpers

def _tags(request: Request) -> TagsService:
    return request.app.state.tags


def _tag_admin(request: Request) -> TagAdmin:
    return request.app.state.tag_admin


def _existing_image_count(request: Request, tag: TagName) -> int:
    image_count = _tags(request).image_count(tag)
    if image_count is None:
        raise PageError(404, "This tag does not exist (any more).")

    return image_count


def tag_page_path(tag: TagName) -> str:
    """Its page, where it is renamed or deleted."""
    return f"/admin/tags/{quote(tag.namespace, safe='')}/{quote(tag.name, safe='')}"


def _with_job(target: str, job: JobInfo | None) -> str:
    """The page after a change links to the job that moves the files, if there is one."""
    return target if job is None else f"{target}&relayout_job={job.id}"


def _tag_page(
    request: Request,
    session: AdminSession,
    tag: TagName,
    new_tag: str,
    error: str | None = None,
    status_code: int = 200,
    renamed: bool = False,
    relayout_job: int | None = None,
) -> HTMLResponse:
    context = {
        "session": session,
        "tag": tag,
        "tag_path": tag_page_path(tag),
        "image_count": _existing_image_count(request, tag),
        "read_only": tag.namespace in _tags(request).read_only_namespace_names(),
        "new_tag": new_tag,
        "error": error,
        "renamed": renamed,
        "relayout_job": relayout_job,
    }
    return templates.TemplateResponse(request, "tag.html", context, status_code=status_code)
