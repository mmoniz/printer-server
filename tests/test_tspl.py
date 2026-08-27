"""
Regression tests for the TSPL filter.

The golden fixtures are real output captured from the vendor's macOS driver
(/Library/Printers/LabelPrinter/Filter/rastertolabel, an x86_64 build of the
CUPS rastertolabel filter) run against the matching raster. If our output stops
matching these byte for byte, we have broken compatibility with the printer.
"""

import gzip
import io
import subprocess
import sys
from pathlib import Path

import pytest

from labelserver import tspl

FIXTURES = Path(__file__).parent / "fixtures"
REPO_ROOT = Path(__file__).parent.parent

GOLDEN = [
    pytest.param("golden_4x6", (812, 1218), (102, 153), id="4x6in"),
    pytest.param("golden_2x1", (406, 203), (51, 26), id="2x1in"),
]


def load(name, suffix):
    with gzip.open(FIXTURES / f"{name}.{suffix}.gz", "rb") as fh:
        return fh.read()


@pytest.mark.parametrize("name,dots,size_mm", GOLDEN)
def test_matches_vendor_driver_byte_for_byte(name, dots, size_mm):
    raster = load(name, "ras")
    expected = load(name, "tspl")

    out = io.BytesIO()
    pages = tspl.convert(raster, tspl.Settings(), out)

    assert pages == 1
    assert out.getvalue() == expected


@pytest.mark.parametrize("name,dots,size_mm", GOLDEN)
def test_page_geometry(name, dots, size_mm):
    page = next(tspl.read_pages(load(name, "ras")))
    assert (page.width, page.height) == dots
    assert page.size_mm == size_mm


def test_media_size_rounds_up():
    """4x6in is 101.6 x 152.4mm; the printer must be told 102 x 153."""
    page = next(tspl.read_pages(load("golden_4x6", "ras")))
    assert page.width_pt, page.height_pt == (288, 432)
    assert page.size_mm == (102, 153)


def test_bitmap_is_inverted_and_padded_white():
    """Bit 1 = white, and row padding bits must be white too."""
    page = next(tspl.read_pages(load("golden_4x6", "ras")))
    packed = tspl.pack_bitmap(page)

    row_bytes = (page.width + 7) // 8
    assert row_bytes == 102
    assert len(packed) == row_bytes * page.height

    # The top row of the test label is blank, so it must be all white bits.
    assert packed[:row_bytes] == b"\xff" * row_bytes

    # 812 dots pad to 816 bits; the last 4 bits of every row must stay white.
    for y in range(0, page.height, 97):
        last = packed[(y + 1) * row_bytes - 1]
        assert last & 0x0F == 0x0F, f"row {y} has burn bits in its padding"


def test_dark_pixels_burn():
    """A page with ink must produce some zero bits."""
    page = next(tspl.read_pages(load("golden_4x6", "ras")))
    packed = tspl.pack_bitmap(page)
    assert any(byte != 0xFF for byte in packed), "no ink found in test label"


def test_settings_from_cups_options():
    s = tspl.Settings.from_cups_options("PageSize=w288h432 Darkness=11 zePrintRate=2")
    assert s.darkness == 11
    assert s.speed == 2
    assert s.gap_mm == 3

    s = tspl.Settings.from_cups_options("zeMediaTracking=Continuous")
    assert s.gap_mm == 0

    # Junk must fall back to the PPD defaults rather than raising.
    s = tspl.Settings.from_cups_options("Darkness=high")
    assert s.darkness == 6


def test_settings_copies_reach_print_command():
    page = next(tspl.read_pages(load("golden_2x1", "ras")))
    out = tspl.page_to_tspl(page, tspl.Settings(copies=3))
    assert out.endswith(b"PRINT 3,1\n")


def test_rejects_non_raster_input():
    with pytest.raises(tspl.RasterError, match="CUPS raster"):
        list(tspl.read_pages(b"%PDF-1.4 this is not a raster"))


def test_rejects_truncated_raster():
    raster = load("golden_2x1", "ras")
    with pytest.raises(tspl.RasterError, match="truncated"):
        list(tspl.read_pages(raster[: len(raster) // 2]))


def test_cups_filter_end_to_end(tmp_path):
    """Exercise the real filter binary with CUPS's calling convention."""
    raster = load("golden_4x6", "ras")
    expected = load("golden_4x6", "tspl")

    env = {"PYTHONPATH": str(REPO_ROOT), "PATH": "/usr/bin:/bin"}
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "cups" / "rastertotspl"),
         "1", "mike", "test-label", "1", "PageSize=w288h432"],
        input=raster, capture_output=True, env=env,
    )

    assert proc.returncode == 0, proc.stderr.decode()
    assert proc.stdout == expected
    assert b"PAGE: 1 1" in proc.stderr


def test_cups_filter_reports_bad_input():
    env = {"PYTHONPATH": str(REPO_ROOT), "PATH": "/usr/bin:/bin"}
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "cups" / "rastertotspl"),
         "1", "mike", "bad", "1", ""],
        input=b"not a raster", capture_output=True, env=env,
    )

    assert proc.returncode == 1
    assert b"ERROR:" in proc.stderr
