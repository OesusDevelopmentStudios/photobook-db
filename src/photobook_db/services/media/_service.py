# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePosixPath

from sqlalchemy import Select, and_, bindparam, delete, func, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from photobook_db.db import Database
from photobook_db.db.models import Media, Namespace, Tag, media_tag
from photobook_db.services.access import Restrictions
from photobook_db.services.storage import StorageService
from photobook_db.services.tags import META_NAMESPACE, TagName, TagsService
from photobook_db.utils.problems import Problem
from photobook_db.utils.time import utc_now


@dataclass(frozen=True)
class NewMedia:
    sha256: str
    storage_path: str  # relative to the library folder
    original_filename: str
    mime_type: str
    width: int
    height: int
    file_size: int
    taken_at: datetime  # local wall-clock time
    taken_at_source: str  # "exif", "container" (a video's own), "file_mtime" or "import_time"
    taken_at_utc_offset_minutes: int | None
    imported_by_user_id: int | None
    media_kind: str = "image"  # or "video"; the rest only for videos
    duration_ms: int | None = None
    video_codec: str | None = None
    audio_codec: str | None = None
    bitrate: int | None = None
    frame_rate: float | None = None


@dataclass(frozen=True)
class MediaRecord:
    id: int
    sha256: str
    storage_path: str
    original_filename: str
    mime_type: str
    width: int
    height: int
    file_size: int
    taken_at: datetime
    taken_at_source: str
    taken_at_utc_offset_minutes: int | None
    imported_at: datetime  # UTC
    updated_at: datetime  # UTC
    imported_by_user_id: int | None
    tags: list[TagName]  # sorted by namespace, then name
    revision: int = 0  # how often the file was changed since it came in
    media_kind: str = "image"  # or "video"; the rest only for videos
    duration_ms: int | None = None
    video_codec: str | None = None
    audio_codec: str | None = None  # None: no sound
    bitrate: int | None = None
    frame_rate: float | None = None


# Checks one media item's tags before and after a change, and its date; raises a Problem to cancel the whole change.
TagChangeCheck = Callable[[list[TagName], list[TagName], datetime], None]

_logger = logging.getLogger(__name__)


class MediaService:
    """When a tag change affects a media item's folder, its file is moved."""

    def __init__(self, database: Database, tags: TagsService, storage: StorageService) -> None:
        self._database = database
        self._tags = tags
        self._storage = storage

    def get_media(self, media_id: int) -> MediaRecord:
        with self._database.session() as session:
            media = session.get(Media, media_id)
            if media is None:
                raise Problem(404, "not_found", "Image not found")

            return _record(session, media)

    def get_visible_media(self, media_id: int, restrictions: Restrictions) -> MediaRecord:
        """404 not_found also for a media item the user is restricted from: it behaves as if it did not exist."""
        if not self.is_visible(media_id, restrictions):
            raise Problem(404, "not_found", "Image not found")

        return self.get_media(media_id)

    def is_visible(self, media_id: int, restrictions: Restrictions) -> bool:
        with self._database.session() as session:
            statement = select(Media.id).where(Media.id == media_id, restrictions.visible_media_filter())
            return session.scalar(statement) is not None

    def get_media_list(self, media_ids: list[int]) -> list[MediaRecord]:
        """In the order of `media_ids`; ids that do not exist are left out. Tags are read in one query."""
        with self._database.session() as session:
            media_rows = {media.id: media for media in session.scalars(select(Media).where(Media.id.in_(media_ids)))}
            tags = _tags_of(session, list(media_rows))
            return [
                _record_with_tags(media_rows[media_id], tags.get(media_id, []))
                for media_id in media_ids
                if media_id in media_rows
            ]

    def change_tags(
        self, media_id: int, add_tags: list[str], remove_tags: list[str], restrictions: Restrictions
    ) -> MediaRecord:
        """Removes, then adds tags (unknown tags are created). In a single-value namespace an added tag replaces
        the media item's current one. If its folder changes, the file is moved before this returns.
        Raises 404 not_found, 403 tag_restricted or 422 invalid_tag."""
        self.change_tags_of_many([media_id], add_tags, remove_tags, restrictions)
        return self.get_media(media_id)

    def change_tags_of_many(
        self, media_ids: list[int], add_tags: list[str], remove_tags: list[str], restrictions: Restrictions
    ) -> int:
        """The same change for several media, in one transaction: if anything fails, nothing changes. Files
        are moved after the commit. Media the user is restricted from count as unknown. Raises 404 not_found
        without saying which id, 403 tag_restricted if a media item would become restricted, 422 invalid_tag, or
        422 tag_read_only for a tag only the server sets."""
        additions = self._tags.resolve_tags(add_tags)
        removals = {self._tags.parse_tag(text) for text in remove_tags}
        self._tags.check_user_editable(removals)
        unique_ids = list(dict.fromkeys(media_ids))
        with self._database.session() as session:
            media_rows = session.scalars(
                select(Media).where(Media.id.in_(unique_ids), restrictions.visible_media_filter())
            ).all()
            if len(media_rows) != len(unique_ids):
                raise Problem(404, "not_found", "Image not found", "One of the images does not exist.")

            def check(_old_tags: list[TagName], new_tags: list[TagName], taken_at: datetime) -> None:
                restrictions.check_tags_allowed(new_tags, taken_at)

            changed_ids = self._change_tags(session, media_rows, unique_ids, additions, removals, check)
            session.commit()

        self._move_files_to_layout(changed_ids)
        return len(unique_ids)

    def set_meta_tags(self, media_id: int, meta_tags: list[TagName]) -> None:
        """The server's own tags of a media item, read from its file, instead of those it had. No restrictions apply:
        they describe the file, and no user asked for them. Raises 404 not_found."""
        with self._database.session() as session:
            media = session.get(Media, media_id)
            if media is None:
                raise Problem(404, "not_found", "Image not found")

            current = _tags_of(session, [media_id]).get(media_id, [])
            outdated = {tag for tag in current if tag.namespace == META_NAMESPACE and tag not in meta_tags}
            self._change_tags(session, [media], [media_id], meta_tags, outdated, _no_check)
            session.commit()

    def ids_without_meta_tags(self) -> list[int]:
        """Media stored before the server set meta tags on import."""
        has_meta_tag = (
            select(media_tag.c.media_id)
            .join(Tag, Tag.id == media_tag.c.tag_id)
            .join(Namespace, Namespace.id == Tag.namespace_id)
            .where(Namespace.name == META_NAMESPACE, media_tag.c.media_id == Media.id)
            .exists()
        )
        with self._database.session() as session:
            return list(session.scalars(select(Media.id).where(~has_meta_tag).order_by(Media.id)))

    def replace_tag(self, old_tag: TagName, new_tag: TagName | None) -> list[int]:
        """For admins: every media item with old_tag gets new_tag instead (a rename, or a merge if new_tag exists), or
        loses it if new_tag is None; then old_tag is deleted. In one transaction. Files are not moved: returns the
        ids of the changed media, for a relayout. The new tag keeps the time the old one was added (an album's
        order); a media item that had both keeps the new one's. Raises 404 not_found."""
        with self._database.session() as session:
            old_tag_id = session.scalar(
                select(Tag.id)
                .join(Namespace, Tag.namespace_id == Namespace.id)
                .where(Namespace.name == old_tag.namespace, Tag.name == old_tag.name)
            )
            if old_tag_id is None:
                raise Problem(404, "not_found", "Tag not found")

            additions = [] if new_tag is None else [new_tag]
            # The new tag exists afterwards, even if no media item had the old one.
            new_tag_ids = _tag_ids(session, additions)
            tagged_ids = select(media_tag.c.media_id).where(media_tag.c.tag_id == old_tag_id)
            media_rows = session.scalars(select(Media).where(Media.id.in_(tagged_ids))).all()
            added_times = _added_times(session, old_tag_id)
            had_new_tag = set(_added_times(session, new_tag_ids[0])) if new_tag_ids else set()
            changed_ids = self._change_tags(session, media_rows, tagged_ids, additions, {old_tag}, _no_check)
            if new_tag_ids:
                kept = [
                    {"link_media_id": media_id, "link_tag_id": new_tag_ids[0], "kept_added_at": added_at}
                    for media_id, added_at in added_times.items()
                    if media_id not in had_new_tag
                ]
                _set_added_times(session, kept)

            session.execute(delete(Tag).where(Tag.id == old_tag_id))
            session.commit()

        return changed_ids

    def delete_namespace_tags(self, namespace: str) -> list[int]:
        """For admins removing a namespace: its tags come off every media item and are deleted, in one transaction.
        Returns the ids of the changed media."""
        with self._database.session() as session:
            namespace_tags = (
                select(Tag.id, Tag.name)
                .join(Namespace, Tag.namespace_id == Namespace.id)
                .where(Namespace.name == namespace)
            )
            rows = session.execute(namespace_tags).all()
            tag_ids = [tag_id for tag_id, _name in rows]
            tagged_ids = select(media_tag.c.media_id).where(media_tag.c.tag_id.in_(tag_ids)).distinct()
            media_rows = session.scalars(select(Media).where(Media.id.in_(tagged_ids))).all()
            removals = {TagName(namespace, name) for _tag_id, name in rows}
            changed_ids = self._change_tags(session, media_rows, tagged_ids, [], removals, _no_check)
            session.execute(delete(Tag).where(Tag.id.in_(tag_ids)))
            session.commit()

        return changed_ids

    def media_count(self) -> int:
        with self._database.session() as session:
            return session.scalar(select(func.count()).select_from(Media)) or 0

    def all_media_ids(self) -> list[int]:
        with self._database.session() as session:
            return list(session.scalars(select(Media.id).order_by(Media.id)))

    def move_file_to_layout(self, media_id: int) -> bool:
        """Moves the media item's file to where its tags and the path template place it. False if it was there.
        Raises 404 not_found, or OSError if moving fails; the file then stays where the database says."""
        record = self.get_media(media_id)
        current_path = PurePosixPath(record.storage_path)
        return self._storage.move_to_layout(record.id, current_path, record.tags, record.taken_at) != current_path

    def record_file_change(self, media_id: int, width: int, height: int, file_size: int) -> MediaRecord:
        """After the media item's file was changed in place, e.g. turned: its new size, and the next revision. The
        sha256 stays the uploaded file's."""
        with self._database.session() as session:
            session.execute(
                update(Media)
                .where(Media.id == media_id)
                .values(
                    width=width,
                    height=height,
                    file_size=file_size,
                    revision=Media.revision + 1,
                    updated_at=utc_now(),
                )
            )
            session.commit()

        return self.get_media(media_id)

    def find_id_by_sha256(self, sha256: str) -> int | None:
        with self._database.session() as session:
            return session.scalar(select(Media.id).where(Media.sha256 == sha256))

    def create_media(self, new_media: NewMedia, tags: list[TagName]) -> MediaRecord:
        """The media item, its new tags and their links in one transaction. Raises 409 duplicate_image if a
        media item with the same SHA-256 exists, also when another upload was a moment faster."""
        now = utc_now()
        media = Media(**vars(new_media), imported_at=now, updated_at=now)
        with self._database.session() as session:
            session.add(media)
            try:
                session.flush()
            except IntegrityError:
                session.rollback()
                existing_image_id = self.find_id_by_sha256(new_media.sha256)
                if existing_image_id is None:
                    raise  # not a duplicate media item: the storage path is taken, a mistake of the caller

                raise duplicate_media(new_media.sha256, existing_image_id) from None

            for tag_id in _tag_ids(session, tags):
                session.execute(media_tag.insert().values(media_id=media.id, tag_id=tag_id))

            session.commit()
            return _record(session, media)

    def _change_tags(
        self,
        session: Session,
        media_rows: Sequence[Media],
        media_ids: list[int] | Select[tuple[int]],
        additions: list[TagName],
        removals: set[TagName],
        check: TagChangeCheck,
    ) -> list[int]:
        """Removes, then adds tags; in a single-value namespace an added tag replaces the current one. Does not
        commit. Returns the ids of the media whose tags changed."""
        single_value = {namespace.name for namespace in self._tags.list_namespaces() if namespace.single_value}
        current_tags = _tags_of(session, media_ids)
        changes: list[_TagChange] = []
        for media in media_rows:
            old_tags = current_tags.get(media.id, [])
            new_tags = _tags_after_change(old_tags, additions, removals, single_value)
            check(old_tags, new_tags, media.taken_at)
            removed = [tag for tag in old_tags if tag not in new_tags]
            change = _TagChange(media.id, removed, added=[tag for tag in new_tags if tag not in old_tags])
            if change.removed or change.added:
                changes.append(change)

        _save_tag_changes(session, changes)
        return [change.media_id for change in changes]

    def _move_files_to_layout(self, media_ids: list[int]) -> None:
        """After a tag change. A failed move leaves the file where the database says it is; it is logged, and
        the tag change stays."""
        for media_id in media_ids:
            try:
                self.move_file_to_layout(media_id)
            except OSError as error:
                _logger.error("Could not move the file of media %s after a tag change: %s", media_id, error)


def duplicate_media(sha256: str, existing_image_id: int | None) -> Problem:
    extra: dict[str, object] = {"sha256": sha256}
    if existing_image_id is not None:
        extra["existing_image_id"] = existing_image_id

    return Problem(
        409, "duplicate_image", "Image already exists", "An image with this SHA-256 is already in the library.",
        **extra,
    )


def _tags_after_change(
    current: list[TagName], additions: list[TagName], removals: set[TagName], single_value: set[str]
) -> list[TagName]:
    tags = [tag for tag in current if tag not in removals]
    for added in additions:
        if added.namespace in single_value:
            tags = [tag for tag in tags if tag.namespace != added.namespace]

        if added not in tags:
            tags.append(added)

    return tags


def _no_check(_old_tags: list[TagName], _new_tags: list[TagName], _taken_at: datetime) -> None:
    """Admins are never restricted."""


@dataclass(frozen=True)
class _TagChange:
    media_id: int
    removed: list[TagName]
    added: list[TagName]


def _save_tag_changes(session: Session, changes: list[_TagChange]) -> None:
    """In a few statements for all media, so that renaming a tag on thousands of media stays quick."""
    if not changes:
        return

    tags = list(dict.fromkeys(tag for change in changes for tag in [*change.removed, *change.added]))
    tag_ids = dict(zip(tags, _tag_ids(session, tags)))
    links = media_tag.c
    removed_links = [
        {"link_media_id": change.media_id, "link_tag_id": tag_ids[tag]} for change in changes for tag in change.removed
    ]
    if removed_links:
        is_link = and_(links.media_id == bindparam("link_media_id"), links.tag_id == bindparam("link_tag_id"))
        session.execute(delete(media_tag).where(is_link), removed_links)

    added_links = [{"media_id": change.media_id, "tag_id": tag_ids[tag]} for change in changes for tag in change.added]
    if added_links:
        session.execute(insert(media_tag), added_links)

    # The media table itself, not the Media class: an ORM update would check every loaded Media object.
    media_columns = Media.__table__.c
    session.execute(
        update(Media.__table__).where(media_columns.id == bindparam("changed_id")).values(updated_at=utc_now()),
        [{"changed_id": change.media_id} for change in changes],
    )


def _added_times(session: Session, tag_id: int) -> dict[int, datetime]:
    """When each media item got the tag, by media id."""
    links = media_tag.c
    return dict(session.execute(select(links.media_id, links.added_at).where(links.tag_id == tag_id)).all())


def _set_added_times(session: Session, times: list[dict[str, object]]) -> None:
    """Rows of link_media_id, link_tag_id and kept_added_at."""
    if times:
        links = media_tag.c
        is_link = and_(links.media_id == bindparam("link_media_id"), links.tag_id == bindparam("link_tag_id"))
        session.execute(update(media_tag).where(is_link).values(added_at=bindparam("kept_added_at")), times)


def _tag_ids(session: Session, tags: list[TagName]) -> list[int]:
    """The ids of the tags, creating the ones that do not exist yet."""
    namespace_ids = {name: namespace_id for name, namespace_id in session.execute(select(Namespace.name, Namespace.id))}
    tag_ids = []
    for tag_name in dict.fromkeys(tags):  # each tag once, in the given order
        namespace_id = namespace_ids[tag_name.namespace]
        tag = session.scalars(select(Tag).where(Tag.namespace_id == namespace_id, Tag.name == tag_name.name)).first()
        if tag is None:
            tag = Tag(namespace_id=namespace_id, name=tag_name.name)
            session.add(tag)
            session.flush()

        tag_ids.append(tag.id)

    return tag_ids


def _record(session: Session, media: Media) -> MediaRecord:
    return _record_with_tags(media, _tags_of(session, [media.id]).get(media.id, []))


def _tags_of(session: Session, media_ids: list[int] | Select[tuple[int]]) -> dict[int, list[TagName]]:
    """The tags of each media item, sorted by namespace, then name. A query for the ids avoids SQLite's limit on the
    number of values in one statement."""
    tag_rows = session.execute(
        select(media_tag.c.media_id, Namespace.name, Tag.name)
        .join(Tag, Tag.namespace_id == Namespace.id)
        .join(media_tag, media_tag.c.tag_id == Tag.id)
        .where(media_tag.c.media_id.in_(media_ids))
        .order_by(Namespace.name, Tag.name)
    )
    tags: dict[int, list[TagName]] = {}
    for media_id, namespace, name in tag_rows:
        tags.setdefault(media_id, []).append(TagName(namespace, name))

    return tags


def _record_with_tags(media: Media, tags: list[TagName]) -> MediaRecord:
    return MediaRecord(
        id=media.id,
        sha256=media.sha256,
        storage_path=media.storage_path,
        original_filename=media.original_filename,
        mime_type=media.mime_type,
        width=media.width,
        height=media.height,
        file_size=media.file_size,
        taken_at=media.taken_at,
        taken_at_source=media.taken_at_source,
        taken_at_utc_offset_minutes=media.taken_at_utc_offset_minutes,
        imported_at=media.imported_at,
        updated_at=media.updated_at,
        imported_by_user_id=media.imported_by_user_id,
        tags=tags,
        revision=media.revision,
        media_kind=media.media_kind,
        duration_ms=media.duration_ms,
        video_codec=media.video_codec,
        audio_codec=media.audio_codec,
        bitrate=media.bitrate,
        frame_rate=media.frame_rate,
    )
