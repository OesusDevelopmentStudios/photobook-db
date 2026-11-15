# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

"""/admin/jobs: background jobs and their progress, and starting a folder import. Pages of unfinished jobs
reload themselves, so progress shows without JavaScript."""

from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from pictorio_db.admin_web._messages import problem_message
from pictorio_db.admin_web._session import AdminForm, AdminPage, AdminSession, PageError
from pictorio_db.admin_web._templates import templates
from pictorio_db.services.jobs import FolderImport, JobInfo, JobStatus
from pictorio_db.utils.problems import Problem

router = APIRouter(prefix="/jobs")

REFRESH_SECONDS = 2  # the worker writes progress about once a second
UNFINISHED = (JobStatus.QUEUED, JobStatus.RUNNING)


@router.get("", response_class=HTMLResponse)
def jobs_page(request: Request, session: AdminPage) -> HTMLResponse:
    jobs = request.app.state.jobs.list_jobs(session.user)
    context = {
        "session": session,
        "jobs": jobs,
        "usernames": _usernames(request),
        "import_dir": request.app.state.server_settings.current().import_dir,
        "refresh_seconds": REFRESH_SECONDS if any(job.status in UNFINISHED for job in jobs) else None,
    }
    return templates.TemplateResponse(request, "jobs.html", context)


# --- Folder imports

@router.get("/import", response_class=HTMLResponse)
def import_page(request: Request, session: AdminPage) -> HTMLResponse:
    return _import_page(request, session)


@router.post("/import", response_class=HTMLResponse)
def start_import(
    request: Request,
    session: AdminForm,
    source_subdir: Annotated[str, Form()] = "",
    tags: Annotated[str, Form()] = "",
    remove_originals: Annotated[bool, Form()] = False,  # an unticked checkbox is not sent at all
) -> Response:
    tag_list = [line.strip() for line in tags.splitlines() if line.strip()]
    try:
        job = _folder_import(request).start(source_subdir.strip(), remove_originals, tag_list, session.user.id)
    except Problem as problem:
        return _import_page(
            request, session, source_subdir, tags, remove_originals, problem_message(problem), problem.status
        )

    return RedirectResponse(f"/admin/jobs/{job.id}", status_code=303)


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


def _folder_import(request: Request) -> FolderImport:
    return request.app.state.folder_import


def _usernames(request: Request) -> dict[int, str]:
    return {user.id: user.username for user in request.app.state.auth.list_users()}


def _import_page(
    request: Request,
    session: AdminSession,
    source_subdir: str = "",
    tags: str = "",
    remove_originals: bool = False,
    error: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    context = {
        "session": session,
        "import_dir": request.app.state.server_settings.current().import_dir,
        "source_folders": _folder_import(request).source_folders(),
        "source_subdir": source_subdir,
        "tags": tags,
        "remove_originals": remove_originals,
        "error": error,
    }
    return templates.TemplateResponse(request, "job_import.html", context, status_code=status_code)
