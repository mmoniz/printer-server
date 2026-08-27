"""Synthetic label PDFs standing in for real carrier output."""

import io

import pytest
from pypdf import PageObject, PdfWriter


def make_pdf(page_w, page_h, blocks, rotate=0):
    """Build a one-page PDF containing solid black rectangles.

    ``blocks`` is a list of (x, y, w, h) in PDF points, origin bottom-left.
    """
    page = PageObject.create_blank_page(width=page_w, height=page_h)

    drawing = " ".join(f"{x} {y} {w} {h} re f" for x, y, w, h in blocks)
    content = f"0 0 0 rg {drawing}".encode()

    from pypdf.generic import DecodedStreamObject, NameObject

    stream = DecodedStreamObject()
    stream.set_data(content)
    page[NameObject("/Contents")] = stream
    if rotate:
        page[NameObject("/Rotate")] = __import__("pypdf").generic.NumberObject(rotate)

    writer = PdfWriter()
    writer.add_page(page)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


@pytest.fixture
def label_4x6():
    """Already a 4x6 label: content fills the page."""
    return make_pdf(288, 432, [(8, 8, 272, 416)])


@pytest.fixture
def letter_with_label():
    """US Letter laid out the way carriers actually print a 4x6 label.

    The label sits in the top-left, with a full-width fold line and a block of
    terms text below it. Those distractors are the whole point: a naive ink
    bounding box swallows them and crops to most of the page.
    """
    label_bottom = 792 - 36 - 432
    return make_pdf(612, 792, [
        (36, label_bottom, 288, 432),  # the label itself
        (36, label_bottom - 48, 540, 1),  # full-width fold line
        (36, 120, 400, 8),  # terms text down in the footer
        (36, 100, 360, 8),
    ])


@pytest.fixture
def letter_label_bottom_right():
    """Same idea, but the label is in the bottom-right corner."""
    return make_pdf(612, 792, [
        (612 - 36 - 288, 36, 288, 432),
        (36, 700, 540, 1),
        (36, 660, 300, 8),
    ])


@pytest.fixture
def landscape_label():
    """A 6x4 label that needs rotating onto portrait stock."""
    return make_pdf(432, 288, [(8, 8, 416, 272)])


@pytest.fixture
def blank_page():
    return make_pdf(612, 792, [])
