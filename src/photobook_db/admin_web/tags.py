# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

"""Changing one tag on every media item, from its namespace's page: renaming it (which also merges it into another tag
or moves it to another namespace) and deleting it. Restricted tags and hidden filters follow; files move in a job
if their folders change. The Tags page's addresses from before 0.2.0 lead to the namespace pages."""

from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from photobook_db.admin_web._messages import problem_message
from photobook_db.admin_web._session import AdminForm, AdminPage, PageError
from photobook_db.admin_web._templates import templates
from photobook_db.admin_web.namespaces import PageNotes, Renaming, tag_path, tags_page, tags_path
from photobook_db.services.jobs import JobInfo
from photobook_db.services.tag_admin import TagAdmin
from photobook_db.services.tags import TagName
from photobook_db.utils.problems import Problem

router = APIRouter(prefix="/namespaces/{namespace}/tags/{name}")
old_urls = APIRouter(prefix="/tags")  # the Tags page before 0.2.0; bookmarks may still lead there


@router.post("/rename", response_class=HTMLResponse)
def rename_tag(
    request: Request,
    session: AdminForm,
    namespace: str,
    name: str,
    new_tag: Annotated[str, Form()],
    search: Annotated[str, Form()] = "",  # the page the row was on, to show it again if the rename is refused
    cursor: Annotated[str, Form()] = "",
) -> Response:
    try:
        new_tag_text = _in_namespace(request, namespace, new_tag)
        renamed, job = _tag_admin(request).rename_tag(TagName(namespace, name), new_tag_text, session.user.id)
    except Problem as problem:
        notes = PageNotes(error=problem_message(problem), status_code=problem.status)
        return tags_page(request, session, namespace, search, cursor or None, notes, renaming=Renaming(name, new_tag))

    # The renamed tag's page, found by its name: it may have moved to another namespace.
    target = f"{tags_path(renamed.namespace)}?search={quote(renamed.name)}&renamed={quote(str(renamed))}"
    return RedirectResponse(_with_job(target, job), status_code=303)


@router.get("/delete", response_class=HTMLResponse)
def confirm_deletion(request: Request, session: AdminPage, namespace: str, name: str) -> HTMLResponse:
    """A page of its own: deleting takes the tag off every media item, and cannot be undone."""
    tag = TagName(namespace, name)
    media_count = request.app.state.tags.media_count(tag)
    if media_count is None:
        raise PageError(404, "This tag does not exist (any more).")

    context = {
        "session": session,
        "tag": tag,
        "tag_path": tag_path(tag),
        "tags_path": tags_path(namespace),
        "image_count": media_count,
        "fixed": namespace in request.app.state.tags.fixed_values(),  # a value, e.g. a rating
    }
    return templates.TemplateResponse(request, "tag_delete.html", context)


@router.post("/delete")
def delete_tag(request: Request, session: AdminForm, namespace: str, name: str) -> RedirectResponse:
    try:
        job = _tag_admin(request).delete_tag(TagName(namespace, name), session.user.id)
    except Problem as problem:
        if problem.status == 404:
            raise PageError(404, "This tag does not exist (any more).") from None

        raise PageError(problem.status, problem_message(problem)) from None  # e.g. a tag the server sets

    return RedirectResponse(_with_job(f"{tags_path(namespace)}?deleted=true", job), status_code=303)


# --- The old addresses

@old_urls.get("")
def moved_tags_page() -> RedirectResponse:
    return RedirectResponse("/admin/namespaces", status_code=301)


@old_urls.get("/{namespace}/{name}")
def moved_tag_page(namespace: str, name: str) -> RedirectResponse:
    """The tag on its namespace's page, found by its name."""
    return RedirectResponse(f"{tags_path(namespace)}?search={quote(name)}", status_code=301)


@old_urls.get("/{namespace}/{name}/delete")
def moved_deletion(namespace: str, name: str) -> RedirectResponse:
    return RedirectResponse(f"{tag_path(TagName(namespace, name))}/delete", status_code=301)


def _in_namespace(request: Request, namespace: str, typed: str) -> str:
    """The field holds the name only: it stays in the page's namespace unless it starts with another namespace,
    e.g. "album:summer", which moves the tag there. Like any typed tag, "re:zero" is a name if "re" is no
    namespace."""
    prefix, separator, _name = typed.strip().partition(":")
    if separator and prefix.strip().lower() in request.app.state.tags.namespace_names():
        return typed

    return f"{namespace}:{typed}"


def _tag_admin(request: Request) -> TagAdmin:
    return request.app.state.tag_admin


def _with_job(target: str, job: JobInfo | None) -> str:
    """The page after a change links to the job that moves the files, if there is one."""
    return target if job is None else f"{target}&relayout_job={job.id}"
