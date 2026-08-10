"""
Checks on the generated PPD.

The PPD is what tells CUPS to hand our filter an 812x1218 8-bit grayscale
raster. If it drifts, the filter starts receiving something it cannot use, so
these assertions guard the contract between the two.
"""

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
PPD_PATH = REPO_ROOT / "cups" / "LabelPrinter.ppd"
GENERATOR = REPO_ROOT / "scripts" / "make_ppd.py"


@pytest.fixture(scope="module")
def ppd():
    return PPD_PATH.read_text()


def directive(ppd, name):
    match = re.search(rf"^\*{re.escape(name)}:\s*(.+)$", ppd, re.MULTILINE)
    return match.group(1).strip().strip('"') if match else None


def test_default_media_is_4x6(ppd):
    assert directive(ppd, "DefaultPageSize") == "4x6.Fullbleed"
    assert directive(ppd, "DefaultPaperDimension") == "4x6.Fullbleed"
    assert directive(ppd, "DefaultImageableArea") == "4x6.Fullbleed"


def test_4x6_is_288x432_points(ppd):
    assert '*PaperDimension 4x6.Fullbleed/4.00x6.00" (shipping label): "288 432"' in ppd
    # Fullbleed: the printable area is the whole label.
    assert '*ImageableArea 4x6.Fullbleed/4.00x6.00" (shipping label): "0 0 288 432"' in ppd


def test_raster_matches_what_the_filter_expects(ppd):
    """8-bit grayscale at 203dpi, which is what tspl.read_pages accepts."""
    assert directive(ppd, "DefaultResolution") == "203dpi"

    resolution = re.search(r"^\*Resolution 203dpi.*$", ppd, re.MULTILINE).group(0)
    assert "HWResolution[203 203]" in resolution
    assert "cupsBitsPerColor 8" in resolution
    assert "cupsColorSpace 0" in resolution  # 0 = gray

    assert directive(ppd, "DefaultColorSpace") == "Gray"
    assert directive(ppd, "ColorDevice") == "False"


def test_filter_is_wired_up(ppd):
    assert directive(ppd, "cupsFilter") == \
        "application/vnd.cups-raster 0 rastertotspl"


def test_airprint_urf_is_declared(ppd):
    """Without this, iOS will not offer the printer."""
    urf = directive(ppd, "cupsUrfSupported")
    assert urf is not None
    assert "W8" in urf, "iOS must be told we take 8-bit grayscale"
    assert "RS203" in urf, "resolution must match the printer"


def test_option_defaults_match_the_filter(ppd):
    """PPD defaults and tspl.Settings defaults must not disagree."""
    from labelserver.tspl import Settings

    defaults = Settings()
    assert int(directive(ppd, "DefaultDarkness")) == defaults.darkness
    assert int(directive(ppd, "DefaultzePrintRate")) == defaults.speed
    assert directive(ppd, "DefaultzeMediaTracking") == "Gap"


def test_every_media_size_is_fully_described(ppd):
    """A size listed as a PageSize must also have the other three entries."""
    sizes = set(re.findall(r"^\*PageSize (\S+)/", ppd, re.MULTILINE))
    assert "4x6.Fullbleed" in sizes

    for keyword in ("PageRegion", "ImageableArea", "PaperDimension"):
        described = set(re.findall(rf"^\*{keyword} (\S+)/", ppd, re.MULTILINE))
        assert described == sizes, f"{keyword} does not cover the same sizes"


def test_ppd_is_in_sync_with_its_generator(tmp_path):
    """Catch hand-edits to the generated file."""
    regenerated = tmp_path / "LabelPrinter.ppd"
    subprocess.run([sys.executable, str(GENERATOR), str(regenerated)],
                   check=True, capture_output=True)

    assert regenerated.read_text() == PPD_PATH.read_text(), \
        "cups/LabelPrinter.ppd is stale; run python3 scripts/make_ppd.py"


@pytest.mark.skipif(shutil.which("cupstestppd") is None,
                    reason="cupstestppd not installed")
def test_cupstestppd_is_happy_apart_from_the_uninstalled_filter():
    proc = subprocess.run(["cupstestppd", str(PPD_PATH)],
                          capture_output=True, text=True)

    problems = [
        line.strip() for line in proc.stdout.splitlines()
        if "**FAIL**" in line
        # The filter only exists once install.sh has put it in place.
        and "rastertotspl" not in line
    ]
    assert not problems, "\n".join(problems)
