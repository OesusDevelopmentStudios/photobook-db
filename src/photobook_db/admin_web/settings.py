# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

"""/admin/settings: the path template, the upload limits and the accent colour of these pages. Rating values are
managed on the rating namespace's page."""

from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from photobook_db.admin_web._messages import problem_message
from photobook_db.admin_web._session import AdminForm, AdminPage, AdminSession
from photobook_db.admin_web._templates import templates
from photobook_db.admin_web._theme import PLASMA_ACCENT_COLORS
from photobook_db.services.importer import UPLOAD_LIMIT_RANGE, VIDEO_UPLOAD_LIMIT_RANGE
from photobook_db.services.server_settings import ServerSettings, SettingsChanges
from photobook_db.utils.problems import Problem

router = APIRouter()

MIB = 1024 * 1024


@dataclass(frozen=True)
class SettingsForm:
    """The values in the form: the stored ones, or what the admin entered if it could not be saved."""

    path_template: str
    max_upload_mib: int
    max_video_upload_mib: int


@router.get("/settings", response_class=HTMLResponse)
def settings_page(
    request: Request,
    session: AdminPage,
    saved: bool = False,
    relayout_job: int | None = None,
    accent_saved: bool = False,
) -> HTMLResponse:
    return _page(request, session, _stored_form(request), saved=saved, relayout_job=relayout_job,
                 accent_saved=accent_saved)


@router.post("/settings", response_class=HTMLResponse)
def save_settings(
    request: Request,
    session: AdminForm,
    path_template: Annotated[str, Form()],
    max_upload_mib: Annotated[int, Form()],
    max_video_upload_mib: Annotated[int, Form()],
) -> Response:
    server_settings = _server_settings(request)
    stored = server_settings.current()
    changes = SettingsChanges(
        path_template=path_template,
        max_upload_bytes=_changed_limit(max_upload_mib, stored.max_upload_bytes),
        max_video_upload_bytes=_changed_limit(max_video_upload_mib, stored.max_video_upload_bytes),
    )
    try:
        _settings, relayout_job = server_settings.change(changes, session.user.id)
    except Problem as problem:
        form = SettingsForm(path_template, max_upload_mib, max_video_upload_mib)
        return _page(request, session, form, error=problem_message(problem), status_code=problem.status)

    # Back to the page with a GET, so reloading it does not send the form again.
    target = "/admin/settings?saved=true"
    if relayout_job is not None:
        target += f"&relayout_job={relayout_job.id}"

    return RedirectResponse(target, status_code=303)


@router.post("/settings/accent", response_class=HTMLResponse)
def save_accent_color(
    request: Request,
    session: AdminForm,
    accent_color: Annotated[str, Form()],
    custom_color: Annotated[str, Form()] = "",
) -> Response:
    """A swatch sends its colour; the custom colour's button sends "custom" and the colour field beside it."""
    color = custom_color if accent_color == "custom" else accent_color
    try:
        _server_settings(request).change(SettingsChanges(web_accent_color=color), session.user.id)
    except Problem as problem:
        return _page(request, session, _stored_form(request), accent_error=problem_message(problem),
                     status_code=problem.status)

    return RedirectResponse("/admin/settings?accent_saved=true#appearance", status_code=303)


def _changed_limit(entered_mib: int, stored_bytes: int) -> int | None:
    """The form shows whole MiB, so an untouched field keeps a limit the API set in bytes."""
    return None if entered_mib == stored_bytes // MIB else entered_mib * MIB


def _server_settings(request: Request) -> ServerSettings:
    return request.app.state.server_settings


def _stored_form(request: Request) -> SettingsForm:
    settings = _server_settings(request).current()
    return SettingsForm(
        settings.path_template, settings.max_upload_bytes // MIB, settings.max_video_upload_bytes // MIB
    )


def _page(
    request: Request,
    session: AdminSession,
    form: SettingsForm,
    error: str | None = None,
    saved: bool = False,
    relayout_job: int | None = None,
    accent_error: str | None = None,
    accent_saved: bool = False,
    status_code: int = 200,
) -> HTMLResponse:
    settings = _server_settings(request).current()
    context = {
        "session": session,
        "form": form,
        "error": error,
        "saved": saved,
        "relayout_job": relayout_job,
        "library_dir": settings.library_dir,
        "import_dir": settings.import_dir,
        "upload_limit_mib": (UPLOAD_LIMIT_RANGE[0] // MIB, UPLOAD_LIMIT_RANGE[1] // MIB),
        "video_upload_limit_mib": (VIDEO_UPLOAD_LIMIT_RANGE[0] // MIB, VIDEO_UPLOAD_LIMIT_RANGE[1] // MIB),
        "accent_error": accent_error,
        "accent_saved": accent_saved,
        "plasma_accent_colors": PLASMA_ACCENT_COLORS,
        "is_custom_accent": settings.web_accent_color not in dict(PLASMA_ACCENT_COLORS).values(),
    }
    return templates.TemplateResponse(request, "settings.html", context, status_code=status_code)
