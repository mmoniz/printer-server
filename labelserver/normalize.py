"""
Turn whatever the family uploads into a 4x6 label the printer can use.

Carrier labels arrive in three shapes:

* already a 4x6 PDF (what UPS/USPS give you if you pick "thermal printer")
* a US Letter page with the 4x6 label in one corner and fold marks around it
* a photo or screenshot of a label

The transform stays vectorial wherever possible -- the source page is placed
onto a 4x6 page with a transformation matrix rather than being rasterized.
Barcodes stay crisp, which matters when a scanner has to read them.

Rasterization is used only to *find* the label (and to build previews).
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import numpy as np
from pypdf import PdfReader, PdfWriter, Transformation

LABEL_4X6 = (288.0, 432.0)  # points

# Rendering resolution used for locating ink. 72dpi means 1px == 1pt, which
# keeps the coordinate maths obvious. Detection does not need detail.
DETECT_DPI = 72

# Pixels at or below this gray level count as ink (0 = black, 255 = white).
INK_THRESHOLD = 245

# How much of the page the ink must cover before we assume the page *is* the
# label and skip cropping.
FULL_PAGE_COVERAGE = 0.92

# Padding kept around detected ink, in points.
CROP_PADDING = 4.0

# Blank space, in points, that separates one block of content from another.
# Carrier pages put a fold line and terms text well clear of the label; the
# gaps *inside* a label (between the address block and the barcode) are much
# smaller than this.
BLOCK_GAP = 18.0

# A candidate block must cover at least this much of the page to be considered
# the label rather than a stray mark.
MIN_BLOCK_COVERAGE = 0.06

# Blocks longer than this relative to their thickness are rules, fold lines or
# cut marks, never labels.
MAX_BLOCK_ELONGATION = 8.0

# A 4x6 label is 0.667 wide:tall. Anything within this tolerance of that (in
# either orientation) is considered label-shaped. Kept tight enough to exclude
# US Letter (0.773), which is the shape we most need to tell labels apart from.
LABEL_ASPECT = 288.0 / 432.0
ASPECT_TOLERANCE = 0.08


class Mode(str, Enum):
    AUTO = "auto"  # crop to the label if the page looks bigger than one
    CROP = "crop"  # always crop to detected ink
    FIT = "fit"  # never crop; shrink the whole page onto the label


class NormalizeError(Exception):
    """The upload could not be turned into a label."""


@dataclass
class Result:
    """What normalization did, for display in the web UI."""

    source_size_pt: tuple[float, float]
    crop_box_pt: tuple[float, float, float, float] | None
    rotated_deg: int
    scale: float
    page_count: int
    label_shaped: bool

    @property
    def cropped(self) -> bool:
        return self.crop_box_pt is not None

    def describe(self) -> str:
        w, h = self.source_size_pt
        bits = [f"source {w / 72:.2f}x{h / 72:.2f}in"]
        if self.cropped:
            x0, y0, x1, y1 = self.crop_box_pt
            bits.append(f"cropped to {(x1 - x0) / 72:.2f}x{(y1 - y0) / 72:.2f}in")
        if self.rotated_deg:
            bits.append(f"rotated {self.rotated_deg}°")
        bits.append(f"scaled {self.scale * 100:.0f}%")
        return ", ".join(bits)


def _render_gray(page_bytes: bytes, page_index: int, dpi: int) -> np.ndarray:
    """Render one PDF page to a 2-D grayscale array."""
    try:
        import pypdfium2
    except ImportError as exc:  # pragma: no cover - depends on install
        raise NormalizeError(
            "pypdfium2 is required to inspect PDFs; install requirements.txt"
        ) from exc

    doc = pypdfium2.PdfDocument(page_bytes)
    try:
        if page_index >= len(doc):
            raise NormalizeError(f"page {page_index + 1} does not exist")
        bitmap = doc[page_index].render(scale=dpi / 72, grayscale=True)
        return np.asarray(bitmap.to_pil().convert("L"))
    finally:
        doc.close()


def find_ink_bbox(gray: np.ndarray, threshold: int = INK_THRESHOLD):
    """Bounding box of non-white pixels as (x0, y0, x1, y1), or None if blank.

    Coordinates are in pixels with the origin at the *top* left, matching the
    rendered image rather than PDF space.
    """
    ink = gray <= threshold
    rows = np.flatnonzero(ink.any(axis=1))
    cols = np.flatnonzero(ink.any(axis=0))
    if rows.size == 0 or cols.size == 0:
        return None
    return int(cols[0]), int(rows[0]), int(cols[-1]) + 1, int(rows[-1]) + 1


def _runs(present: np.ndarray, min_gap: int) -> list[tuple[int, int]]:
    """Group True values into runs, bridging gaps shorter than ``min_gap``.

    Given a per-row (or per-column) "has ink" profile, this returns the spans
    of content, treating small whitespace as part of the same block.
    """
    idx = np.flatnonzero(present)
    if idx.size == 0:
        return []

    runs = []
    start = prev = idx[0]
    for i in idx[1:]:
        if i - prev > min_gap:
            runs.append((int(start), int(prev) + 1))
            start = i
        prev = i
    runs.append((int(start), int(prev) + 1))
    return runs


def find_content_blocks(ink: np.ndarray, min_gap: int) -> list[tuple[int, int, int, int]]:
    """Split a page into blocks of content separated by whitespace.

    A projection cut: first into horizontal bands, then each band into columns.
    This is enough to tell a shipping label apart from the fold line and terms
    text that carriers print on the same page.

    Returns (x0, y0, x1, y1) boxes in pixel coordinates, origin top-left.
    """
    blocks = []
    for top, bottom in _runs(ink.any(axis=1), min_gap):
        band = ink[top:bottom]
        for left, right in _runs(band.any(axis=0), min_gap):
            cell = band[:, left:right]
            # Tighten to the actual ink inside this cell.
            rows = np.flatnonzero(cell.any(axis=1))
            cols = np.flatnonzero(cell.any(axis=0))
            if rows.size == 0 or cols.size == 0:
                continue
            blocks.append((
                left + int(cols[0]), top + int(rows[0]),
                left + int(cols[-1]) + 1, top + int(rows[-1]) + 1,
            ))
    return blocks


def _score_block(box, page_area: float) -> float:
    """How much a block looks like the label we want. Higher is better."""
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    if w <= 0 or h <= 0:
        return 0.0

    area = w * h
    if area / page_area < MIN_BLOCK_COVERAGE:
        return 0.0
    if max(w, h) / max(1.0, min(w, h)) > MAX_BLOCK_ELONGATION:
        return 0.0  # a rule or a fold line

    # Prefer big blocks, and strongly prefer ones shaped like a 4x6 label.
    return area * (3.0 if _is_label_shaped(w, h) else 1.0)


def find_label_region(gray: np.ndarray, min_gap: int,
                      threshold: int = INK_THRESHOLD):
    """Locate the label on a page, in pixel coordinates.

    Falls back to the overall ink bounding box when no block stands out.
    """
    ink = gray <= threshold
    if not ink.any():
        return None

    page_area = float(gray.shape[0] * gray.shape[1])
    blocks = find_content_blocks(ink, min_gap)

    best, best_score = None, 0.0
    for box in blocks:
        score = _score_block(box, page_area)
        if score > best_score:
            best, best_score = box, score

    return best if best is not None else find_ink_bbox(gray, threshold)


def _is_label_shaped(width: float, height: float) -> bool:
    if width <= 0 or height <= 0:
        return False
    aspect = width / height
    return (
        abs(aspect - LABEL_ASPECT) <= ASPECT_TOLERANCE
        or abs(aspect - 1 / LABEL_ASPECT) <= ASPECT_TOLERANCE
    )


def _detect_crop(pdf_bytes: bytes, page_index: int, page_w: float, page_h: float,
                 mode: Mode):
    """Work out the region of the page to keep, in PDF points."""
    if mode is Mode.FIT:
        return None, False

    gray = _render_gray(pdf_bytes, page_index, DETECT_DPI)
    if not (gray <= INK_THRESHOLD).any():
        raise NormalizeError("the page appears to be blank")

    min_gap = round(BLOCK_GAP * DETECT_DPI / 72)
    box = find_label_region(gray, min_gap)
    if box is None:
        raise NormalizeError("the page appears to be blank")

    img_h, img_w = gray.shape
    sx, sy = page_w / img_w, page_h / img_h
    x0, top, x1, bottom = box

    # Flip the vertical axis: images count down from the top, PDFs count up
    # from the bottom.
    pdf_box = (
        max(0.0, x0 * sx - CROP_PADDING),
        max(0.0, page_h - bottom * sy - CROP_PADDING),
        min(page_w, x1 * sx + CROP_PADDING),
        min(page_h, page_h - top * sy + CROP_PADDING),
    )

    ink_w = pdf_box[2] - pdf_box[0]
    ink_h = pdf_box[3] - pdf_box[1]
    label_shaped = _is_label_shaped(ink_w, ink_h)

    if mode is Mode.AUTO:
        coverage = (ink_w * ink_h) / (page_w * page_h)
        if coverage >= FULL_PAGE_COVERAGE:
            # The page is essentially all label already; leave it alone.
            return None, label_shaped

    return pdf_box, label_shaped


def normalize_pdf(data: bytes, mode: Mode = Mode.AUTO, page_index: int = 0,
                  target: tuple[float, float] = LABEL_4X6) -> tuple[bytes, Result]:
    """Place a page from ``data`` onto a label-sized page.

    Returns the new single-page PDF and a description of what was done.
    """
    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception as exc:
        raise NormalizeError(f"could not read the PDF: {exc}") from exc

    if reader.is_encrypted:
        try:
            reader.decrypt("")  # carrier PDFs are often "encrypted" with no password
        except Exception as exc:
            raise NormalizeError("the PDF is password protected") from exc

    if not reader.pages:
        raise NormalizeError("the PDF has no pages")
    if page_index >= len(reader.pages):
        raise NormalizeError(
            f"asked for page {page_index + 1} of a {len(reader.pages)}-page PDF"
        )

    # Attach the page to a writer before touching it; pypdf needs an owning
    # document to rewrite content streams reliably.
    staging = PdfWriter()
    staging.add_page(reader.pages[page_index])
    source = staging.pages[0]

    # Bake any /Rotate into the content so our own maths is the only rotation.
    if source.get("/Rotate"):
        source.transfer_rotation_to_content()

    page_w = float(source.mediabox.width)
    page_h = float(source.mediabox.height)
    if page_w <= 0 or page_h <= 0:
        raise NormalizeError("the page has no usable size")

    crop, label_shaped = _detect_crop(data, page_index, page_w, page_h, mode)

    if crop is None:
        x0, y0 = 0.0, 0.0
        src_w, src_h = page_w, page_h
    else:
        x0, y0, x1, y1 = crop
        src_w, src_h = x1 - x0, y1 - y0

    target_w, target_h = target

    # Rotate a landscape label upright so it fills the portrait stock.
    rotate = 90 if (src_w > src_h) != (target_w > target_h) else 0
    effective_w, effective_h = (src_h, src_w) if rotate else (src_w, src_h)

    scale = min(target_w / effective_w, target_h / effective_h)

    # Move the region of interest to the origin, rotate about it, scale, then
    # centre what is left on the label.
    transform = Transformation().translate(-x0, -y0)
    if rotate:
        transform = transform.rotate(90).translate(src_h, 0)
    transform = transform.scale(scale, scale)
    transform = transform.translate(
        (target_w - effective_w * scale) / 2,
        (target_h - effective_h * scale) / 2,
    )

    writer = PdfWriter()
    label = writer.add_blank_page(width=target_w, height=target_h)
    label.merge_transformed_page(source, transform)

    out = io.BytesIO()
    writer.write(out)

    return out.getvalue(), Result(
        source_size_pt=(page_w, page_h),
        crop_box_pt=crop,
        rotated_deg=rotate,
        scale=scale,
        page_count=len(reader.pages),
        label_shaped=label_shaped,
    )


def image_to_pdf(data: bytes) -> bytes:
    """Wrap a PNG/JPEG in a PDF page of the same proportions."""
    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover - depends on install
        raise NormalizeError("Pillow is required to accept images") from exc

    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception as exc:
        raise NormalizeError(f"could not read the image: {exc}") from exc

    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")

    # Assume 203dpi so a label-sized image lands at roughly label size; the
    # scale-to-fit step corrects anything else.
    out = io.BytesIO()
    img.save(out, format="PDF", resolution=203.0)
    return out.getvalue()


def normalize_upload(data: bytes, filename: str = "", mode: Mode = Mode.AUTO,
                     page_index: int = 0) -> tuple[bytes, Result]:
    """Normalize an uploaded PDF or image into a 4x6 label PDF."""
    if not data:
        raise NormalizeError("the uploaded file is empty")

    if data[:5] != b"%PDF-":
        suffix = Path(filename).suffix.lower()
        if suffix in {".pdf"}:
            raise NormalizeError("that file claims to be a PDF but is not one")
        data = image_to_pdf(data)

    return normalize_pdf(data, mode=mode, page_index=page_index)


def render_preview(pdf_bytes: bytes, width_px: int = 400) -> bytes:
    """Render page 1 of a PDF to PNG for the web UI."""
    try:
        import pypdfium2
    except ImportError as exc:  # pragma: no cover
        raise NormalizeError("pypdfium2 is required to render previews") from exc

    doc = pypdfium2.PdfDocument(pdf_bytes)
    try:
        page = doc[0]
        scale = width_px / page.get_width()
        image = page.render(scale=scale).to_pil()
    finally:
        doc.close()

    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


__all__ = [
    "LABEL_4X6",
    "Mode",
    "NormalizeError",
    "Result",
    "find_ink_bbox",
    "image_to_pdf",
    "normalize_pdf",
    "normalize_upload",
    "render_preview",
]
