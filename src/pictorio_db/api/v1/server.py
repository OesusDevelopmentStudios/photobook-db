# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

"""/server: lets a client check that a URL points to a compatible pictorio-db server, and whether it runs on
the same computer."""

from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

import pictorio_db
from pictorio_db.api.v1.dependencies import AuthServiceDependency, CurrentAuthentication, SameMachineDependency
from pictorio_db.utils.problems import problem_responses

SERVER_NAME = "pictorio-db"
API_VERSION = 1

router = APIRouter(prefix="/server", tags=["server"])


class ServerInfo(BaseModel):
    name: str
    version: str
    api_version: int
    setup_required: bool  # no admin account exists yet; the client sends the user to /admin/setup


@router.get("/info", summary="Server name and versions (public)")
def server_info(auth: AuthServiceDependency) -> ServerInfo:
    return ServerInfo(
        name=SERVER_NAME,
        version=pictorio_db.__version__,
        api_version=API_VERSION,
        setup_required=auth.is_setup_required(),
    )


class LocalLibrary(BaseModel):
    library_dir: str = Field(
        description="The library folder on this computer; an image's file is there at its storage_path",
        examples=["/home/kid/Pictures/pictorio-library"],
    )


@router.get(
    "/local",
    summary="The library folder, for a client on the computer the server runs on",
    responses=problem_responses(401, 404),
)
def local_library(
    machine: Annotated[
        str,
        Query(
            max_length=128,
            description='HMAC-SHA256 of "pictorio-db" with the machine ID in /etc/machine-id as the key, in hex',
        ),
    ],
    _user: CurrentAuthentication,
    same_machine: SameMachineDependency,
) -> LocalLibrary:
    """404 not_same_machine when the client is on another computer, or the server knows no machine ID."""
    return LocalLibrary(library_dir=str(same_machine.library_dir_for(machine)))
