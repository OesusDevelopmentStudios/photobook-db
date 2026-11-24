# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

import shutil
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path, PurePosixPath

from photobook_db.services.access import Restrictions
from photobook_db.services.images import ImageRecord, ImagesService, NewImage, duplicate_image
from photobook_db.services.importer._inspection import (
    ImageFacts,
    file_sha256,
    inspect_image,
    sniffed_video_type,
    video_facts,
    videos_not_supported,
)
from photobook_db.services.media_probe import MediaProbe
from photobook_db.services.meta_tags import meta_tags_for, video_meta_tags
from photobook_db.services.settings import SettingsService
from photobook_db.services.storage import StorageService, file_name
from photobook_db.services.tags import TagName, TagsService
from photobook_db.services.thumbnails import THUMBNAIL_SIZES, ThumbnailsService
from photobook_db.utils.problems import Problem

MAX_UPLOAD_BYTES_SETTING = "max_upload_bytes"
DEFAULT_MAX_UPLOAD_BYTES = 200 * 1024 * 1024
UPLOAD_LIMIT_RANGE = (1024 * 1024, 10 * 1024 * 1024 * 1024)  # what an admin may set: 1 MiB to 10 GiB
POSTER_LATEST_MS = 10_000  # a video's poster frame is at most this far in


@dataclass(frozen=True)
class UploadMetadata:
    original_filename: str
    file_mtime: datetime | None = None  # the original file's modification time, as the client saw it
    client_sha256: str | None = None  # what the client computed, to detect damage on the way
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class TakenAt:
    time: datetime  # local wall-clock time
    source: str  # "exif", "container", "file_mtime" or "import_time"
    utc_offset_minutes: int | None


def local_now() -> datetime:
    """The server's local wall-clock time, the way a photo's own date is recorded."""
    return datetime.now().replace(microsecond=0)


class ImporterService:
    def __init__(
        self,
        images: ImagesService,
        tags: TagsService,
        storage: StorageService,
        thumbnails: ThumbnailsService,
        settings: SettingsService,
        media_probe: MediaProbe,
        now: Callable[[], datetime] = local_now,
    ) -> None:
        self._images = images
        self._tags = tags
        self._storage = storage
        self._thumbnails = thumbnails
        self._settings = settings
        self._media_probe = media_probe
        self._now = now

    def max_upload_bytes(self) -> int:
        stored_limit = self._settings.get(MAX_UPLOAD_BYTES_SETTING)
        return DEFAULT_MAX_UPLOAD_BYTES if stored_limit is None else stored_limit

    def set_max_upload_bytes(self, limit: int) -> None:
        check_upload_limit(limit)
        self._settings.set(MAX_UPLOAD_BYTES_SETTING, limit)

    def new_incoming_path(self) -> Path:
        """Where to write an upload before passing it to import_image()."""
        return self._storage.new_incoming_path()

    def import_image(
        self, incoming: Path, metadata: UploadMetadata, user_id: int | None, restrictions: Restrictions
    ) -> ImageRecord:
        """Takes over `incoming`: it ends up in the library or is deleted. Raises 409 duplicate_image,
        403 tag_restricted, 415 unsupported_media_type, 422 checksum_mismatch or 422 invalid_tag; nothing is
        stored then. The server adds the file's meta tags; meta tags in the metadata raise 422 tag_read_only."""
        sha256, tags, facts, taken_at = self._checked_upload(incoming, metadata, restrictions)
        library_path = self._storage.path_for(
            tags, taken_at.time, file_name(metadata.original_filename, sha256, facts.type.extension)
        )
        stored_path = self._storage.store_incoming(incoming, library_path)
        new_image = self._new_image(metadata, sha256, facts, taken_at, stored_path, user_id)
        try:
            return self._images.create_image(new_image, tags)
        except Exception:
            self._storage.delete_library_file(stored_path)
            if self._images.find_id_by_sha256(sha256) is None:  # not another upload of the same file
                self._thumbnails.delete_thumbnails(sha256)

            raise

    def import_copy(
        self, source: Path, metadata: UploadMetadata, user_id: int | None, restrictions: Restrictions
    ) -> ImageRecord:
        """Imports a copy of `source` and leaves the file itself alone. Raises like import_image()."""
        sha256 = file_sha256(source)
        self._check_not_duplicate(sha256, restrictions)  # before copying: a repeated import is mostly duplicates
        incoming = self.new_incoming_path()
        try:
            shutil.copyfile(source, incoming)
        except Exception:
            incoming.unlink(missing_ok=True)
            raise

        # The checksum finds a source that changed while it was copied.
        return self.import_image(incoming, replace(metadata, client_sha256=sha256), user_id, restrictions)

    def make_thumbnails(self, path: Path, sha256: str, revision: int = 0, video_duration_ms: int | None = None) -> None:
        """The grid's thumbnails: of the image, or of a video's poster frame (video_duration_ms is a video's duration;
        None for an image). Raises 415 if the picture cannot be decoded."""
        if video_duration_ms is None:
            self._thumbnails.create_thumbnails(path, sha256, revision)
            return

        frame = self._poster_frame(path, video_duration_ms)
        if frame is None:
            raise Problem(
                415,
                "unsupported_media_type",
                "The video cannot be decoded",
                "FFmpeg reads the file but cannot decode its pictures.",
            )

        self._thumbnails.create_thumbnails_from_frame(frame, sha256, revision)

    def _poster_frame(self, path: Path, duration_ms: int) -> bytes | None:
        """At a tenth of the video, at most 10 s in, past a black start or a fade; else its first picture."""
        largest = max(THUMBNAIL_SIZES)
        frame = self._media_probe.frame(path, min(duration_ms // 10, POSTER_LATEST_MS), largest)
        return frame if frame is not None else self._media_probe.frame(path, 0, largest)

    def _inspect(self, path: Path) -> ImageFacts:
        """An image, as Pillow reads it; else a video, as FFmpeg does. Raises 415 unsupported_media_type."""
        try:
            return inspect_image(path)
        except Problem as not_an_image:
            if not_an_image.code != "unsupported_media_type":
                raise

            video = self._media_probe.probe(path)
            if video is not None:
                return video_facts(video)

            detected = None if self._media_probe.available() else sniffed_video_type(path)
            if detected is not None:
                raise videos_not_supported(detected) from None

            raise

    def _checked_upload(
        self, incoming: Path, metadata: UploadMetadata, restrictions: Restrictions
    ) -> tuple[str, list[TagName], ImageFacts, TakenAt]:
        """Everything that can refuse an upload, before anything is stored."""
        try:
            sha256 = file_sha256(incoming)
            _check_client_sha256(metadata.client_sha256, sha256)
            self._check_not_duplicate(sha256, restrictions)
            facts = self._inspect(incoming)
            tags = self._tags.resolve_tags(list(metadata.tags))
            tags += _meta_tags(facts)
            taken_at = self._taken_at(metadata, facts)
            restrictions.check_tags_allowed(tags, taken_at.time)
        except Exception:
            incoming.unlink(missing_ok=True)
            raise

        try:  # decodes the whole image, or a video's frame: finds damaged data
            self.make_thumbnails(incoming, sha256, video_duration_ms=facts.video.duration_ms if facts.video else None)
        except Exception:
            incoming.unlink(missing_ok=True)
            self._thumbnails.delete_thumbnails(sha256)
            raise

        return sha256, tags, facts, taken_at

    def _check_not_duplicate(self, sha256: str, restrictions: Restrictions) -> None:
        existing_image_id = self._images.find_id_by_sha256(sha256)
        if existing_image_id is not None:
            # The user has the file already; confirming a copy exists reveals little, but not which image.
            visible = self._images.is_visible(existing_image_id, restrictions)
            raise duplicate_image(sha256, existing_image_id if visible else None)

    def _new_image(
        self,
        metadata: UploadMetadata,
        sha256: str,
        facts: ImageFacts,
        taken_at: TakenAt,
        stored_path: PurePosixPath,
        user_id: int | None,
    ) -> NewImage:
        return NewImage(
            sha256=sha256,
            storage_path=str(stored_path),
            original_filename=metadata.original_filename,
            mime_type=facts.type.mime_type,
            width=facts.width,
            height=facts.height,
            file_size=self._storage.absolute_path(stored_path).stat().st_size,
            taken_at=taken_at.time,
            taken_at_source=taken_at.source,
            taken_at_utc_offset_minutes=taken_at.utc_offset_minutes,
            imported_by_user_id=user_id,
            **_video_fields(facts),
        )

    def _taken_at(self, metadata: UploadMetadata, facts: ImageFacts) -> TakenAt:
        """From EXIF or the video itself; else the original file's modification time; else now. The source is
        recorded too."""
        if facts.taken_at is not None:
            source = "container" if facts.video is not None else "exif"
            return TakenAt(facts.taken_at, source, facts.taken_at_utc_offset_minutes)

        if metadata.file_mtime is not None:
            return TakenAt(metadata.file_mtime.replace(tzinfo=None), "file_mtime", None)

        return TakenAt(self._now(), "import_time", None)


def _meta_tags(facts: ImageFacts) -> list[TagName]:
    video = facts.video
    if video is None:
        return meta_tags_for(facts.type.mime_type, facts.width, facts.height, facts.animated)

    return video_meta_tags(video.container, video.width, video.height, video.audio_codec is not None, video.duration_ms)


def _video_fields(facts: ImageFacts) -> dict[str, object]:
    video = facts.video
    if video is None:
        return {}

    return {
        "media_kind": "video",
        "duration_ms": video.duration_ms,
        "video_codec": video.video_codec,
        "audio_codec": video.audio_codec,
        "bitrate": video.bitrate,
        "frame_rate": video.frame_rate,
    }


def check_upload_limit(limit: int) -> None:
    """Raises 422 validation_error if an admin may not set it."""
    smallest, largest = UPLOAD_LIMIT_RANGE
    if not smallest <= limit <= largest:
        raise Problem(
            422, "validation_error", "Invalid upload limit", f"The limit must be from {smallest} to {largest} bytes."
        )


def _check_client_sha256(client_sha256: str | None, sha256: str) -> None:
    if client_sha256 is not None and client_sha256.lower() != sha256:
        raise Problem(
            422,
            "checksum_mismatch",
            "File damaged on the way",
            "The file's SHA-256 differs from client_sha256; send it again.",
        )
