# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

"""/admin/import: background jobs and their progress, and starting a folder import. Pages of unfinished jobs
reload themselves, so progress shows without JavaScript."""

import re
from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from photobook_db.admin_web._messages import problem_message
from photobook_db.admin_web._session import AdminForm, AdminPage, AdminSession, PageError
from photobook_db.admin_web._templates import templates
from photobook_db.services.jobs import SUCCESSFUL_JOB_LIFETIME, FolderImport, JobInfo, JobStatus
from photobook_db.services.tags import NamespaceInfo, TagsService
from photobook_db.utils.problems import Problem

router = APIRouter(prefix="/import")
old_urls = APIRouter(prefix="/jobs")  # where these pages were before 0.2.0; bookmarks may still lead there

REFRESH_SECONDS = 2  # the worker writes progress about once a second
UNFINISHED = (JobStatus.QUEUED, JobStatus.RUNNING)
TAG_FIELD_PREFIX = "tag_"  # the import form has a field per namespace: tag_author, tag_rating…
SUGGESTIONS = 100  # values offered for a one-tag-per-image namespace


@dataclass(frozen=True)
class TagField:
    """One namespace's field in the import form."""

    namespace: str
    kind: str  # "choice": fixed values (ratings); "single": one tag, with suggestions; "several": comma-separated
    value: str
    options: list[str]


@router.get("", response_class=HTMLResponse)
def jobs_page(request: Request, session: AdminPage, removed: int | None = None) -> HTMLResponse:
    jobs = request.app.state.jobs.list_jobs(session.user)
    context = {
        "session": session,
        "jobs": jobs,
        "usernames": _usernames(request),
        "import_dir": request.app.state.server_settings.current().import_dir,
        "refresh_seconds": REFRESH_SECONDS if any(job.status in UNFINISHED for job in jobs) else None,
        "has_finished_jobs": any(job.status not in UNFINISHED for job in jobs),
        "lifetime_hours": int(SUCCESSFUL_JOB_LIFETIME.total_seconds() // 3600),
        "removed": removed,
    }
    return templates.TemplateResponse(request, "jobs.html", context)


@router.post("/remove-finished")
def remove_finished_jobs(request: Request, _session: AdminForm) -> RedirectResponse:
    removed = request.app.state.jobs.remove_finished_jobs()
    return RedirectResponse(f"/admin/import?removed={removed}", status_code=303)


# --- Folder imports

@router.get("/new", response_class=HTMLResponse)
def import_page(request: Request, session: AdminPage) -> HTMLResponse:
    return _import_page(request, session)


async def _entered_tags(request: Request) -> dict[str, str]:
    """The import form's tag fields, by namespace. Read here because the fields depend on the namespaces."""
    form = await request.form()
    fields = [(key, str(value)) for key, value in form.items() if key.startswith(TAG_FIELD_PREFIX)]
    return {key.removeprefix(TAG_FIELD_PREFIX): value for key, value in fields}


@router.post("/new", response_class=HTMLResponse)
def start_import(
    request: Request,
    session: AdminForm,
    entered_tags: Annotated[dict[str, str], Depends(_entered_tags)],
    source_subdir: Annotated[str, Form()] = "",
    remove_originals: Annotated[bool, Form()] = False,  # an unticked checkbox is not sent at all
    action: Annotated[str, Form()] = "start",
) -> Response:
    if action == "rescan":  # list the folders again, e.g. after copying one in; keep what was entered
        return _import_page(request, session, source_subdir, entered_tags, remove_originals)

    try:
        tags = _tag_texts(entered_tags, _tags(request).list_namespaces())
        job = _folder_import(request).start(source_subdir.strip(), remove_originals, tags, session.user.id)
    except Problem as problem:
        return _import_page(
            request, session, source_subdir, entered_tags, remove_originals, problem_message(problem), problem.status
        )

    return RedirectResponse(f"/admin/import/{job.id}", status_code=303)


# --- One job

@router.get("/{job_id}", response_class=HTMLResponse)
def job_page(request: Request, session: AdminPage, job_id: int) -> HTMLResponse:
    try:
        job: JobInfo = request.app.state.jobs.get_job(job_id, session.user)
    except Problem:
        raise PageError(404, "This job does not exist.") from None

    context = {
        "session": session,
        "job": job,
        "usernames": _usernames(request),
        "refresh_seconds": REFRESH_SECONDS if job.status in UNFINISHED else None,
    }
    return templates.TemplateResponse(request, "job.html", context)


# --- The old addresses

@old_urls.get("")
@old_urls.get("/{rest:path}")
def moved_to_import(rest: str = "") -> RedirectResponse:
    """/admin/jobs → /admin/import, /admin/jobs/import → /admin/import/new, /admin/jobs/7 → /admin/import/7.
    The target always stays under /admin/import, whatever `rest` holds."""
    target = "/admin/import/new" if rest == "import" else f"/admin/import/{rest}".removesuffix("/")
    return RedirectResponse(target, status_code=301)


def _folder_import(request: Request) -> FolderImport:
    return request.app.state.folder_import


def _tags(request: Request) -> TagsService:
    return request.app.state.tags


def _tag_texts(entered: dict[str, str], namespaces: list[NamespaceInfo]) -> list[str]:
    """"sample_artist, other_artist" in the author field → author:sample_artist, author:other_artist. Commas and
    line breaks can be no part of a tag, so they separate tags; a one-tag-per-image field takes its text whole."""
    texts = []
    for namespace in namespaces:
        if not namespace.user_editable:
            continue  # the form has no field for it

        text = entered.get(namespace.name, "")
        if namespace.single_value and "," in text:
            raise Problem(
                422, "validation_error", "Invalid tag", f"An image has one {namespace.name} tag, so give one."
            )

        names = [text] if namespace.single_value else re.split(r"[,\n]", text)
        texts += [f"{namespace.name}:{name.strip()}" for name in names if name.strip()]

    return texts


def _tag_fields(request: Request, entered: dict[str, str]) -> list[TagField]:
    tags = _tags(request)
    fields = []
    for namespace in tags.list_namespaces():
        if not namespace.user_editable:
            continue  # the server sets those tags itself

        value = entered.get(namespace.name, "")
        if namespace.allowed_values is not None:  # fixed values, e.g. the ratings
            fields.append(TagField(namespace.name, "choice", value, list(namespace.allowed_values)))
        elif namespace.single_value:
            known = [match.tag.name for match in tags.autocomplete(namespace=namespace.name, limit=SUGGESTIONS)]
            fields.append(TagField(namespace.name, "single", value, known))
        else:
            fields.append(TagField(namespace.name, "several", value, []))

    return fields


def _usernames(request: Request) -> dict[int, str]:
    return {user.id: user.username for user in request.app.state.auth.list_users()}


def _import_page(
    request: Request,
    session: AdminSession,
    source_subdir: str = "",
    entered_tags: dict[str, str] | None = None,
    remove_originals: bool = False,
    error: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    context = {
        "session": session,
        "import_dir": request.app.state.server_settings.current().import_dir,
        "source_folders": _folder_import(request).source_folders(),
        "source_subdir": source_subdir,
        "tag_fields": _tag_fields(request, entered_tags or {}),
        "tag_field_prefix": TAG_FIELD_PREFIX,
        "remove_originals": remove_originals,
        "error": error,
    }
    return templates.TemplateResponse(request, "job_import.html", context, status_code=status_code)
