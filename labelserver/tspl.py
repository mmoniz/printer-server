"""
CUPS raster -> TSPL conversion for the generic "Thermal Label Printer".

The printer's vendor driver is a rebadged build of the CUPS ``rastertolabel``
filter, shipped only as an x86_64 macOS binary. This module reimplements its
output so the printer can be driven from Linux/ARM (a Raspberry Pi).

Output is byte-identical to the vendor binary; see tests/test_tspl.py, which
diffs against golden fixtures captured from it.

Key details that are easy to get wrong:

* TSPL bitmaps are inverted -- bit 1 is white, bit 0 burns.
* Rows are padded to a byte boundary, and the padding bits must be white or
  every label gets a black stripe down its right edge.
* Media size is sent in whole millimetres, rounded *up* (4x6in -> 102x153mm).
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from typing import BinaryIO, Iterator

import numpy as np

RASTER_SYNC_LE = b"3SaR"
HEADER_LEN = 1796

# Byte offsets into the CUPS v3 page header of the fields we use.
_F_PAGE_SIZE = 352  # two uint32s: width, height, in points
_F_WIDTH = 372
_F_HEIGHT = 376
_F_BITS_PER_COLOR = 384
_F_BITS_PER_PIXEL = 388
_F_BYTES_PER_LINE = 392
_F_COLOR_SPACE = 400
_F_COMPRESSION = 404

_COLORSPACE_GRAY = 0

POINTS_PER_MM = 72.0 / 25.4


class RasterError(Exception):
    """The incoming raster stream is not something we can print."""


@dataclass(frozen=True)
class Settings:
    """Per-job knobs. Defaults mirror the vendor PPD."""

    darkness: int = 6  # TSPL DENSITY, 0-15
    speed: int = 4  # TSPL SPEED, in inches/sec
    gap_mm: int = 3  # label gap; 0 for continuous stock
    offset_mm: int = 0
    adjust_x: int = 0  # TSPL REFERENCE, in dots
    adjust_y: int = 0
    threshold: int = 127  # gray level at or below which a dot burns
    copies: int = 1

    @classmethod
    def from_cups_options(cls, options: str, copies: int = 1) -> "Settings":
        """Parse a CUPS ``argv[5]`` option string (``"Darkness=8 Speed=3"``)."""
        opts = {}
        for token in options.split():
            key, sep, value = token.partition("=")
            if sep:
                opts[key.lower()] = value

        def as_int(key: str, default: int) -> int:
            try:
                return int(opts[key])
            except (KeyError, ValueError):
                return default

        gap = 0 if opts.get("zemediatracking", "").lower() == "continuous" else 3
        return cls(
            darkness=as_int("darkness", 6),
            speed=as_int("zeprintrate", 4),
            gap_mm=as_int("gap", gap),
            offset_mm=as_int("feedoffset", 0),
            adjust_x=as_int("adjusthoriaontal", as_int("adjusthorizontal", 0)),
            adjust_y=as_int("adjustvertical", 0),
            copies=max(1, copies),
        )


@dataclass(frozen=True)
class Page:
    """One decoded raster page."""

    width: int  # dots
    height: int  # dots
    bytes_per_line: int
    width_pt: int
    height_pt: int
    gray: bytes  # 8-bit grayscale, bytes_per_line * height

    @property
    def size_mm(self) -> tuple[int, int]:
        # Round up so the declared media is never shorter than the label.
        return (
            math.ceil(self.width_pt / POINTS_PER_MM),
            math.ceil(self.height_pt / POINTS_PER_MM),
        )


def read_pages(raster: bytes) -> Iterator[Page]:
    """Yield each page of an uncompressed little-endian CUPS v3 raster."""
    if raster[:4] != RASTER_SYNC_LE:
        raise RasterError(
            "expected a little-endian CUPS raster v3 stream (%r)" % raster[:4]
        )

    pos = 4
    while pos + HEADER_LEN <= len(raster):
        hdr = raster[pos : pos + HEADER_LEN]
        pos += HEADER_LEN

        def field(offset: int) -> int:
            return struct.unpack_from("<I", hdr, offset)[0]

        if field(_F_COMPRESSION) != 0:
            raise RasterError("compressed rasters are not supported")
        if field(_F_BITS_PER_PIXEL) != 8 or field(_F_BITS_PER_COLOR) != 8:
            raise RasterError("expected 8 bits per pixel")
        if field(_F_COLOR_SPACE) != _COLORSPACE_GRAY:
            raise RasterError("expected a grayscale raster")

        height = field(_F_HEIGHT)
        bpl = field(_F_BYTES_PER_LINE)
        width_pt, height_pt = struct.unpack_from("<II", hdr, _F_PAGE_SIZE)

        n = bpl * height
        if pos + n > len(raster):
            raise RasterError("raster data is truncated")

        yield Page(
            width=field(_F_WIDTH),
            height=height,
            bytes_per_line=bpl,
            width_pt=width_pt,
            height_pt=height_pt,
            gray=raster[pos : pos + n],
        )
        pos += n


def pack_bitmap(page: Page, threshold: int = 127) -> bytes:
    """Pack a grayscale page into TSPL's inverted 1-bit-per-dot format."""
    row_bytes = (page.width + 7) // 8

    pixels = np.frombuffer(page.gray, dtype=np.uint8)
    pixels = pixels.reshape(page.height, page.bytes_per_line)[:, : page.width]

    bits = pixels > threshold  # True = leave white
    padding = row_bytes * 8 - page.width
    if padding:
        bits = np.hstack([bits, np.ones((page.height, padding), dtype=bool)])

    return np.packbits(bits, axis=1).tobytes()


def page_to_tspl(page: Page, settings: Settings = Settings()) -> bytes:
    """Render one page as a complete TSPL command sequence."""
    width_mm, height_mm = page.size_mm
    row_bytes = (page.width + 7) // 8

    out = bytearray()
    out += b"SIZE %d mm ,%d mm\n" % (width_mm, height_mm)
    out += b"REFERENCE %d,%d\n" % (settings.adjust_x, settings.adjust_y)
    out += b"DIRECTION 0,0\n"
    out += b"GAP %d mm,0 mm\n" % settings.gap_mm
    out += b"OFFSET %d mm\n" % settings.offset_mm
    out += b"DENSITY %d\n" % settings.darkness
    out += b"SPEED %d\n" % settings.speed
    out += b"SETC AUTODOTTED OFF\n"
    out += b"SETC PAUSEKEY ON\n"
    out += b"SETC WATERMARK OFF\n"
    out += b"CLS\n"
    out += b"BITMAP 0,0,%d,%d,1," % (row_bytes, page.height)
    out += pack_bitmap(page, settings.threshold)
    out += b"PRINT %d,1\n" % settings.copies
    return bytes(out)


def convert(raster: bytes, settings: Settings, out: BinaryIO,
            log: BinaryIO | None = None) -> int:
    """Convert a whole raster stream, writing TSPL to ``out``.

    Returns the number of pages emitted.
    """
    count = 0
    for page in read_pages(raster):
        count += 1
        if log is not None:
            log.write(b"PAGE: %d 1\n" % count)
            log.flush()
        out.write(page_to_tspl(page, settings))
    out.flush()
    return count
