# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

"""/admin/settings: the path template, the upload limit, the rating values and the accent colour of these pages."""

from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from photobook_db.admin_web._messages import problem_message
from photobook_db.admin_web._session import AdminForm, AdminPage, AdminSession
from photobook_db.admin_web._templates import templates
from photobook_db.admin_web._theme import PLASMA_ACCENT_COLORS
from photobook_db.services.importer import UPLOAD_LIMIT_RANGE
from photobook_db.services.server_settings import ServerSettings, SettingsChanges
from photobook_db.services.tags import MAX_RATING_VALUES
from photobook_db.utils.problems import Problem

router = APIRouter()

MIB = 1024 * 1024


@dataclass(frozen=True)
class SettingsForm:
    """The values in the form: the stored ones, or what the admin entered if it could not be saved."""

    path_template: str
    max_upload_mib: int
    rating_values: str  # one per line


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
    rating_values: Annotated[str, Form()],
) -> Response:
    server_settings = _server_settings(request)
    unchanged_mib = server_settings.current().max_upload_bytes // MIB
    changes = SettingsChanges(
        path_template=path_template,
        # The form shows whole MiB, so an untouched field keeps a limit the API set in bytes.
        max_upload_bytes=None if max_upload_mib == unchanged_mib else max_upload_mib * MIB,
        rating_values=[line for line in rating_values.splitlines() if line.strip()],
    )
    try:
        _settings, relayout_job = server_settings.change(changes, session.user.id)
    except Problem as problem:
        form = SettingsForm(path_template, max_upload_mib, rating_values)
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


def _server_settings(request: Request) -> ServerSettings:
    return request.app.state.server_settings


def _stored_form(request: Request) -> SettingsForm:
    settings = _server_settings(request).current()
    return SettingsForm(settings.path_template, settings.max_upload_bytes // MIB, "\n".join(settings.rating_values))


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
        "max_rating_values": MAX_RATING_VALUES,
        "accent_error": accent_error,
        "accent_saved": accent_saved,
        "plasma_accent_colors": PLASMA_ACCENT_COLORS,
        "is_custom_accent": settings.web_accent_color not in dict(PLASMA_ACCENT_COLORS).values(),
    }
    return templates.TemplateResponse(request, "settings.html", context, status_code=status_code)
