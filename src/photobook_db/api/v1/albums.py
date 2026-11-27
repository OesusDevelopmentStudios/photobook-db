# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

"""/albums: the albums (album: tags) with their size and cover, for the app's Albums tab."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from photobook_db.api.v1.dependencies import (
    CurrentAuthentication,
    CurrentRestrictions,
    PreferencesServiceDependency,
    SearchServiceDependency,
)
from photobook_db.utils.problems import problem_responses
from photobook_db.utils.time import as_utc

router = APIRouter(prefix="/albums", tags=["albums"])


class AlbumCover(BaseModel):
    id: int = Field(description="The album's first photo or video, in the order they were added to it")
    revision: int = Field(description="For the thumbnail's URL (rev=), as in the media object")


class Album(BaseModel):
    tag: str = Field(examples=["album:vacation_2015"])
    media_count: int = Field(description="The photos and videos you may see")
    cover: AlbumCover
    last_changed_at: datetime = Field(description="When a photo or video was last added to it")


class AlbumList(BaseModel):
    items: list[Album]


@router.get("", summary="Albums, most recently changed first", responses=problem_responses(401, 422))
def list_albums(
    authentication: CurrentAuthentication,
    restrictions: CurrentRestrictions,
    search: SearchServiceDependency,
    preferences: PreferencesServiceDependency,
    include_hidden: Annotated[
        bool, Query(description="Also albums with photos or videos that your hidden-by-default filters match")
    ] = False,
) -> AlbumList:
    hidden_filters = () if include_hidden else tuple(preferences.hidden_filters(authentication.user.id))
    albums = search.albums(restrictions.visible_media_filter(), hidden_filters)
    return AlbumList(
        items=[
            Album(
                tag=f"album:{album.name}",
                media_count=album.media_count,
                cover=AlbumCover(id=album.cover_id, revision=album.cover_revision),
                last_changed_at=as_utc(album.last_changed_at),
            )
            for album in albums
        ]
    )
