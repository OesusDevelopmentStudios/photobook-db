# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

"""Bringing photos and videos into the library: what a file is, whether it is new, and storing it with its tags."""

from photobook_db.services.importer._inspection import (
    SUPPORTED_TYPES,
    FileType,
    MediaFacts,
    file_sha256,
    inspect_image,
    sniffed_video_type,
    video_facts,
)
from photobook_db.services.importer._service import (
    DEFAULT_MAX_UPLOAD_BYTES,
    DEFAULT_MAX_VIDEO_UPLOAD_BYTES,
    MAX_UPLOAD_BYTES_SETTING,
    MAX_VIDEO_UPLOAD_BYTES_SETTING,
    UPLOAD_LIMIT_RANGE,
    VIDEO_UPLOAD_LIMIT_RANGE,
    ImporterService,
    UploadMetadata,
    check_upload_limit,
    check_video_upload_limit,
    file_too_large,
)

__all__ = [
    "DEFAULT_MAX_UPLOAD_BYTES",
    "DEFAULT_MAX_VIDEO_UPLOAD_BYTES",
    "MAX_UPLOAD_BYTES_SETTING",
    "MAX_VIDEO_UPLOAD_BYTES_SETTING",
    "SUPPORTED_TYPES",
    "UPLOAD_LIMIT_RANGE",
    "VIDEO_UPLOAD_LIMIT_RANGE",
    "FileType",
    "ImporterService",
    "MediaFacts",
    "UploadMetadata",
    "check_upload_limit",
    "check_video_upload_limit",
    "file_sha256",
    "file_too_large",
    "inspect_image",
    "sniffed_video_type",
    "video_facts",
]
