# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 OesusDevelopmentStudios

"""The EXIF orientation of a JPEG or TIFF file, read and changed without touching anything else in it: the
image data stays byte for byte, so a rotation is saved without losing quality. (Pillow would encode a JPEG
again to save new metadata.)

Both formats keep the tag in a TIFF structure: a JPEG in its APP1 "Exif" segment, a TIFF in the file itself.
If the first directory (IFD0) has the tag, its value is overwritten in place. If not, a copy of IFD0 with
the tag added is appended and the header points to it: all other values are found by offsets from the
structure's start, which do not move. A JPEG without EXIF gets a small APP1 segment with only the tag."""

import struct
from typing import NamedTuple

ORIENTATION_TAG = 0x0112
_SHORT = 3  # TIFF field type of the tag's value
_EXIF_HEADER = b"Exif\x00\x00"
_JPEG_START = b"\xff\xd8"
_TIFF_HEADERS = (b"II*\x00", b"MM\x00*")
_MAX_SEGMENT_LENGTH = 0xFFFF


class _Orientation(NamedTuple):
    """What is done to the stored pixels to show them: mirrored left to right first, then turned clockwise."""

    mirrored: bool
    clockwise_degrees: int


_ORIENTATIONS = {
    1: _Orientation(False, 0),
    2: _Orientation(True, 0),
    3: _Orientation(False, 180),
    4: _Orientation(True, 180),
    5: _Orientation(True, 270),
    6: _Orientation(False, 90),
    7: _Orientation(True, 90),
    8: _Orientation(False, 270),
}
_VALUES = {orientation: value for value, orientation in _ORIENTATIONS.items()}


def turned_orientation(orientation: int, clockwise_degrees: int) -> int:
    """The orientation that shows the image turned clockwise by 90, 180 or 270 degrees more. An unknown value
    counts as 1, as viewers read it."""
    current = _ORIENTATIONS.get(orientation, _ORIENTATIONS[1])
    return _VALUES[_Orientation(current.mirrored, (current.clockwise_degrees + clockwise_degrees) % 360)]


def read_orientation(data: bytes) -> int:
    """1 to 8; 1 if the file has none. Raises ValueError for a file that is not a JPEG or TIFF."""
    tiff = _tiff_of(data)
    if tiff is None:
        return 1

    entry = _Tiff(tiff).orientation_entry()
    return entry.value if entry is not None else 1


def with_orientation(data: bytes, orientation: int) -> bytes:
    """The file with this orientation, 1 to 8. Raises ValueError for a file that is not a JPEG or TIFF, or whose
    EXIF data is damaged or would grow too large for its segment."""
    if orientation not in _ORIENTATIONS:
        raise ValueError(f"Not an EXIF orientation: {orientation}")

    if data.startswith(_TIFF_HEADERS):
        return _Tiff(data).with_orientation(orientation)

    if not data.startswith(_JPEG_START):
        raise ValueError("Neither a JPEG nor a TIFF file")

    segment = _exif_segment(data)
    if segment is None:
        return _with_new_exif_segment(data, orientation)

    start, end = segment
    tiff = _Tiff(data[start + 4 + len(_EXIF_HEADER):end]).with_orientation(orientation)
    payload = _EXIF_HEADER + tiff
    if len(payload) + 2 > _MAX_SEGMENT_LENGTH:
        raise ValueError("The EXIF data would not fit into its JPEG segment")

    return data[:start] + b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload + data[end:]


class _Entry(NamedTuple):
    offset: int  # of the 12-byte directory entry, from the TIFF structure's start
    value: int


class _Tiff:
    """A TIFF structure: a TIFF file, or the data of a JPEG's EXIF segment."""

    def __init__(self, data: bytes) -> None:
        if data[:4] not in _TIFF_HEADERS:
            raise ValueError("Damaged EXIF data: no TIFF header")

        self._data = data
        self._order = "<" if data[:2] == b"II" else ">"
        (self._ifd0,) = self._unpack("I", 4)
        (self._entry_count,) = self._unpack("H", self._ifd0)

    def orientation_entry(self) -> _Entry | None:
        for index in range(self._entry_count):
            offset = self._ifd0 + 2 + 12 * index
            tag, field_type, count = self._unpack("HHI", offset)
            if tag == ORIENTATION_TAG and field_type == _SHORT and count == 1:
                (value,) = self._unpack("H", offset + 8)
                return _Entry(offset, value)

        return None

    def with_orientation(self, orientation: int) -> bytes:
        entry = self.orientation_entry()
        if entry is not None:
            value = struct.pack(self._order + "H", orientation)
            return self._data[:entry.offset + 8] + value + self._data[entry.offset + 10:]

        return self._with_ifd0_copy(orientation)

    def _with_ifd0_copy(self, orientation: int) -> bytes:
        """IFD0 with the tag added, in tag order as TIFF wants, after the end of the data."""
        entries_end = self._ifd0 + 2 + 12 * self._entry_count
        entries = [self._data[offset:offset + 12] for offset in range(self._ifd0 + 2, entries_end, 12)]
        entries = [entry for entry in entries if struct.unpack(self._order + "H", entry[:2])[0] != ORIENTATION_TAG]
        entries.append(struct.pack(self._order + "HHIHH", ORIENTATION_TAG, _SHORT, 1, orientation, 0))
        entries.sort(key=lambda entry: struct.unpack(self._order + "H", entry[:2])[0])
        next_ifd = self._data[entries_end:entries_end + 4]

        padding = b"\x00" * (len(self._data) % 2)  # directories start on a word boundary
        new_ifd0 = len(self._data) + len(padding)
        header = self._data[:4] + struct.pack(self._order + "I", new_ifd0)
        directory = struct.pack(self._order + "H", len(entries)) + b"".join(entries) + next_ifd
        return header + self._data[8:] + padding + directory

    def _unpack(self, fields: str, offset: int) -> tuple[int, ...]:
        try:
            return struct.unpack_from(self._order + fields, self._data, offset)
        except struct.error:
            raise ValueError("Damaged EXIF data") from None


def _tiff_of(data: bytes) -> bytes | None:
    if data.startswith(_TIFF_HEADERS):
        return data

    if not data.startswith(_JPEG_START):
        raise ValueError("Neither a JPEG nor a TIFF file")

    segment = _exif_segment(data)
    if segment is None:
        return None

    start, end = segment
    return data[start + 4 + len(_EXIF_HEADER):end]


def _exif_segment(data: bytes) -> tuple[int, int] | None:
    """Where the APP1 "Exif" segment starts (its marker) and ends, if there is one before the image data."""
    offset = len(_JPEG_START)
    while offset + 4 <= len(data) and data[offset] == 0xFF:
        marker = data[offset + 1]
        if marker == 0xDA:  # start of scan: the image data follows, no metadata after it
            return None

        (length,) = struct.unpack_from(">H", data, offset + 2)
        end = offset + 2 + length
        if marker == 0xE1 and data[offset + 4:offset + 4 + len(_EXIF_HEADER)] == _EXIF_HEADER:
            return offset, end

        offset = end

    return None


def _with_new_exif_segment(data: bytes, orientation: int) -> bytes:
    """After the JFIF segment if there is one, which must come first; otherwise right after the start."""
    tiff = b"MM\x00*" + struct.pack(">I", 8) + struct.pack(">H", 1)
    tiff += struct.pack(">HHIHH", ORIENTATION_TAG, _SHORT, 1, orientation, 0) + struct.pack(">I", 0)
    payload = _EXIF_HEADER + tiff
    segment = b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload

    position = len(_JPEG_START)
    if data[position:position + 2] == b"\xff\xe0":
        (length,) = struct.unpack_from(">H", data, position + 2)
        position += 2 + length

    return data[:position] + segment + data[position:]


__all__ = ["ORIENTATION_TAG", "read_orientation", "turned_orientation", "with_orientation"]
