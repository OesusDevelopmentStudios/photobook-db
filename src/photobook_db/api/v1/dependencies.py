# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

"""What the routers need from the app: the services, and the user who sent the request."""

from typing import Annotated

from fastapi import Depends, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from photobook_db.services.access import AccessService, Restrictions
from photobook_db.services.auth import Authentication, AuthService, Role, not_authenticated
from photobook_db.services.images import ImagesService
from photobook_db.services.importer import ImporterService
from photobook_db.services.jobs import FolderImport, JobsService
from photobook_db.services.preferences import PreferencesService
from photobook_db.services.rotation import RotationService
from photobook_db.services.media_probe import MediaProbe
from photobook_db.services.same_machine import SameMachine
from photobook_db.services.search import SearchService
from photobook_db.services.server_settings import ServerSettings
from photobook_db.services.storage import StorageService
from photobook_db.services.storyboard import StoryboardService
from photobook_db.services.stream import StreamService
from photobook_db.services.tag_admin import TagAdmin
from photobook_db.services.tags import TagsService
from photobook_db.services.thumbnails import ThumbnailsService
from photobook_db.utils.problems import Problem

# auto_error=False: a missing token is answered with our own 401 problem, not FastAPI's error.
_bearer_token = HTTPBearer(auto_error=False, description="An API token from POST /api/v1/auth/tokens")


def access_service(request: Request) -> AccessService:
    return request.app.state.access


def auth_service(request: Request) -> AuthService:
    return request.app.state.auth


def tag_admin(request: Request) -> TagAdmin:
    return request.app.state.tag_admin


def tags_service(request: Request) -> TagsService:
    return request.app.state.tags


def images_service(request: Request) -> ImagesService:
    return request.app.state.images


def importer_service(request: Request) -> ImporterService:
    return request.app.state.importer


def folder_import(request: Request) -> FolderImport:
    return request.app.state.folder_import


def jobs_service(request: Request) -> JobsService:
    return request.app.state.jobs


def preferences_service(request: Request) -> PreferencesService:
    return request.app.state.preferences


def media_probe(request: Request) -> MediaProbe:
    return request.app.state.media_probe


def rotation_service(request: Request) -> RotationService:
    return request.app.state.rotation


def same_machine(request: Request) -> SameMachine:
    return request.app.state.same_machine


def search_service(request: Request) -> SearchService:
    return request.app.state.search


def server_settings(request: Request) -> ServerSettings:
    return request.app.state.server_settings


def storage_service(request: Request) -> StorageService:
    return request.app.state.storage


def thumbnails_service(request: Request) -> ThumbnailsService:
    return request.app.state.thumbnails


def storyboard_service(request: Request) -> StoryboardService:
    return request.app.state.storyboards


def stream_service(request: Request) -> StreamService:
    return request.app.state.streams


def current_authentication(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_token)],
    auth: Annotated[AuthService, Depends(auth_service)],
) -> Authentication:
    """Protects an endpoint: the request needs `Authorization: Bearer <token>`, or it gets 401."""
    if credentials is None:
        raise not_authenticated()

    return auth.authenticate(credentials.credentials)


def current_restrictions(
    authentication: Annotated[Authentication, Depends(current_authentication)],
    access: Annotated[AccessService, Depends(access_service)],
) -> Restrictions:
    """What the logged-in user may see and use; none for admins. Every image endpoint needs it."""
    return access.restrictions_for(authentication.user)


def file_restrictions(
    image_id: int,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_token)],
    auth: Annotated[AuthService, Depends(auth_service)],
    access: Annotated[AccessService, Depends(access_service)],
    streams: Annotated[StreamService, Depends(stream_service)],
    t: Annotated[int | None, Query(description="With exp and sig: a URL from POST /images/{id}/stream")] = None,
    exp: Annotated[int | None, Query(description="When the stream URL expires, in Unix time")] = None,
    sig: Annotated[str | None, Query(max_length=100, description="The stream URL's signature")] = None,
) -> Restrictions:
    """Like current_restrictions, for an image's file: a player that cannot send the token uses a signed stream URL
    for that one file instead. Its login must still exist, and the user must still see the file."""
    if credentials is not None:
        authentication = auth.authenticate(credentials.credentials)
    elif t is not None and exp is not None and sig is not None:
        streams.check(image_id, t, exp, sig)
        authentication = auth.authenticate_token_id(t)
    else:
        raise not_authenticated()

    return access.restrictions_for(authentication.user)


def current_admin(authentication: Annotated[Authentication, Depends(current_authentication)]) -> Authentication:
    """Protects an admin endpoint: other users get 403."""
    if authentication.user.role != Role.ADMIN:
        raise Problem(403, "forbidden", "Admins only", "This needs an admin account.")

    return authentication


AccessServiceDependency = Annotated[AccessService, Depends(access_service)]
AuthServiceDependency = Annotated[AuthService, Depends(auth_service)]
TagsServiceDependency = Annotated[TagsService, Depends(tags_service)]
TagAdminDependency = Annotated[TagAdmin, Depends(tag_admin)]
ImagesServiceDependency = Annotated[ImagesService, Depends(images_service)]
ImporterServiceDependency = Annotated[ImporterService, Depends(importer_service)]
JobsServiceDependency = Annotated[JobsService, Depends(jobs_service)]
FolderImportDependency = Annotated[FolderImport, Depends(folder_import)]
PreferencesServiceDependency = Annotated[PreferencesService, Depends(preferences_service)]
RotationServiceDependency = Annotated[RotationService, Depends(rotation_service)]
MediaProbeDependency = Annotated[MediaProbe, Depends(media_probe)]
SameMachineDependency = Annotated[SameMachine, Depends(same_machine)]
SearchServiceDependency = Annotated[SearchService, Depends(search_service)]
ServerSettingsDependency = Annotated[ServerSettings, Depends(server_settings)]
StorageServiceDependency = Annotated[StorageService, Depends(storage_service)]
StoryboardServiceDependency = Annotated[StoryboardService, Depends(storyboard_service)]
StreamServiceDependency = Annotated[StreamService, Depends(stream_service)]
ThumbnailsServiceDependency = Annotated[ThumbnailsService, Depends(thumbnails_service)]
CurrentAuthentication = Annotated[Authentication, Depends(current_authentication)]
CurrentAdmin = Annotated[Authentication, Depends(current_admin)]
CurrentRestrictions = Annotated[Restrictions, Depends(current_restrictions)]
FileRestrictions = Annotated[Restrictions, Depends(file_restrictions)]
