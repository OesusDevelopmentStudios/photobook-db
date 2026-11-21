# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

import logging

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from fastapi import FastAPI

import photobook_db
from photobook_db import admin_web
from photobook_db.api import v1
from photobook_db.config import Config
from photobook_db.db import DATABASE_FILE_NAME, open_database
from photobook_db.services.access import AccessService
from photobook_db.services.auth import AuthService
from photobook_db.services.preferences import PreferencesService
from photobook_db.services.rotation import RotationService
from photobook_db.services.same_machine import SameMachine
from photobook_db.services.search import SearchService
from photobook_db.services.server_settings import ServerSettings
from photobook_db.services.images import ImagesService
from photobook_db.services.jobs import FolderImport, JobKind, JobsService, Relayout
from photobook_db.services.importer import ImporterService
from photobook_db.services.settings import SettingsService
from photobook_db.services.storage import StorageService
from photobook_db.services.tag_admin import TagAdmin
from photobook_db.services.tags import TagsService
from photobook_db.services.thumbnails import ThumbnailsService
from photobook_db.utils.problems import install_problem_handlers

_logger = logging.getLogger(__name__)


def create_app(config: Config) -> FastAPI:
    """Creating the app touches no files; the database is opened when the server starts.
    config.data_dir and config.library_dir must exist by then."""
    app = FastAPI(
        title="photobook-db",
        version=photobook_db.__version__,
        openapi_url="/api/openapi.json",
        docs_url="/api/docs",
        redoc_url=None,
        lifespan=_lifespan(config),
    )
    install_problem_handlers(app)  # every error is answered as RFC 9457 Problem Details
    app.include_router(v1.router)
    app.include_router(admin_web.router)
    admin_web.install_page_handlers(app)  # logins and errors of the admin pages
    return app


def _lifespan(config: Config) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        database = open_database(config.data_dir / DATABASE_FILE_NAME)
        app.state.database = database
        app.state.auth = AuthService(database)
        settings = app.state.settings = SettingsService(database)
        # Tags and storage ask each other: which namespaces exist, which ones form folder names.
        app.state.tags = TagsService(database, lambda: app.state.storage.path_template_namespaces())
        app.state.storage = StorageService(database, settings, config.library_dir, app.state.tags.namespace_names)
        app.state.storage.finish_interrupted_moves()  # before any request can touch the files
        app.state.storage.clean_incoming()  # uploads a restart interrupted
        app.state.thumbnails = ThumbnailsService(config.data_dir / "thumbnails")
        app.state.images = ImagesService(database, app.state.tags, app.state.storage)
        app.state.rotation = RotationService(app.state.images, app.state.storage, app.state.thumbnails)
        app.state.search = SearchService(database, app.state.tags.namespace_names)
        app.state.preferences = PreferencesService(database, app.state.tags.namespace_names)
        app.state.access = AccessService(database, app.state.tags.namespace_names)
        app.state.importer = ImporterService(
            app.state.images, app.state.tags, app.state.storage, app.state.thumbnails, settings
        )
        app.state.jobs = JobsService(database)
        app.state.folder_import = FolderImport(app.state.jobs, app.state.importer, app.state.tags, config.import_dir)
        app.state.jobs.register(JobKind.FOLDER_IMPORT, app.state.folder_import.run)
        relayout = Relayout(app.state.jobs, app.state.images)
        app.state.jobs.register(JobKind.RELAYOUT, relayout.run)
        app.state.server_settings = ServerSettings(
            app.state.storage, app.state.importer, app.state.tags, relayout, settings, config.import_dir
        )
        app.state.tag_admin = TagAdmin(
            app.state.tags, app.state.images, app.state.access, app.state.preferences, app.state.storage, relayout
        )
        app.state.same_machine = SameMachine(
            config.host_machine_id_file, config.host_library_dir or config.library_dir
        )
        app.state.jobs.start()
        _announce_setup_code(app.state.auth)
        yield
        app.state.jobs.stop()  # the running job stops after its current item
        database.close()

    return lifespan


def _announce_setup_code(auth: AuthService) -> None:
    setup_code = auth.setup_code()
    if setup_code is not None:
        _logger.warning(
            "No admin account exists yet. Create it at /admin/setup on this server with the setup code %s",
            setup_code,
        )
