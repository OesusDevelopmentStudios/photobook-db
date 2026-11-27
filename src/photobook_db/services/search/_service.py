# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

import base64
import binascii
import json
from collections.abc import Callable, Collection
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from itertools import groupby

from sqlalchemy import ColumnElement, Select, Subquery, and_, func, literal, or_, select, true, tuple_
from sqlalchemy.orm import Session

from photobook_db.db import Database
from photobook_db.db.models import Media, Namespace, Tag, media_tag
from photobook_db.services.search._conditions import matching_condition
from photobook_db.services.search._hidden_filters import FilteredQuery, apply_hidden_filters
from photobook_db.services.search._parser import parse_query
from photobook_db.services.tags import normalized_tag_name
from photobook_db.utils.problems import Problem

DEFAULT_LIMIT = 100
MAX_LIMIT = 500
ALBUM_NAMESPACE = "album"


class Sort(StrEnum):
    TAKEN_AT = "taken_at"
    IMPORTED_AT = "imported_at"
    RANDOM = "random"
    ALBUM = "album"  # when the album tag was added, then by file name; only with an album


class MediaKinds(StrEnum):
    IMAGE = "image"  # images only: what clients from before videos expect, so it is the default
    VIDEO = "video"
    ALL = "all"


class Order(StrEnum):
    DESC = "desc"
    ASC = "asc"


@dataclass(frozen=True)
class SearchRequest:
    query: str = ""
    sort: Sort = Sort.TAKEN_AT
    order: Order = Order.DESC  # ignored for random
    seed: int | None = None  # required for random
    limit: int = DEFAULT_LIMIT
    cursor: str | None = None  # next_cursor of the previous page
    hidden_filters: tuple[str, ...] = ()
    album: str | None = None  # only this album's media, e.g. "vacation_2015"
    media: MediaKinds = MediaKinds.IMAGE


@dataclass(frozen=True)
class SearchPage:
    media_ids: list[int]
    next_cursor: str | None  # None on the last page
    effective_query: str  # the query with the hidden filters that were added


@dataclass(frozen=True)
class AlbumSummary:
    name: str  # of its album: tag
    media_count: int
    cover_id: int  # the first media item in album order
    cover_revision: int
    last_changed_at: datetime  # when a media item was last added to it


@dataclass(frozen=True)
class SearchCount:
    count: int
    effective_query: str


class SearchService:
    """Searches media with the query language. Pages are found by "keyset": each page continues after the
    sort value and id of the previous page's last media item, so pages stay correct while media are added."""

    def __init__(self, database: Database, namespace_names: Callable[[], Collection[str]]) -> None:
        self._database = database
        self._namespace_names = namespace_names

    def search(self, request: SearchRequest, visible_media: ColumnElement[bool]) -> SearchPage:
        """Only media matching `visible_media` (the user's restrictions; not shown in effective_query).
        Raises 400 invalid_query, 422 validation_error for a missing seed, a damaged cursor or sort=album without
        an album, or 422 invalid_tag for an album name the tag rules refuse."""
        filtered = self._filtered(request.query, request.hidden_filters)
        links = None if request.album is None else _album_links(request.album)
        sort_keys = _sort_keys(request.sort, request.seed, links)
        descending = request.sort not in (Sort.RANDOM, Sort.ALBUM) and request.order == Order.DESC
        statement = _in_album(select(Media.id, *sort_keys), links)
        statement = statement.where(matching_condition(filtered.tree), visible_media, _of_media(request.media))
        if request.cursor is not None:
            last_values, last_id = _decode_cursor(request.cursor, request.sort)
            statement = statement.where(_after(sort_keys, last_values, last_id, descending))

        directed = [key.desc() if descending else key.asc() for key in [*sort_keys, Media.id]]
        statement = statement.order_by(*directed).limit(request.limit + 1)  # one more: is there a next page?

        with self._database.session() as session:
            rows = session.execute(statement).all()

        page_rows = rows[: request.limit]
        has_next_page = len(rows) > request.limit
        next_cursor = _encode_cursor(list(page_rows[-1][1:]), page_rows[-1][0]) if has_next_page else None
        return SearchPage([row[0] for row in page_rows], next_cursor, filtered.effective_query)

    def count(
        self,
        query: str,
        hidden_filters: tuple[str, ...],
        visible_media: ColumnElement[bool],
        album: str | None = None,
        media: MediaKinds = MediaKinds.IMAGE,
    ) -> SearchCount:
        """How many media search() finds over all its pages. Raises 400 invalid_query or 422 invalid_tag."""
        filtered = self._filtered(query, hidden_filters)
        links = None if album is None else _album_links(album)
        statement = _in_album(select(func.count(Media.id)), links)
        statement = statement.where(matching_condition(filtered.tree), visible_media, _of_media(media))
        with self._database.session() as session:
            count = session.scalar(statement) or 0

        return SearchCount(count, filtered.effective_query)

    def albums(self, visible_media: ColumnElement[bool], hidden_filters: tuple[str, ...]) -> list[AlbumSummary]:
        """Each album with a media item the user may see (`visible_media`): how many, the first in album order as its
        cover, and when one was last added; most recently changed first. An album with a media item that one of
        `hidden_filters` matches is left out."""
        links = (
            select(Tag.name, Media.id, Media.revision, media_tag.c.added_at)
            .join(media_tag, media_tag.c.media_id == Media.id)
            .join(Tag, Tag.id == media_tag.c.tag_id)
            .join(Namespace, Namespace.id == Tag.namespace_id)
            .where(Namespace.name == ALBUM_NAMESPACE, visible_media)
            .order_by(Tag.name, media_tag.c.added_at, Media.original_filename, Media.id)
        )
        with self._database.session() as session:
            rows = session.execute(links).all()
            hidden_ids = self._hidden_album_media(session, hidden_filters, visible_media)

        albums = []
        for name, album_rows in groupby(rows, key=lambda row: row[0]):
            album_media = list(album_rows)
            if any(media_id in hidden_ids for _name, media_id, _revision, _added_at in album_media):
                continue

            _name, cover_id, cover_revision, _added_at = album_media[0]
            last_changed_at = max(added_at for _name, _id, _revision, added_at in album_media)
            albums.append(AlbumSummary(name, len(album_media), cover_id, cover_revision, last_changed_at))

        return sorted(albums, key=lambda album: album.last_changed_at, reverse=True)

    def _hidden_album_media(
        self, session: Session, hidden_filters: tuple[str, ...], visible_media: ColumnElement[bool]
    ) -> set[int]:
        if not hidden_filters:
            return set()

        namespace_names = self._namespace_names()
        hidden = or_(*(matching_condition(parse_query(term, namespace_names)) for term in hidden_filters))
        album_media_ids = (
            select(media_tag.c.media_id)
            .join(Tag, Tag.id == media_tag.c.tag_id)
            .join(Namespace, Namespace.id == Tag.namespace_id)
            .where(Namespace.name == ALBUM_NAMESPACE)
        )
        return set(session.scalars(select(Media.id).where(Media.id.in_(album_media_ids), hidden, visible_media)))

    def _filtered(self, query: str, hidden_filters: tuple[str, ...]) -> FilteredQuery:
        namespace_names = self._namespace_names()
        tree = parse_query(query, namespace_names)
        return apply_hidden_filters(query, tree, list(hidden_filters), namespace_names)


def _of_media(media: MediaKinds) -> ColumnElement[bool]:
    return true() if media == MediaKinds.ALL else Media.media_kind == media.value


def _album_links(album: str) -> Subquery:
    """The media of an album and when the album tag was put on each."""
    return (
        select(media_tag.c.media_id, media_tag.c.added_at)
        .join(Tag, Tag.id == media_tag.c.tag_id)
        .join(Namespace, Namespace.id == Tag.namespace_id)
        .where(Namespace.name == ALBUM_NAMESPACE, Tag.name == normalized_tag_name(album))
        .subquery()
    )


def _in_album(statement: Select, links: Subquery | None) -> Select:
    return statement if links is None else statement.join(links, links.c.media_id == Media.id)


def _sort_keys(sort: Sort, seed: int | None, links: Subquery | None) -> list[ColumnElement]:
    """What the media are ordered by, before their id."""
    if sort == Sort.ALBUM:
        if links is None:
            raise _invalid("query.sort", "sort=album needs an album: the order is when images were added to it.")

        return [links.c.added_at, Media.original_filename]

    if sort == Sort.RANDOM:
        if seed is None:
            raise _invalid("query.seed", "Required for sort=random; the same seed gives the same order.")

        return [func.shuffled_position(seed, Media.id)]  # registered on every database connection

    return [Media.taken_at if sort == Sort.TAKEN_AT else Media.imported_at]


def _after(keys: list[ColumnElement], last: list[object], last_id: int, descending: bool) -> ColumnElement[bool]:
    if len(keys) > 1:  # compared as one row value; only ascending (album order)
        return tuple_(*keys, Media.id) > tuple_(*[literal(value) for value in last], literal(last_id))

    [key], [last_value] = keys, last
    if descending:
        return or_(key < last_value, and_(key == last_value, Media.id < last_id))

    return or_(key > last_value, and_(key == last_value, Media.id > last_id))


def _encode_cursor(sort_values: list[object], media_id: int) -> str:
    """base64url JSON {"v": <last sort value>, "id": <last id>}, as the API documents it; for album order "v" is
    [<when it was added>, <file name>]."""
    values = [value.isoformat() if isinstance(value, datetime) else value for value in sort_values]
    text = json.dumps({"v": values[0] if len(values) == 1 else values, "id": media_id}, separators=(",", ":"))
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def _decode_cursor(cursor: str, sort: Sort) -> tuple[list[object], int]:
    try:
        content = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        media_id = content["id"]
        value = content["v"]
        if not isinstance(media_id, int):
            raise ValueError

        if sort == Sort.RANDOM:
            if not isinstance(value, int):
                raise ValueError

            return [value], media_id

        if sort == Sort.ALBUM:
            added_at, file_name = value
            if not isinstance(file_name, str):
                raise ValueError

            return [datetime.fromisoformat(added_at), file_name], media_id

        return [datetime.fromisoformat(value)], media_id
    except (binascii.Error, ValueError, TypeError, KeyError, UnicodeDecodeError):
        raise _invalid("query.cursor", "Use next_cursor of the previous page, with the same sort.") from None


def _invalid(field: str, message: str) -> Problem:
    return Problem(
        422,
        "validation_error",
        "Invalid request",
        "The request body or parameters are invalid.",
        errors=[{"field": field, "message": message}],
    )
