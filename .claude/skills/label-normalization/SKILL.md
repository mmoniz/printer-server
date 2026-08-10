---
name: label-normalization
description: How uploads become 4x6 labels — finding the label on a carrier page, cropping, rotating and scaling without rasterizing. Use this whenever you touch labelserver/normalize.py, work on crop/fit modes, block segmentation, ink detection, barcode sharpness, or investigate complaints like "the label printed tiny", "it cropped the wrong thing", or "the barcode won't scan". Read this before changing any detection constant.
---

# Label normalization

`labelserver/normalize.py` turns whatever a family member uploads into a
single 4x6 page (288 x 432 pt) that CUPS can rasterize.

## Keep it vectorial

The source page is placed onto a 4x6 page with a **transformation matrix**
(`pypdf.Transformation`), not rendered to an image and pasted. This matters:
these are shipping labels, and a courier's scanner has to read the barcode. Every
rasterization step costs sharpness.

Rasterization is used *only* to locate the label and to build the preview
image the user sees. It never touches what gets printed.

If you find yourself reaching for `Image` to build print output, stop and find a
transform-based way instead.

## Finding the label is the hard part

Carrier PDFs come in three shapes:

- already 4x6 (what you get if you pick "thermal printer" at checkout)
- **US Letter with the label in a corner**, plus a full-width fold line and a
  block of terms text
- a photo or screenshot

The middle case is where the real difficulty lives. A plain ink bounding box
also catches the fold line and the terms text, so the crop becomes nearly the
whole sheet and the label prints at roughly a third of its proper size. This
actually happened during development and was caught by the preview screen.

**The fix: projection-based block segmentation.** `find_content_blocks` splits
the page into horizontal bands separated by whitespace, then splits each band
into columns the same way, giving discrete content blocks. `_score_block` then
picks the winner:

- blocks under `MIN_BLOCK_COVERAGE` (6% of the page) are ignored as stray marks
- blocks more elongated than `MAX_BLOCK_ELONGATION` (8:1) are rejected as rules,
  fold lines and cut marks
- remaining blocks score by area, tripled if the shape is label-like

So a big label-shaped block beats a slightly larger square blob, and a
full-width hairline never wins regardless of how wide it is.

## The constants, and why they are what they are

| Constant | Value | Reasoning |
|---|---|---|
| `DETECT_DPI` | 72 | 1px == 1pt, so the coordinate maths is obvious. Detection needs no detail. |
| `INK_THRESHOLD` | 245 | Close to white so faint content still counts. Not the same as the printing threshold — see the `tspl-printer-protocol` skill. |
| `BLOCK_GAP` | 18 pt | Bigger than gaps *inside* a label, smaller than the gap separating a label from page furniture. This single number is what makes segmentation work. |
| `FULL_PAGE_COVERAGE` | 0.92 | Above this, the page *is* the label; skip cropping entirely. |
| `ASPECT_TOLERANCE` | 0.08 | Tight enough to exclude US Letter (0.773) from 4x6 (0.667). Loosening this past ~0.10 makes Letter look label-shaped and breaks detection. |
| `CROP_PADDING` | 4 pt | Breathing room so a border line isn't shaved off. |

`ASPECT_TOLERANCE` is the one most likely to be "improved" into breaking things.
It was 0.18 initially and classified US Letter as a label.

## Modes

`Mode.AUTO` crops only if the ink does not already fill the page. `Mode.CROP`
always crops. `Mode.FIT` never crops and shrinks the whole page onto the label —
this is the escape hatch when detection guesses wrong, and it is why the web app
offers all three rather than trying to be perfect.

Detection is a heuristic over documents we do not control. The honest design is
a good default plus a visible preview plus an easy override, not a cleverer
algorithm that fails silently.

## Rotation

A landscape label is rotated 90° so it fills portrait stock. Any `/Rotate`
attribute on the source page is baked into the content first
(`transfer_rotation_to_content`) so there is only ever one rotation to reason
about. pypdf needs the page attached to a writer before it will rewrite content
streams reliably — that is what the `staging` writer is for.

## Testing

`tests/conftest.py` builds synthetic PDFs with the distractors real carriers
print — `letter_with_label` has a full-width fold line and footer text
specifically so a naive bounding box fails the test.

`ink_coverage()` in `tests/test_normalize.py` is the key assertion: after
cropping, the label should cover >90% of the output page. That catches
"technically produced a 4x6" while the label sits tiny in one corner.

When adding a fixture, model a real layout rather than a convenient one. A
fixture that only passes because it is tidy proves nothing.
