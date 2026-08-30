"""Tests for turning uploads into 4x6 labels."""

import io
from pathlib import Path

import numpy as np
import pytest
from pypdf import PdfReader

from labelserver import normalize
from labelserver.normalize import Mode, NormalizeError

from conftest import make_pdf

FIXTURES = Path(__file__).parent / "fixtures"


def page_size(pdf_bytes):
    page = PdfReader(io.BytesIO(pdf_bytes)).pages[0]
    return (round(float(page.mediabox.width)), round(float(page.mediabox.height)))


def ink_coverage(pdf_bytes):
    """Fraction of the rendered page covered by ink."""
    gray = normalize._render_gray(pdf_bytes, 0, 72)
    return float((gray <= normalize.INK_THRESHOLD).mean())


# --- output geometry -----------------------------------------------------

@pytest.mark.parametrize("fixture", ["label_4x6", "letter_with_label",
                                     "landscape_label"])
def test_output_is_always_4x6(fixture, request):
    data = request.getfixturevalue(fixture)
    out, _ = normalize.normalize_pdf(data)
    assert page_size(out) == (288, 432)


def test_already_4x6_is_left_alone(label_4x6):
    out, result = normalize.normalize_pdf(label_4x6)
    assert not result.cropped
    assert result.rotated_deg == 0
    assert result.scale == pytest.approx(1.0, abs=0.02)


def test_letter_page_is_cropped_to_the_label(letter_with_label):
    out, result = normalize.normalize_pdf(letter_with_label)

    assert result.cropped
    assert result.label_shaped
    assert result.source_size_pt == (612.0, 792.0)

    x0, y0, x1, y1 = result.crop_box_pt
    # The label is 288x432 plus a few points of padding on each side.
    assert x1 - x0 == pytest.approx(288, abs=2 * normalize.CROP_PADDING + 2)
    assert y1 - y0 == pytest.approx(432, abs=2 * normalize.CROP_PADDING + 2)


def test_cropping_makes_the_label_fill_the_page(letter_with_label):
    """The point of cropping: ink should dominate the output."""
    before = ink_coverage(letter_with_label)
    out, _ = normalize.normalize_pdf(letter_with_label)
    after = ink_coverage(out)

    assert before < 0.30, "fixture should be mostly empty page"
    assert after > 0.90, f"label did not fill the 4x6 page (coverage {after:.2f})"


def test_landscape_label_is_rotated_upright(landscape_label):
    out, result = normalize.normalize_pdf(landscape_label)

    assert result.rotated_deg == 90
    assert page_size(out) == (288, 432)
    assert ink_coverage(out) > 0.90


def test_rotated_page_attribute_is_honoured():
    """A page carrying /Rotate 90 must still come out upright and filled."""
    data = make_pdf(432, 288, [(8, 8, 416, 272)], rotate=90)
    out, _ = normalize.normalize_pdf(data)

    assert page_size(out) == (288, 432)
    assert ink_coverage(out) > 0.90


def test_unconfident_crop_is_not_rotated_blind():
    """A real UPS return label surfaced this: a tall page laid out in
    horizontal bands (sender, ship-to, barcode block, footer) with enough
    whitespace between them that block segmentation split it apart and
    picked the barcode band alone as "the label" -- landscape-shaped, but
    not a clean 4x6/6x4 match (aspect ~1.2, not ~0.67 or ~1.5).

    The old rule rotated purely on width>height vs the target, so it forced
    a 90-degree turn on this ambiguous band with no way to know if that was
    the right direction -- and it wasn't, so the tracking barcode came out
    sideways. This block is a minimal stand-in for that shape: reproduce it
    with a real carrier PDF and this should stay green.
    """
    page_w, page_h = 300.0, 400.0
    # A landscape block covering most of a portrait page, aspect ~1.2 --
    # nowhere near label-shaped in either orientation.
    data = make_pdf(page_w, page_h, [(10, 80, 280, 233)])

    _, result = normalize.normalize_pdf(data)

    assert not result.label_shaped
    assert result.rotated_deg == 0


def test_real_ups_multiband_label_is_not_rotated_sideways():
    """The actual carrier PDF that surfaced this bug, with names, addresses
    and the tracking/routing numbers replaced by placeholder text -- the
    exact layout (page size, section spacing, barcode positions) is
    untouched, since that's what triggers the segmentation misfire in
    test_unconfident_crop_is_not_rotated_blind. That synthetic test documents
    the mechanism cheaply; this one proves the fix against the real,
    messier carrier output rather than a fixture shaped to be convenient.

    Confirmed against the pre-fix code before committing: this fixture
    reproduces rotated_deg == 90 there, and == 0 here.
    """
    data = (FIXTURES / "ups_multiband_redacted.pdf").read_bytes()

    _, result = normalize.normalize_pdf(data)

    assert not result.label_shaped
    assert result.rotated_deg == 0
    # The crop still captures the barcode section at minimum -- this isn't
    # asserting the crop is *good*, just that nothing came out sideways.
    assert result.crop_box_pt is not None
    assert result.rotated_deg == 0


# --- modes ---------------------------------------------------------------

def test_fit_mode_never_crops(letter_with_label):
    out, result = normalize.normalize_pdf(letter_with_label, mode=Mode.FIT)

    assert not result.cropped
    assert page_size(out) == (288, 432)
    # The whole Letter page shrunk onto a 4x6, so the label is now small.
    assert ink_coverage(out) < 0.40


def test_crop_mode_crops_even_when_page_is_already_a_label(label_4x6):
    out, result = normalize.normalize_pdf(label_4x6, mode=Mode.CROP)
    assert result.cropped
    assert page_size(out) == (288, 432)


def test_auto_mode_skips_crop_at_high_coverage(label_4x6):
    _, result = normalize.normalize_pdf(label_4x6, mode=Mode.AUTO)
    assert not result.cropped


# --- ink detection -------------------------------------------------------

def test_label_is_found_despite_fold_line_and_terms(letter_with_label):
    """The distractors carriers print must not widen the crop."""
    _, result = normalize.normalize_pdf(letter_with_label)

    x0, y0, x1, y1 = result.crop_box_pt
    width, height = x1 - x0, y1 - y0
    slack = 2 * normalize.CROP_PADDING + 2

    assert width == pytest.approx(288, abs=slack), \
        "crop grew sideways - the full-width fold line was included"
    assert height == pytest.approx(432, abs=slack)
    assert result.label_shaped


def test_label_found_in_bottom_right_corner(letter_label_bottom_right):
    out, result = normalize.normalize_pdf(letter_label_bottom_right)
    x0, y0, x1, y1 = result.crop_box_pt
    slack = 2 * normalize.CROP_PADDING + 2

    assert (x1 - x0) == pytest.approx(288, abs=slack)
    assert (y1 - y0) == pytest.approx(432, abs=slack)
    assert ink_coverage(out) > 0.90


def test_content_blocks_separate_label_from_furniture():
    """A tall block plus a thin wide rule must come back as two blocks."""
    ink = np.zeros((300, 400), dtype=bool)
    ink[10:200, 10:140] = True  # label-ish block
    ink[250:252, 5:395] = True  # full-width rule, well separated

    blocks = normalize.find_content_blocks(ink, min_gap=18)

    assert len(blocks) == 2
    assert (10, 10, 140, 200) in blocks
    assert (5, 250, 395, 252) in blocks


def test_content_blocks_bridge_small_gaps():
    """Whitespace inside a label must not split it apart."""
    ink = np.zeros((300, 400), dtype=bool)
    ink[10:80, 10:140] = True
    ink[92:200, 10:140] = True  # 12px gap, under the 18px threshold

    blocks = normalize.find_content_blocks(ink, min_gap=18)
    assert blocks == [(10, 10, 140, 200)]


def test_thin_rules_are_never_chosen():
    ink = np.zeros((300, 400), dtype=bool)
    ink[100:102, 0:400] = True  # only a rule on the page

    assert normalize._score_block((0, 100, 400, 102), 300 * 400) == 0.0


def test_label_shape_beats_raw_size():
    """A slightly smaller label-shaped block wins over a big square one."""
    page_area = 612.0 * 792.0
    label = (0, 0, 288, 432)  # 124k, label-shaped
    blob = (0, 0, 380, 380)  # 144k, not label-shaped

    assert normalize._score_block(label, page_area) > \
        normalize._score_block(blob, page_area)


def test_find_ink_bbox_locates_a_block():
    gray = np.full((100, 200), 255, dtype=np.uint8)
    gray[10:40, 50:120] = 0
    assert normalize.find_ink_bbox(gray) == (50, 10, 120, 40)


def test_find_ink_bbox_returns_none_when_blank():
    assert normalize.find_ink_bbox(np.full((10, 10), 255, dtype=np.uint8)) is None


def test_find_ink_bbox_ignores_near_white():
    gray = np.full((50, 50), 252, dtype=np.uint8)
    assert normalize.find_ink_bbox(gray) is None


def test_label_shape_detection():
    assert normalize._is_label_shaped(288, 432)  # 4x6 portrait
    assert normalize._is_label_shaped(432, 288)  # 4x6 landscape
    assert not normalize._is_label_shaped(612, 792)  # Letter
    assert not normalize._is_label_shaped(0, 100)


# --- uploads and errors --------------------------------------------------

def test_normalize_upload_accepts_png():
    from PIL import Image

    img = Image.new("RGB", (400, 600), "white")
    for x in range(50, 350):
        for y in range(50, 550):
            img.putpixel((x, y), (0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")

    out, result = normalize.normalize_upload(buf.getvalue(), "label.png")
    assert page_size(out) == (288, 432)
    assert result.page_count == 1


def test_blank_page_is_rejected(blank_page):
    with pytest.raises(NormalizeError, match="blank"):
        normalize.normalize_pdf(blank_page)


def test_empty_upload_is_rejected():
    with pytest.raises(NormalizeError, match="empty"):
        normalize.normalize_upload(b"", "x.pdf")


def test_bogus_pdf_is_rejected():
    with pytest.raises(NormalizeError):
        normalize.normalize_upload(b"%PDF-1.4 garbage garbage", "x.pdf")


def test_mislabelled_pdf_is_rejected():
    with pytest.raises(NormalizeError, match="not one"):
        normalize.normalize_upload(b"this is plain text", "label.pdf")


def test_out_of_range_page_is_rejected(label_4x6):
    with pytest.raises(NormalizeError, match="page 5"):
        normalize.normalize_pdf(label_4x6, page_index=4)


def test_result_describes_itself(letter_with_label):
    _, result = normalize.normalize_pdf(letter_with_label)
    text = result.describe()
    assert "source 8.50x11.00in" in text
    assert "cropped" in text


# --- preview -------------------------------------------------------------

def test_render_preview_produces_a_png(label_4x6):
    png = normalize.render_preview(label_4x6, width_px=200)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"

    from PIL import Image

    img = Image.open(io.BytesIO(png))
    assert img.width == 200
    # 4x6 aspect ratio preserved.
    assert img.height == pytest.approx(300, abs=2)
