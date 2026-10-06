---
name: tspl-printer-protocol
description: How this repo drives the thermal label printer with TSPL, and the byte-level rules you must respect. Use this whenever you touch labelserver/tspl.py, cups/rastertotspl, the golden fixtures in tests/fixtures, or anything about DENSITY/GAP/BITMAP/PRINT commands, raster decoding, dot geometry, or "the label prints inverted / striped / wrong size". Read this BEFORE changing how bytes reach the printer — the mistakes here are silent and only show up on physical labels.
---
<!-- component-paths: labelserver/tspl.py cups/rastertotspl tests/test_tspl.py tests/fixtures/golden_* -->

# TSPL printer protocol

## Overview

The printer is a generic 203 dpi USB thermal label printer. Its vendor driver is
a rebadged build of the CUPS `rastertolabel` filter, shipped only as a
`Mach-O 64-bit x86_64` binary — wrong OS and wrong CPU for a Raspberry Pi. So
this repo reimplements the printer side.

**The printer speaks TSPL.** `labelserver/tspl.py` turns a CUPS raster into it;
`cups/rastertotspl` is the thin CUPS entry point around that.

This component owns the bytes that reach the printer. It leaves the PPD and
queue setup to `cups-print-chain`, and finding the label on a page to
`label-normalization`.

## How it works

### The wire format

A complete 4x6 job is a 174-byte preamble, a raw 1-bit bitmap, and a print
command:

```
SIZE 102 mm ,153 mm      <- whole mm, rounded UP (101.6 x 152.4 -> 102 x 153)
REFERENCE 0,0            <- print origin in dots; PPD offset options feed this
DIRECTION 0,0
GAP 3 mm,0 mm            <- gap-separated stock; 0 for continuous
OFFSET 0 mm
DENSITY 6                <- darkness, 0-15
SPEED 4                  <- inches/sec
SETC AUTODOTTED OFF
SETC PAUSEKEY ON
SETC WATERMARK OFF
CLS
BITMAP 0,0,102,1218,1,<124236 raw bytes>
PRINT 1,1                <- <copies>,1
```

### Geometry for 4x6

| Quantity | Value |
|---|---|
| Page | 288 x 432 pt |
| Dots @ 203 dpi | 812 x 1218 |
| Bytes per row | 102 (812 bits padded to 816) |
| Bitmap payload | 124,236 bytes |
| Whole job | 124,420 bytes |

### Input contract

`read_pages` accepts only what our PPD makes CUPS produce:

- little-endian CUPS raster v3 (`3SaR` magic), 1796-byte page headers
- uncompressed
- 8 bits per pixel, grayscale (`cupsColorSpace 0`)

Anything else raises `RasterError` rather than producing garbage on the printer.
If you change the PPD's `*Resolution` entry, this contract is what you are
changing — see the `cups-print-chain` skill.

### Per-job settings

`Settings.from_cups_options` parses CUPS's `argv[5]` option string. Unparseable
values fall back to the `Settings` field defaults, which mirror the PPD's.

### Verification: golden fixtures

`tests/fixtures/` holds real output captured from the vendor binary (run under
Rosetta on a Mac) next to the raster that produced it, gzipped:

```
golden_4x6.ras.gz   golden_4x6.tspl.gz    (812x1218)
golden_2x1.ras.gz   golden_2x1.tspl.gz    (406x203)
```

`tests/test_tspl.py` feeds our filter the same rasters and asserts the output is
**byte-identical**. This is the safety net for the whole printer side — if you
change the emitter and these still pass, you have not broken compatibility.

The 2x1 fixture exists specifically because it pins the round-up behaviour from
a second angle (144pt -> 51mm, 72pt -> 26mm).

### Working on this safely

- Run `uv run pytest tests/test_tspl.py` after any change.
- To inspect output by hand, `python -c` a page through `page_to_tspl` and look
  at the first ~200 bytes; the preamble is ASCII and readable.
- `scripts/testprint.sh --raw` writes known-good TSPL straight to
  `/dev/usb/lp0`, bypassing CUPS entirely. That is the tool for deciding whether
  a problem is the printer or the software above it.

## Gotchas

### The three rules that bite

These are the mistakes that cost real labels to discover. Each one is covered by
a test, so if you break one you'll see it before hardware does.

1. **The bitmap is inverted.** Bit `1` is white; bit `0` burns. Build rows as
   all-`0xFF` and *clear* bits where ink goes, rather than setting bits.

2. **Row padding must be white.** 812 dots pad to 816 bits, leaving 4 spare bits
   per row. Leave them `0` and every label gets a black stripe down its right
   edge. `pack_bitmap` appends a `np.ones` block before `np.packbits` for this.

3. **Media size rounds up, not to nearest.** 4x6in is 101.6 x 152.4mm. `round()`
   gives 152; the printer must be told 153. `Page.size_mm` uses `math.ceil`.

### The odd spacing in `SIZE 102 mm ,153 mm`

Symptom: it looks like a typo, with a space before the comma. Cause: that is
what the vendor emits, and the golden fixtures encode it. Don't "fix" it.

### Two different thresholds — don't confuse them

- `tspl.Settings.threshold` (default **127**) decides whether a gray pixel
  *burns*. This is a printing decision.
- `normalize.INK_THRESHOLD` (default **245**) decides whether a pixel *counts as
  content* when locating a label on a page. This is a detection decision and is
  deliberately much closer to white so faint marks still register.

Changing one because you were reasoning about the other is an easy mistake.

### A failing golden test means your change is wrong

**If a golden test fails, do not update the fixture to match your output.** The
fixtures are ground truth from the real driver. A diff means your change is
wrong, unless you are deliberately diverging — in which case say so explicitly
in the commit message and explain why.

### The vendor PPD's `AdjustHoriaontal` typo

Ours uses the correct spelling, and the parser accepts both so an old queue
keeps working.

## Decisions

Newest first. Don't delete entries. When a decision is replaced, mark it superseded.

### 2026-08-09 — Reimplement the vendor filter as `rastertotspl`

- **Context:** The vendor driver is a rebadged `rastertolabel` build shipped only as a `Mach-O 64-bit x86_64` binary, which can't run on a Raspberry Pi.
- **Decision:** `labelserver/tspl.py` emits TSPL from a CUPS raster, and `cups/rastertotspl` is the thin CUPS entry point around it.
- **Why:** There was no other way to run the printer from a Pi.
- **Alternatives considered:** > TODO(history): nothing in the repo records what else was tried (an Intel emulation layer on the Pi, other generic drivers); ask the owner.
- **Status:** active

### 2026-08-09 — Golden fixtures captured from the vendor binary are ground truth

- **Context:** A TSPL mistake is silent until a physical label comes out wrong.
- **Decision:** `tests/fixtures/` holds output captured from the vendor binary under Rosetta next to the raster that produced it, and `tests/test_tspl.py` requires byte-identical output. A failing golden test means the change is wrong; the fixture isn't updated to match.
- **Why:** The fixtures are the only compatibility proof that doesn't need the printer. A second (2x1) fixture pins the round-up behaviour from another angle.
- **Alternatives considered:** > TODO(history): not recorded.
- **Status:** active

### 2026-08-09 — Option parsing is forgiving

- **Context:** CUPS passes options as one string in `argv[5]`.
- **Decision:** `Settings.from_cups_options` falls back to the `Settings` defaults for values it can't parse, and accepts both `AdjustHoriaontal` (the vendor's typo) and `AdjustHorizontal`.
- **Why:** A bad option should not turn into a failed print job, and an old queue should keep working after the PPD spelling was corrected.
- **Alternatives considered:** Raising on a bad option, which fails the job.
- **Status:** active

## Trade-offs

We emit the vendor's exact preamble, quirks included, rather than a minimal
TSPL job, so a change that would be harmless on paper still has to match the
golden bytes. We accept only the one raster format our PPD produces, so a PPD
change to resolution or colour space is also a filter change. Both are the price
of a safety net that doesn't need hardware. We'd reconsider if a second printer
model with different TSPL needs to be supported.

## Related

- `cups-print-chain`: the PPD that defines the raster this filter accepts.
- `label-normalization`: where `INK_THRESHOLD` lives.
- `pi-deployment`: `scripts/testprint.sh` and tuning against real stock.
