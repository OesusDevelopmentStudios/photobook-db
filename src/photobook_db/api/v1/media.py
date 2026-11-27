# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

"""/media: uploading photos and videos, and reading them back with their files and thumbnails."""

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request, Response, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, ValidationError

from photobook_db.api.v1._upload_receiver import METADATA_PART, receive_upload
from photobook_db.api.v1.dependencies import (
    CurrentAuthentication,
    CurrentRestrictions,
    FileRestrictions,
    MediaServiceDependency,
    ImporterServiceDependency,
    PreferencesServiceDependency,
    RotationServiceDependency,
    SearchServiceDependency,
    StorageServiceDependency,
    StoryboardServiceDependency,
    StreamServiceDependency,
    TagsServiceDependency,
    ThumbnailsServiceDependency,
)
from photobook_db.services.access import Restrictions
from photobook_db.services.media import MediaRecord, MediaService
from photobook_db.services.importer import UploadMetadata
from photobook_db.services.search import DEFAULT_LIMIT, MAX_LIMIT, MediaKinds, Order, SearchRequest, Sort
from photobook_db.services.storage import StorageService
from photobook_db.services.storyboard import StoryboardLayout, StoryboardService
from photobook_db.services.thumbnails import THUMBNAIL_SIZES
from photobook_db.utils.problems import Problem, problem_responses
from photobook_db.utils.time import as_utc

router = APIRouter(prefix="/media", tags=["media"])

# A thumbnail never changes for a given SHA-256 and revision, so clients may keep it for good.
THUMBNAIL_CACHE_CONTROL = "private, max-age=31536000, immutable"

# The parameters that choose which media a search finds, shared by the search and its count
SearchText = Annotated[str, Query(max_length=2000, description="Search query, e.g. category:artwork -rating:private")]
ApplyHiddenFilters = Annotated[bool, Query(description="false skips your hidden-by-default filters")]
AlbumName = Annotated[
    str | None, Query(max_length=200, description="Only this album's media: the name of an album: tag, e.g. trip")
]
MediaChoice = Annotated[
    MediaKinds, Query(description="image, video, or all (the default)")
]


class MediaLinks(BaseModel):
    self: str
    file: str
    thumbnail: str
    storyboard: str | None = Field(default=None, exclude_if=lambda value: value is None, description="Videos only")


class VideoDetails(BaseModel):
    video_codec: str = Field(examples=["h264"])
    audio_codec: str | None = Field(description="null: no sound", examples=["aac"])
    bitrate: int | None = Field(description="Bits per second, of the whole file")
    frame_rate: float | None


class MediaObject(BaseModel):
    id: int
    sha256: str
    original_filename: str
    storage_path: str = Field(description="Relative to the library folder")
    mime_type: str
    width: int = Field(description="As it is shown: after an image's EXIF orientation or a video's rotation")
    height: int
    file_size: int
    taken_at: datetime = Field(description="Local wall-clock time, without a time zone")
    taken_at_source: Literal["exif", "file_mtime", "import_time", "container"] = Field(
        description="container: a video's own recording time"
    )
    imported_at: datetime
    updated_at: datetime
    revision: int = Field(
        description="Grows when the file changes, e.g. a saved rotation; put it into thumbnail URLs as `rev`"
    )
    tags: list[str] = Field(
        description="Sorted by namespace, then name", examples=[["category:photo", "general:summer"]]
    )
    links: MediaLinks
    media_kind: Literal["image", "video"]
    # Only for videos; an image's object has neither.
    duration_ms: int | None = Field(default=None, exclude_if=lambda value: value is None)
    video: VideoDetails | None = Field(default=None, exclude_if=lambda value: value is None)


class MediaList(BaseModel):
    items: list[MediaObject]
    next_cursor: str | None = Field(description="Pass as `cursor` for the next page; null on the last page")
    effective_query: str = Field(description="The query with the hidden-by-default filters that were added")


class Storyboard(BaseModel):
    """Frames of a video at a fixed interval, in one sprite image filled row by row."""

    interval_ms: int = Field(description="Frame i shows the video at i × interval_ms", examples=[2224])
    frame_count: int = Field(examples=[100])
    frame_width: int = Field(examples=[160])
    frame_height: int = Field(examples=[90])
    columns: int = Field(description="Frames per row of the sprite", examples=[10])
    sprite: str = Field(
        description="The sprite's URL: a WebP image", examples=["/api/v1/media/1234/storyboard/sprite"]
    )


class StreamUrl(BaseModel):
    url: str = Field(
        description="The video's file without the token: for players that cannot send it",
        examples=["/api/v1/media/1234/file?t=56&exp=1795000000&sig=…"],
    )
    expires_at: datetime = Field(description="Also ends when this login ends, or the user may no longer see it")


class TagCountItem(BaseModel):
    tag: str = Field(examples=["general:beach"])
    media_count: int


class TagCountList(BaseModel):
    items: list[TagCountItem] = Field(description="Every tag of the media item, in its object's order")


class MediaCount(BaseModel):
    count: int = Field(description="All media the search finds, over all its pages")
    effective_query: str = Field(description="The query with the hidden-by-default filters that were added")


class TagChanges(BaseModel):
    add_tags: list[str] = Field(default=[], max_length=200, examples=[["author:sample_artist"]])
    remove_tags: list[str] = Field(default=[], max_length=200, examples=[["general:summer"]])


class BulkTagChanges(TagChanges):
    media_ids: list[int] = Field(min_length=1, max_length=1000)


class BulkTagResult(BaseModel):
    updated: int


class Rotation(BaseModel):
    degrees: Literal[90, 180, 270] = Field(description="Clockwise, from how the image is shown now")


class UploadMetadataModel(BaseModel):
    original_filename: str = Field(min_length=1, max_length=255, examples=["IMG_2041.jpg"])
    file_mtime: datetime | None = Field(default=None, description="The original file's modification time")
    client_sha256: str | None = Field(default=None, pattern="^[0-9a-fA-F]{64}$")
    tags: list[str] = Field(default=[], max_length=200, examples=[["category:photo", "summer"]])


_UPLOAD_REQUEST_BODY = {
    "required": True,
    "content": {
        "multipart/form-data": {
            "schema": {
                "type": "object",
                "required": ["file", "metadata"],
                "properties": {
                    "file": {"type": "string", "format": "binary", "description": "The photo or video"},
                    "metadata": {
                        "type": "string",
                        "description": "JSON: original_filename, file_mtime?, client_sha256?, tags?",
                        "example": '{"original_filename": "IMG_2041.jpg", "tags": ["category:photo", "summer"]}',
                    },
                },
            }
        }
    },
}


@router.get("", summary="Search and list photos and videos", responses=problem_responses(400, 401, 422))
def search_media(
    authentication: CurrentAuthentication,
    restrictions: CurrentRestrictions,
    search: SearchServiceDependency,
    media_service: MediaServiceDependency,
    preferences: PreferencesServiceDependency,
    q: SearchText = "",
    sort: Annotated[Sort, Query(description="album: the order media were added to `album`; needs it")] = Sort.TAKEN_AT,
    order: Annotated[Order, Query(description="Ignored for sort=random and sort=album")] = Order.DESC,
    seed: Annotated[int | None, Query(description="Required for sort=random: same seed, same order")] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    cursor: Annotated[str | None, Query(max_length=500, description="next_cursor of the previous page")] = None,
    apply_hidden_filters: ApplyHiddenFilters = True,
    album: AlbumName = None,
    media: MediaChoice = MediaKinds.ALL,
) -> MediaList:
    hidden_filters = preferences.hidden_filters(authentication.user.id) if apply_hidden_filters else []
    request = SearchRequest(
        query=q,
        sort=sort,
        order=order,
        seed=seed,
        limit=limit,
        cursor=cursor,
        hidden_filters=tuple(hidden_filters),
        album=album,
        media=media,
    )
    page = search.search(request, restrictions.visible_media_filter())
    return MediaList(
        items=[_media_object(record) for record in media_service.get_media_list(page.media_ids)],
        next_cursor=page.next_cursor,
        effective_query=page.effective_query,
    )


# Before /{media_id}, so that "count" is never read as an id.
@router.get("/count", summary="How many photos and videos a search finds", responses=problem_responses(400, 401, 422))
def count_media(
    authentication: CurrentAuthentication,
    restrictions: CurrentRestrictions,
    search: SearchServiceDependency,
    preferences: PreferencesServiceDependency,
    q: SearchText = "",
    apply_hidden_filters: ApplyHiddenFilters = True,
    album: AlbumName = None,
    media: MediaChoice = MediaKinds.ALL,
) -> MediaCount:
    hidden_filters = preferences.hidden_filters(authentication.user.id) if apply_hidden_filters else []
    found = search.count(q, tuple(hidden_filters), restrictions.visible_media_filter(), album, media)
    return MediaCount(count=found.count, effective_query=found.effective_query)


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Upload one photo or video",
    responses=problem_responses(401, 403, 409, 413, 415, 422),
    openapi_extra={"requestBody": _UPLOAD_REQUEST_BODY},
)
async def upload_media(
    request: Request,
    response: Response,
    authentication: CurrentAuthentication,
    restrictions: CurrentRestrictions,
    importer: ImporterServiceDependency,
) -> MediaObject:
    incoming = importer.new_incoming_path()
    # The importer checks the limit for the kind of file once it knows what the file is.
    metadata_json = await receive_upload(request, incoming, importer.largest_upload_bytes())
    try:
        metadata = _parse_metadata(metadata_json)
    except Problem:
        incoming.unlink(missing_ok=True)
        raise

    # Hashing and thumbnails take a while; the server keeps answering other requests meanwhile.
    record = await run_in_threadpool(
        importer.import_media, incoming, metadata, authentication.user.id, restrictions
    )
    response.headers["Location"] = f"/api/v1/media/{record.id}"
    return _media_object(record)


@router.post(
    "/bulk-tags",
    summary="Change the tags of many photos and videos; all or nothing",
    responses=problem_responses(401, 403, 404, 422),
)
async def change_tags_of_many(
    changes: BulkTagChanges, restrictions: CurrentRestrictions, media_service: MediaServiceDependency
) -> BulkTagResult:
    updated = await run_in_threadpool(
        media_service.change_tags_of_many, changes.media_ids, changes.add_tags, changes.remove_tags, restrictions
    )
    return BulkTagResult(updated=updated)


@router.patch(
    "/{media_id}",
    summary="Change the tags of a photo or video; a change to a folder-forming tag moves the file",
    responses=problem_responses(401, 403, 404, 422),
)
async def change_tags(
    media_id: int, changes: TagChanges, restrictions: CurrentRestrictions, media_service: MediaServiceDependency
) -> MediaObject:
    record = await run_in_threadpool(
        media_service.change_tags, media_id, changes.add_tags, changes.remove_tags, restrictions
    )
    return _media_object(record)


@router.post(
    "/{media_id}/rotation",
    summary="Turn an image, saved into its file without losing quality",
    description=(
        "JPEG and TIFF get a new EXIF orientation; PNG and BMP have their pixels turned. Other types: "
        "422 rotation_not_supported. The response has the new size and the next `revision`; `sha256` stays "
        "the uploaded file's."
    ),
    responses=problem_responses(401, 404, 422),
)
async def rotate_media(
    media_id: int, rotation: Rotation, restrictions: CurrentRestrictions, rotations: RotationServiceDependency
) -> MediaObject:
    record = await run_in_threadpool(rotations.rotate, media_id, rotation.degrees, restrictions)
    return _media_object(record)


@router.get("/{media_id}", summary="One photo or video", responses=problem_responses(401, 404, 422))
def get_media(media_id: int, restrictions: CurrentRestrictions, media_service: MediaServiceDependency) -> MediaObject:
    return _media_object(media_service.get_visible_media(media_id, restrictions))


@router.get(
    "/{media_id}/tag-counts",
    summary="How many photos and videos have each of this one's tags",
    description="For the viewer's tag pane. The counts are over all media, also ones you cannot see, as in "
    "autocomplete.",
    responses=problem_responses(401, 404, 422),
)
def get_tag_counts(
    media_id: int, restrictions: CurrentRestrictions, media_service: MediaServiceDependency, tags: TagsServiceDependency
) -> TagCountList:
    media = media_service.get_visible_media(media_id, restrictions)
    counts = tags.media_counts(list(media.tags))
    return TagCountList(items=[TagCountItem(tag=str(count.tag), media_count=count.media_count) for count in counts])


@router.get(
    "/{media_id}/file",
    summary="The original file; supports Range and If-None-Match. The token, or a URL from POST /media/{id}/stream",
    response_class=FileResponse,
    responses={200: {"content": {"image/*": {}}}, 304: {"description": "Not modified"}, **problem_responses(401, 404)},
)
def get_media_file(
    media_id: int,
    request: Request,
    restrictions: FileRestrictions,
    media_service: MediaServiceDependency,
    storage: StorageServiceDependency,
) -> Response:
    record = media_service.get_visible_media(media_id, restrictions)
    etag = f'"{record.sha256}-{record.revision}"'
    if _client_has(request, etag):
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers={"ETag": etag})

    return FileResponse(
        storage.absolute_path(record.storage_path),
        media_type=record.mime_type,
        filename=record.original_filename,
        content_disposition_type="inline",
        headers={"ETag": etag},
    )


@router.get(
    "/{media_id}/thumbnail",
    summary="A WebP thumbnail; the longest side is at most `size` pixels",
    response_class=FileResponse,
    responses={
        200: {"content": {"image/webp": {}}},
        304: {"description": "Not modified"},
        **problem_responses(401, 404, 422),
    },
)
def get_media_thumbnail(
    media_id: int,
    request: Request,
    restrictions: CurrentRestrictions,
    media_service: MediaServiceDependency,
    storage: StorageServiceDependency,
    thumbnails: ThumbnailsServiceDependency,
    importer: ImporterServiceDependency,
    size: Annotated[int, Query(json_schema_extra={"enum": list(THUMBNAIL_SIZES)})] = 256,
    rev: Annotated[
        int | None,
        Query(description="Its `revision`: a new URL after the file changed, so caches fetch it anew"),
    ] = None,
) -> Response:
    if size not in THUMBNAIL_SIZES:  # query values arrive as text, so a Literal type would refuse every size
        raise Problem(
            422,
            "validation_error",
            "Invalid request",
            "The request body or parameters are invalid.",
            errors=[{"field": "query.size", "message": f"One of {list(THUMBNAIL_SIZES)}"}],
        )

    record = media_service.get_visible_media(media_id, restrictions)
    etag = f'"{record.sha256}-{record.revision}-{size}"'
    headers = {"ETag": etag, "Cache-Control": THUMBNAIL_CACHE_CONTROL}
    if _client_has(request, etag):
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)

    thumbnail = thumbnails.thumbnail_path(record.sha256, size, record.revision)
    if not thumbnail.exists():  # e.g. the thumbnail folder was cleared: make them again
        importer.make_thumbnails(
            storage.absolute_path(record.storage_path),
            record.sha256,
            record.revision,
            record.duration_ms if record.media_kind == "video" else None,
        )

    return FileResponse(thumbnail, media_type="image/webp", headers=headers)


@router.get(
    "/{media_id}/storyboard",
    summary="A video's frames for previews while scrubbing; the first request makes them and may take seconds",
    responses=problem_responses(401, 404, 503),
)
def get_storyboard(
    media_id: int,
    restrictions: CurrentRestrictions,
    media_service: MediaServiceDependency,
    storage: StorageServiceDependency,
    storyboards: StoryboardServiceDependency,
) -> Storyboard:
    record = _visible_video(media_id, restrictions, media_service)
    layout = _storyboard_of(record, storage, storyboards)
    return Storyboard(**layout.to_json(), sprite=f"/api/v1/media/{record.id}/storyboard/sprite")


@router.get(
    "/{media_id}/storyboard/sprite",
    summary="The storyboard's frames as one WebP image",
    response_class=FileResponse,
    responses={
        200: {"content": {"image/webp": {}}},
        304: {"description": "Not modified"},
        **problem_responses(401, 404, 503),
    },
)
def get_storyboard_sprite(
    media_id: int,
    request: Request,
    restrictions: CurrentRestrictions,
    media_service: MediaServiceDependency,
    storage: StorageServiceDependency,
    storyboards: StoryboardServiceDependency,
) -> Response:
    record = _visible_video(media_id, restrictions, media_service)
    etag = f'"{record.sha256}-storyboard"'
    headers = {"ETag": etag, "Cache-Control": THUMBNAIL_CACHE_CONTROL}  # a video's file never changes
    if _client_has(request, etag):
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)

    _storyboard_of(record, storage, storyboards)  # e.g. the thumbnail folder was cleared: made again
    return FileResponse(storyboards.sprite_path(record.sha256), media_type="image/webp", headers=headers)


@router.post(
    "/{media_id}/stream",
    summary="A URL to stream a video from without the token, for a player that cannot send it",
    responses=problem_responses(401, 404),
)
def create_stream_url(
    media_id: int,
    authentication: CurrentAuthentication,
    restrictions: CurrentRestrictions,
    media_service: MediaServiceDependency,
    streams: StreamServiceDependency,
) -> StreamUrl:
    record = _visible_video(media_id, restrictions, media_service)
    grant = streams.grant(record.id, authentication.token_id)
    return StreamUrl(url=f"/api/v1/media/{record.id}/file?{grant.query()}", expires_at=grant.expires_at)


def _visible_video(media_id: int, restrictions: Restrictions, media_service: MediaService) -> MediaRecord:
    record = media_service.get_visible_media(media_id, restrictions)
    if record.media_kind != "video":
        raise Problem(404, "not_found", "Not a video", "This is an image; only videos have this.")

    return record


def _storyboard_of(record: MediaRecord, storage: StorageService, storyboards: StoryboardService) -> StoryboardLayout:
    return storyboards.storyboard(
        storage.absolute_path(record.storage_path), record.sha256, record.duration_ms or 0, record.width, record.height
    )


def _parse_metadata(metadata_json: bytes) -> UploadMetadata:
    try:
        model = UploadMetadataModel.model_validate_json(metadata_json)
    except ValidationError as error:
        raise Problem(
            422,
            "validation_error",
            "Invalid request",
            "The request body or parameters are invalid.",
            errors=[
                {"field": ".".join([METADATA_PART, *(str(part) for part in issue["loc"])]), "message": issue["msg"]}
                for issue in error.errors()
            ],
        ) from None

    return UploadMetadata(
        original_filename=model.original_filename,
        file_mtime=model.file_mtime,
        client_sha256=model.client_sha256,
        tags=tuple(model.tags),
    )


def _client_has(request: Request, etag: str) -> bool:
    """If-None-Match: the client already has this version, so 304 Not Modified is enough."""
    if_none_match = request.headers.get("if-none-match", "")
    return etag in (tag.strip() for tag in if_none_match.split(",")) or if_none_match.strip() == "*"


def _media_object(record: MediaRecord) -> MediaObject:
    base = f"/api/v1/media/{record.id}"
    return MediaObject(
        id=record.id,
        sha256=record.sha256,
        original_filename=record.original_filename,
        storage_path=record.storage_path,
        mime_type=record.mime_type,
        width=record.width,
        height=record.height,
        file_size=record.file_size,
        taken_at=record.taken_at,
        taken_at_source=record.taken_at_source,  # type: ignore[arg-type]
        imported_at=as_utc(record.imported_at),
        updated_at=as_utc(record.updated_at),
        revision=record.revision,
        tags=[str(tag) for tag in record.tags],
        links=MediaLinks(
            self=base,
            file=f"{base}/file",
            thumbnail=f"{base}/thumbnail",
            storyboard=f"{base}/storyboard" if record.media_kind == "video" else None,
        ),
        media_kind=record.media_kind,  # type: ignore[arg-type]
        duration_ms=record.duration_ms,
        video=_video_details(record),
    )


def _video_details(record: MediaRecord) -> VideoDetails | None:
    if record.media_kind != "video":
        return None

    return VideoDetails(
        video_codec=record.video_codec or "",
        audio_codec=record.audio_codec,
        bitrate=record.bitrate,
        frame_rate=record.frame_rate,
    )
