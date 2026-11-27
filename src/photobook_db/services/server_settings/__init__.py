# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

"""The server's settings as the admin sees them: the API and the web admin pages read and change them here."""

import re
from dataclasses import dataclass
from pathlib import Path

from photobook_db.services.importer import ImporterService, check_upload_limit, check_video_upload_limit
from photobook_db.services.jobs import JobInfo, Relayout
from photobook_db.services.settings import SettingsService
from photobook_db.services.storage import StorageService
from photobook_db.utils.problems import Problem

WEB_ACCENT_COLOR_SETTING = "web_accent_color"
DEFAULT_WEB_ACCENT_COLOR = "#3daee9"  # the Plasma default, so the pages match a stock KDE desktop
_COLOR_PATTERN = re.compile("#[0-9a-f]{6}")


@dataclass(frozen=True)
class ServerSettingsInfo:
    path_template: str
    max_upload_bytes: int  # for images
    max_video_upload_bytes: int
    web_accent_color: str  # "#rrggbb"
    library_dir: Path  # from the environment; read-only here
    import_dir: Path | None


@dataclass(frozen=True)
class SettingsChanges:
    """None leaves a setting as it is."""

    path_template: str | None = None
    max_upload_bytes: int | None = None
    max_video_upload_bytes: int | None = None
    web_accent_color: str | None = None


class ServerSettings:
    def __init__(
        self,
        storage: StorageService,
        importer: ImporterService,
        relayout: Relayout,
        settings: SettingsService,
        import_dir: Path | None,
    ) -> None:
        self._storage = storage
        self._importer = importer
        self._relayout = relayout
        self._settings = settings
        self._import_dir = import_dir

    def current(self) -> ServerSettingsInfo:
        return ServerSettingsInfo(
            path_template=self._storage.path_template().text,
            max_upload_bytes=self._importer.max_upload_bytes(),
            max_video_upload_bytes=self._importer.max_video_upload_bytes(),
            web_accent_color=self.web_accent_color(),
            library_dir=self._storage.library_dir,
            import_dir=self._import_dir,
        )

    def change(self, changes: SettingsChanges, user_id: int | None) -> tuple[ServerSettingsInfo, JobInfo | None]:
        """All or nothing: every value is checked before any is stored. A different path template starts a
        relayout job, which is returned. Raises 422 validation_error."""
        template = None
        if changes.path_template is not None:
            template = self._storage.checked_path_template(changes.path_template)

        if changes.max_upload_bytes is not None:
            check_upload_limit(changes.max_upload_bytes)

        if changes.max_video_upload_bytes is not None:
            check_video_upload_limit(changes.max_video_upload_bytes)

        web_accent_color = None
        if changes.web_accent_color is not None:
            web_accent_color = checked_web_accent_color(changes.web_accent_color)

        if web_accent_color is not None:
            self._settings.set(WEB_ACCENT_COLOR_SETTING, web_accent_color)

        if changes.max_upload_bytes is not None:
            self._importer.set_max_upload_bytes(changes.max_upload_bytes)

        if changes.max_video_upload_bytes is not None:
            self._importer.set_max_video_upload_bytes(changes.max_video_upload_bytes)

        relayout_job = None
        if template is not None and template.text != self._storage.path_template().text:
            self._storage.set_path_template(template.text)
            relayout_job = self._relayout.start(user_id)

        return self.current(), relayout_job

    def web_accent_color(self) -> str:
        """Read by every web admin page, so on its own rather than through current()."""
        return self._settings.get(WEB_ACCENT_COLOR_SETTING) or DEFAULT_WEB_ACCENT_COLOR


def checked_web_accent_color(text: str) -> str:
    """"#3DAEE9" → "#3daee9". Raises 422 validation_error."""
    color = text.strip().lower()
    if not _COLOR_PATTERN.fullmatch(color):
        raise Problem(
            422,
            "validation_error",
            "Invalid request",
            "The request body or parameters are invalid.",
            errors=[{"field": "web_accent_color", "message": "A colour as #rrggbb, e.g. #3daee9"}],
        )

    return color


__all__ = [
    "DEFAULT_WEB_ACCENT_COLOR",
    "ServerSettings",
    "ServerSettingsInfo",
    "SettingsChanges",
    "checked_web_accent_color",
]
