---
name: label-normalization
description: How uploads become 4x6 labels — finding the label on a carrier page, cropping, rotating and scaling without rasterizing. Use this whenever you touch labelserver/normalize.py, work on crop/fit modes, block segmentation, ink detection, barcode sharpness, or investigate complaints like "the label printed tiny", "it cropped the wrong thing", or "the barcode won't scan". Read this before changing any detection constant.
---
<!-- component-paths: labelserver/normalize.py tests/test_normalize.py tests/conftest.py tests/fixtures/ups_multiband_redacted.pdf -->

# Label normalization

## Overview

`labelserver/normalize.py` turns whatever a family member uploads into a
single 4x6 page (288 x 432 pt) that CUPS can rasterize.

It owns locating the label on a carrier page, cropping, rotating and scaling,
and the preview image. It leaves talking to CUPS to `label-web-app` and the
printer's bytes to `tspl-printer-protocol`.

## How it works

### Keep it vectorial

The source page is placed onto a 4x6 page with a **transformation matrix**
(`pypdf.Transformation`), not rendered to an image and pasted. This matters:
these are shipping labels, and a courier's scanner has to read the barcode. Every
rasterization step costs sharpness.

Rasterization is used *only* to locate the label and to build the preview
image the user sees. It never touches what gets printed.

If you find yourself reaching for `Image` to build print output, stop and find a
transform-based way instead.

### Finding the label

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
- remaining blocks score by area, multiplied by `LABEL_SHAPED_WEIGHT` (3x) if the
  shape is label-like

So a big label-shaped block beats a slightly larger square blob, and a
full-width hairline never wins regardless of how wide it is.

### The constants, and why they are what they are

| Constant | Value | Reasoning |
|---|---|---|
| `DETECT_DPI` | 72 | 1px == 1pt, so the coordinate maths is obvious. Detection needs no detail. |
| `INK_THRESHOLD` | 245 | Close to white so faint content still counts. Not the same as the printing threshold — see the `tspl-printer-protocol` skill. |
| `BLOCK_GAP` | 18 pt | Bigger than gaps *inside* a label, smaller than the gap separating a label from page furniture. This single number is what makes segmentation work. |
| `FULL_PAGE_COVERAGE` | 0.92 | Above this, the page *is* the label; skip cropping entirely. |
| `ASPECT_TOLERANCE` | 0.08 | Tight enough to exclude US Letter (0.773) from 4x6 (0.667). Loosening this past ~0.10 makes Letter look label-shaped and breaks detection. |
| `CROP_PADDING` | 4 pt | Breathing room so a border line isn't shaved off. |
| `LABEL_SHAPED_WEIGHT` | 3.0 | How much a label-shaped block outweighs a same-sized blob of any other shape. |
| `POINTS_PER_INCH`, `PRINT_DPI`, `QUARTER_TURN` | 72, 203.0, 90 | Unit conversions and the printer's native resolution (image uploads are assumed to be 203 dpi so a label-sized image lands at label size). Named so no bare `72` or `90` appears in the maths. |
| `REVIEW_PREVIEW_WIDTH_PX` | 420 | Width of the preview on the review page; the web upload and the mail poller both use it. |

### Modes

`Mode.AUTO` crops only if the ink does not already fill the page. `Mode.CROP`
always crops. `Mode.FIT` never crops and shrinks the whole page onto the label —
this is the escape hatch when detection guesses wrong, and it is why the web app
offers all three rather than trying to be perfect.

### Rotation

A landscape label is rotated 90° so it fills portrait stock. Any `/Rotate`
attribute on the source page is baked into the content first
(`transfer_rotation_to_content`) so there is only ever one rotation to reason
about. pypdf needs the page attached to a writer before it will rewrite content
streams reliably — that is what the `staging` writer is for.

`normalize_pdf` gates the rotation guess on `label_shaped` (already computed for
the "does this look like a label" preview warning) whenever an actual
sub-region was cropped out; only a whole untouched page (`Mode.FIT`, or
`Mode.AUTO`'s full-page-coverage shortcut) skips this check, since there's no
segmentation guess to distrust there.

### Testing

`tests/conftest.py` builds synthetic PDFs with the distractors real carriers
print — `letter_with_label` has a full-width fold line and footer text
specifically so a naive bounding box fails the test.

`ink_coverage()` in `tests/test_normalize.py` is the key assertion: after
cropping, the label should cover >90% of the output page. That catches
"technically produced a 4x6" while the label sits tiny in one corner.

When adding a fixture, model a real layout rather than a convenient one. A
fixture that only passes because it is tidy proves nothing.

## Gotchas

### `ASPECT_TOLERANCE` is the one most likely to be "improved" into breaking things

Symptom: a Letter-sized page gets treated as a label and prints at a third of
its size. Cause: the tolerance was loosened. It was 0.18 initially and
classified US Letter as a label. Keep it at 0.08.

### Rotating on aspect alone sends a barcode sideways

Width-vs-height alone can't say *which way* to turn something — only that it
isn't tall like the target. That's fine when the crop is a clean 4x6/6x4
match (there's really only one sane orientation for that shape), but a real
UPS return label surfaced the failure mode: a tall page laid out in
horizontal bands (sender, ship-to, barcode block, footer) with real
whitespace between them, wide enough that block segmentation split it apart
and picked one wide-but-not-label-shaped band as "the label." Width > height
made the old rule rotate it 90° with no way to know if that was the right
direction — it wasn't, and the tracking barcode came out sideways.

Two tests guard this: `test_unconfident_crop_is_not_rotated_blind` is a minimal
synthetic reproduction of the shape; `test_real_ups_multiband_label_is_not_rotated_sideways`
runs the real carrier PDF that surfaced the bug (`tests/fixtures/ups_multiband_redacted.pdf`
-- names, addresses and the tracking/routing numbers replaced with placeholder
text, everything else, including page size and every gap between sections,
untouched, since that's what triggers the misfire). See both before touching
this logic again, and don't relax it back to "always rotate on aspect alone."

### A multi-band label can still be cropped badly

This doesn't fix the crop itself for a label like that — segmentation may
still split it into bands, and the constants that correctly exclude a fold
line (`MIN_BLOCK_COVERAGE`, `MAX_BLOCK_ELONGATION`) can't be tightened
further without risking exactly the false-positive Letter-page match
`ASPECT_TOLERANCE` already had to be tuned away from. `Mode.FIT` remains the
answer when a layout like this guesses wrong: rotating a mis-selected region
made it look broken (sideways); merely not-cropping-well is a small,
honest miss the preview screen catches, not a silent one.

## Decisions

Newest first. Don't delete entries. When a decision is replaced, mark it superseded.

### 2026-10-05 — Name the unit conversions and the preview width

- **Context:** The magic-value audit (ENG-15) found a bare `72` in four places, `203.0`, `90`, `3.0`, and a `420` repeated in the web app and the mail poller.
- **Decision:** They became `POINTS_PER_INCH`, `PRINT_DPI`, `QUARTER_TURN`, `LABEL_SHAPED_WEIGHT` and `REVIEW_PREVIEW_WIDTH_PX`, with values unchanged.
- **Why:** One definition each, next to the code that owns it. `render_preview`'s own default of 400 was left alone, since tests rely on it.
- **Alternatives considered:** One shared constants module, which would separate each value from the code that explains it.
- **Status:** active

### 2026-08-29 — Rotate a crop only when it is confidently label-shaped

- **Context:** A real UPS return label (tall page, horizontal bands) was split by segmentation, and one wide band was rotated 90° on aspect alone, sending the tracking barcode sideways. Commit dd0dcc1.
- **Decision:** `normalize_pdf` gates rotation on `label_shaped` whenever a sub-region was cropped. Whole untouched pages skip the check. `tests/fixtures/ups_multiband_redacted.pdf` keeps the real layout as a regression fixture (e8b7e20).
- **Why:** Not rotating is a small miss the preview catches; rotating the wrong way looks broken.
- **Alternatives considered:** Tightening `MIN_BLOCK_COVERAGE` or `MAX_BLOCK_ELONGATION`, which risks the Letter-page false positive that `ASPECT_TOLERANCE` already had to be tuned away from.
- **Status:** active

### 2026-08-09 — Place pages with a transformation matrix, and find the label by block segmentation

- **Context:** Shipping labels need scannable barcodes, and a plain ink bounding box also caught the fold line and terms text, so the label printed at roughly a third of its size (caught by the preview screen during development).
- **Decision:** Never rasterize print output: place the source page with `pypdf.Transformation`. Locate the label with projection-based block segmentation scored by area, with penalties for stray marks and elongated rules.
- **Why:** Each rasterization step costs barcode sharpness, and segmentation survives the distractors real carriers print.
- **Alternatives considered:** A plain ink bounding box, which failed on a real layout. `ASPECT_TOLERANCE` started at 0.18, which classified US Letter as a label, and was tightened to 0.08.
- **Status:** active

### 2026-08-09 — Detection is a heuristic, so offer three modes and a preview

- **Context:** Documents come from carriers we don't control.
- **Decision:** `Mode.AUTO`, `Mode.CROP` and `Mode.FIT` are all exposed in the web app, with a visible preview and an easy override.
- **Why:** The honest design is a good default plus a visible preview plus an easy override, not a cleverer algorithm that fails silently.
- **Alternatives considered:** A smarter detector with no override.
- **Status:** active

## Trade-offs

Segmentation is tuned on the layouts we have seen, so a new carrier layout can
mis-crop, and the cost is borne by a person looking at the preview and picking
`Mode.FIT`. In return nothing is rasterized for printing and barcodes stay
sharp. We'd reconsider the constants only with a new real carrier PDF added as
a fixture first.

## Related

- `tspl-printer-protocol`: the printing threshold (127) that is deliberately different from `INK_THRESHOLD`.
- `label-web-app`: the review page that shows the preview, and the mode picker.
- `url-link-intake`, `mail-intake`: the other ways a document arrives here.
