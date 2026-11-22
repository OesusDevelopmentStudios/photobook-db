# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

"""Web admin pages under /admin. Like the REST routers they are thin: they call the same services."""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from photobook_db.admin_web import jobs, login, namespaces, overview, settings, setup, tags, users
from photobook_db.admin_web._session import install_page_handlers

router = APIRouter(prefix="/admin", include_in_schema=False)  # web pages, not part of the REST API
router.include_router(setup.router)
router.include_router(login.router)
router.include_router(settings.router)
router.include_router(users.router)
router.include_router(namespaces.router)
router.include_router(tags.router)
router.include_router(jobs.router)
router.include_router(jobs.old_urls)
router.add_api_route("", overview.overview, response_class=HTMLResponse)  # /admin itself: a router needs a path

# The icon. Public, as the login page shows it too. A router cannot carry a mount, so the app mounts it.
STATIC_PATH = "/admin/static"
static_files = StaticFiles(directory=Path(__file__).parent / "static")

__all__ = ["STATIC_PATH", "install_page_handlers", "router", "static_files"]
