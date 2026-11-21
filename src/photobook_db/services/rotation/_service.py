# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

import io

from PIL import Image

from photobook_db.services.access import Restrictions
from photobook_db.services.images import ImageRecord, ImagesService
from photobook_db.services.storage import StorageService
from photobook_db.services.thumbnails import ThumbnailsService
from photobook_db.utils import image_limits  # noqa: F401  (allows the large JPEGs the importer accepts)
from photobook_db.utils.exif_orientation import read_orientation, turned_orientation, with_orientation
from photobook_db.utils.problems import Problem

ROTATIONS = (90, 180, 270)  # clockwise degrees

# JPEG and TIFF keep the turn in their EXIF orientation, so their image data stays as it is; the desktop app
# applies that tag for these two only. PNG and BMP are lossless, so their pixels can be turned and saved again.
_ORIENTATION_TAG_TYPES = {"image/jpeg", "image/tiff"}
_PIXEL_TYPES = {"image/png": "PNG", "image/bmp": "BMP"}

# Pillow's turns are counter-clockwise.
_TRANSPOSE_CLOCKWISE = {
    90: Image.Transpose.ROTATE_270,
    180: Image.Transpose.ROTATE_180,
    270: Image.Transpose.ROTATE_90,
}

# What a saved PNG or BMP keeps besides its pixels
_KEPT_INFO = ("icc_profile", "dpi", "transparency")


class RotationService:
    def __init__(self, images: ImagesService, storage: StorageService, thumbnails: ThumbnailsService) -> None:
        self._images = images
        self._storage = storage
        self._thumbnails = thumbnails

    def rotate(self, image_id: int, clockwise_degrees: int, restrictions: Restrictions) -> ImageRecord:
        """Turns the image clockwise from how it is shown now, in its file; then its size, the next revision and
        new thumbnails. Raises 404 not_found, also for an image the user is restricted from, and 422
        rotation_not_supported for a type that cannot be turned without losing quality."""
        if clockwise_degrees not in ROTATIONS:
            raise ValueError(f"Not a quarter turn: {clockwise_degrees}")

        # Read and written under the storage's lock: another turn, or a move after a tag change, waits.
        with self._storage.changing_files():
            record = self._images.get_visible_image(image_id, restrictions)
            file_path = self._storage.absolute_path(record.storage_path)
            content = _turned_file(file_path.read_bytes(), record.mime_type, clockwise_degrees)
            self._storage.replace_library_file(record.storage_path, content)

            quarter_turn = clockwise_degrees != 180
            width, height = (record.height, record.width) if quarter_turn else (record.width, record.height)
            self._thumbnails.create_thumbnails(file_path, record.sha256, record.revision + 1)
            changed = self._images.record_file_change(image_id, width, height, len(content))
            self._thumbnails.delete_thumbnails(record.sha256, record.revision)
            return changed


def _turned_file(content: bytes, mime_type: str, clockwise_degrees: int) -> bytes:
    if mime_type in _ORIENTATION_TAG_TYPES:
        try:
            return with_orientation(content, turned_orientation(read_orientation(content), clockwise_degrees))
        except ValueError:
            raise _not_supported("Its EXIF data cannot be changed.") from None

    if mime_type in _PIXEL_TYPES:
        return _turned_pixels(content, _PIXEL_TYPES[mime_type], clockwise_degrees)

    raise _not_supported("This type of image cannot be turned without losing quality.")


def _turned_pixels(content: bytes, image_format: str, clockwise_degrees: int) -> bytes:
    with Image.open(io.BytesIO(content)) as image:
        kept = {key: image.info[key] for key in _KEPT_INFO if key in image.info}
        turned = image.transpose(_TRANSPOSE_CLOCKWISE[clockwise_degrees])

    output = io.BytesIO()
    turned.save(output, format=image_format, **kept)
    return output.getvalue()


def _not_supported(detail: str) -> Problem:
    return Problem(422, "rotation_not_supported", "The image cannot be turned", detail)
