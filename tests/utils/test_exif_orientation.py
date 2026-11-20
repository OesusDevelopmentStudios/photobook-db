# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

"""Changing the EXIF orientation of JPEG and TIFF files without touching their image data. Pillow reads the
results, as the server's thumbnails and any other program would."""

import io

import pytest
from PIL import Image, ImageOps

from pictorio_db.utils.exif_orientation import (
    ORIENTATION_TAG,
    read_orientation,
    turned_orientation,
    with_orientation,
)

MAKE_TAG = 0x010F


def quartered_image() -> Image.Image:
    """Four differently coloured quarters, so that every turn and mirror shows."""
    image = Image.new("RGB", (64, 32), "red")
    image.paste("green", (32, 0, 64, 16))
    image.paste("yellow", (0, 16, 32, 32))
    image.paste("blue", (32, 16, 64, 32))
    return image


def encoded(image_format: str, exif: dict[int, object] | None = None) -> bytes:
    output = io.BytesIO()
    options: dict[str, object] = {}
    if exif is not None:
        tags = Image.Exif()
        tags.update(exif)
        options["exif"] = tags.tobytes()
    quartered_image().save(output, format=image_format, **options)
    return output.getvalue()


def opened(data: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(data))
    image.load()
    return image


def exif_of(data: bytes) -> Image.Exif:
    """Read before the image is loaded, as the server does: Pillow forgets a TIFF's tags once it loaded it."""
    return Image.open(io.BytesIO(data)).getexif()


def image_data(data: bytes) -> bytes:
    """A JPEG's compressed image data: everything from the start of the scan"""
    return data[data.index(b"\xff\xda"):]


def shown(image: Image.Image, orientation: int) -> Image.Image:
    """As Pillow shows the image with this orientation"""
    image = image.copy()
    image.getexif()[ORIENTATION_TAG] = orientation
    return ImageOps.exif_transpose(image)


@pytest.mark.parametrize("orientation", range(1, 9))
@pytest.mark.parametrize("degrees", [90, 180, 270])
def test_turns_any_orientation_as_shown(orientation: int, degrees: int) -> None:
    turned = shown(quartered_image(), turned_orientation(orientation, degrees))

    assert turned.tobytes() == shown(quartered_image(), orientation).rotate(-degrees, expand=True).tobytes()


def test_changes_the_tag_of_a_jpeg_in_place() -> None:
    original = encoded("JPEG", {ORIENTATION_TAG: 1, MAKE_TAG: "SampleCam"})

    changed = with_orientation(original, 6)

    assert read_orientation(changed) == 6
    assert len(changed) == len(original)
    assert image_data(changed) == image_data(original)
    exif = exif_of(changed)
    assert exif[ORIENTATION_TAG] == 6
    assert exif[MAKE_TAG] == "SampleCam"


def test_adds_the_tag_to_a_jpeg_whose_exif_has_none() -> None:
    original = encoded("JPEG", {MAKE_TAG: "SampleCam"})

    changed = with_orientation(original, 8)

    assert read_orientation(changed) == 8
    assert image_data(changed) == image_data(original)
    exif = exif_of(changed)
    assert exif[ORIENTATION_TAG] == 8
    assert exif[MAKE_TAG] == "SampleCam"


def test_gives_a_jpeg_without_exif_its_first() -> None:
    original = encoded("JPEG")
    assert read_orientation(original) == 1

    changed = with_orientation(original, 3)
    changed_again = with_orientation(changed, 5)  # now in place, in the new segment

    assert read_orientation(changed) == 3
    assert exif_of(changed_again)[ORIENTATION_TAG] == 5
    assert image_data(changed_again) == image_data(original)


@pytest.mark.parametrize("exif", [None, {ORIENTATION_TAG: 1}])
def test_changes_a_tiff(exif: dict[int, object] | None) -> None:
    original = encoded("TIFF", exif)

    changed = with_orientation(original, 7)

    assert read_orientation(changed) == 7
    assert exif_of(changed)[ORIENTATION_TAG] == 7
    # Pillow applies a TIFF's orientation when it loads one: the same pixels, shown as 7 says
    assert opened(changed).tobytes() == shown(quartered_image(), 7).tobytes()


def test_refuses_other_formats() -> None:
    png = encoded("PNG")

    with pytest.raises(ValueError):
        with_orientation(png, 6)
    with pytest.raises(ValueError):
        read_orientation(png)


def test_refuses_damaged_exif() -> None:
    damaged = encoded("JPEG", {ORIENTATION_TAG: 1})
    start = damaged.index(b"Exif\x00\x00") + 6
    damaged = damaged[:start] + b"XX" + damaged[start + 2:]  # no TIFF byte order any more

    with pytest.raises(ValueError):
        with_orientation(damaged, 6)


def test_refuses_a_value_that_is_not_an_orientation() -> None:
    with pytest.raises(ValueError):
        with_orientation(encoded("JPEG"), 9)
