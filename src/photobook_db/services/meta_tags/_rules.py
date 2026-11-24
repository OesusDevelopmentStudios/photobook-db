# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

from photobook_db.services.tags import META_NAMESPACE, TagName

# The file type's tag, by MIME type
_FILE_TYPES = {
    "image/jpeg": "jpeg",
    "image/png": "png",
    "image/webp": "webp",
    "image/gif": "gif",
    "image/tiff": "tiff",
    "image/bmp": "bmp",
    "image/jp2": "jpeg2000",
    "image/avif": "avif",
}

# A video's file type tag is its container's name.
VIDEO_CONTAINERS = ("mp4", "mov", "webm", "mkv", "avi")
_LONG_VIDEO_MS = 5 * 60 * 1000  # over five minutes

# Sizes as boorus name them: either side may be the long one.
_LOW_RES_MAX_SIDE = 500
_HI_RES_SIZE = (1600, 1200)
_ABSURD_RES_SIZE = (3200, 2400)


def meta_tags_for(mime_type: str, width: int, height: int, animated: bool) -> list[TagName]:
    """The kind of picture, its file type and its size class. A rotation swaps width and height, which changes
    none of them."""
    names = ["animated" if animated else "still_image"]
    file_type = _FILE_TYPES.get(mime_type)
    if file_type is not None:
        names.append(file_type)

    size_class = _size_class(width, height)
    if size_class is not None:
        names.append(size_class)

    return [TagName(META_NAMESPACE, name) for name in names]


def video_meta_tags(container: str, width: int, height: int, has_sound: bool, duration_ms: int) -> list[TagName]:
    """A video's kind, container, size class (as for images, by the size it is shown at), whether it has sound,
    and whether it is long."""
    names = ["video"]
    if container in VIDEO_CONTAINERS:
        names.append(container)

    size_class = _size_class(width, height)
    if size_class is not None:
        names.append(size_class)

    names.append("sound" if has_sound else "no_sound")
    if duration_ms > _LONG_VIDEO_MS:
        names.append("long_video")

    return [TagName(META_NAMESPACE, name) for name in names]


def _size_class(width: int, height: int) -> str | None:
    if _at_least(width, height, _ABSURD_RES_SIZE):
        return "absurd_res"

    if _at_least(width, height, _HI_RES_SIZE):
        return "hi_res"

    if max(width, height) <= _LOW_RES_MAX_SIDE:
        return "low_res"

    return None


def _at_least(width: int, height: int, size: tuple[int, int]) -> bool:
    long_side, short_side = max(width, height), min(width, height)
    return long_side >= size[0] and short_side >= size[1]
