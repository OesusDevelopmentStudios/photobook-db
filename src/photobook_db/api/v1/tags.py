# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

"""/tags: autocomplete and browsing tags; adding, renaming and deleting them for admins."""

from typing import Annotated

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, Field

from photobook_db.api.v1.dependencies import (
    CurrentAdmin,
    CurrentRestrictions,
    TagAdminDependency,
    TagsServiceDependency,
)
from photobook_db.services.tags import TagName
from photobook_db.utils.problems import problem_responses

router = APIRouter(prefix="/tags", tags=["tags"])


class TagWithCount(BaseModel):
    tag: str = Field(examples=["author:sample_artist"])
    media_count: int = Field(description="All photos and videos with this tag, also ones the user cannot see")


class TagList(BaseModel):
    items: list[TagWithCount]
    next_cursor: str | None = Field(description="Pass as `cursor` for the next page; null on the last page")


class NewTag(BaseModel):
    tag: str = Field(max_length=300, examples=["location:harbour"])


@router.get("", summary="Tags starting with a prefix, most used first", responses=problem_responses(401, 422))
def list_tags(
    restrictions: CurrentRestrictions,
    tags: TagsServiceDependency,
    prefix: Annotated[str, Query(max_length=200, description="May include a namespace: author:sam")] = "",
    namespace: Annotated[str | None, Query(max_length=100, description="Only this namespace")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Annotated[str | None, Query(max_length=500, description="next_cursor of the previous page")] = None,
) -> TagList:
    is_hidden = restrictions.is_tag_hidden if restrictions.active else None  # restricted tags are never suggested
    page = tags.tag_page(prefix, namespace, limit, is_hidden, cursor)
    items = [TagWithCount(tag=str(match.tag), media_count=match.media_count) for match in page.items]
    return TagList(items=items, next_cursor=page.next_cursor)


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Add a tag that nothing has yet, so that it is suggested",
    responses=problem_responses(401, 403, 409, 422),
)
def create_tag(new_tag: NewTag, _admin: CurrentAdmin, tag_admin: TagAdminDependency) -> TagWithCount:
    return TagWithCount(tag=str(tag_admin.create_tag(new_tag.tag)), media_count=0)


class TagRename(BaseModel):
    new_tag: str = Field(max_length=300, examples=["author:sample_artist"])


class RenamedTag(BaseModel):
    tag: str = Field(description="The new tag, normalized", examples=["author:sample_artist"])
    job_id: int | None = Field(description="The job that moves the affected files, if their folders change")


class DeletedTag(BaseModel):
    job_id: int | None = Field(description="The job that moves the affected files, if their folders change")


@router.patch(
    "/{namespace}/{name}",
    summary="Rename a tag, or merge it into an existing one, on every photo and video",
    responses=problem_responses(401, 403, 404, 422),
)
def rename_tag(
    namespace: str, name: str, rename: TagRename, admin: CurrentAdmin, tag_admin: TagAdminDependency
) -> RenamedTag:
    new_tag, job = tag_admin.rename_tag(TagName(namespace, name), rename.new_tag, admin.user.id)
    return RenamedTag(tag=str(new_tag), job_id=None if job is None else job.id)


@router.delete(
    "/{namespace}/{name}",
    summary="Remove a tag from every photo and video and delete it",
    responses=problem_responses(401, 403, 404, 422),
)
def delete_tag(namespace: str, name: str, admin: CurrentAdmin, tag_admin: TagAdminDependency) -> DeletedTag:
    job = tag_admin.delete_tag(TagName(namespace, name), admin.user.id)
    return DeletedTag(job_id=None if job is None else job.id)
