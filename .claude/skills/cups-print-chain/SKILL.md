---
name: cups-print-chain
description: How CUPS, the PPD and our filter fit together, and how AirPrint discovery works. Use this whenever you touch scripts/make_ppd.py, cups/LabelPrinter.ppd, cups/rastertotspl, media sizes, resolution, queue options, or debug "the job vanished", "CUPS rendered the wrong size", or "the iPhone can't see the printer". Read this before editing the PPD — it is generated, and it defines the contract the filter depends on.
---
<!-- component-paths: scripts/make_ppd.py scripts/airprint.sh cups/LabelPrinter.ppd tests/test_ppd.py -->

# The CUPS print chain

## Overview

Everything printable converges on one CUPS queue, so the printer-specific code
exists exactly once and both AirPrint and the web app inherit it.

```
 iPhone/iPad ──AirPrint──┐
 Mac/PC ────────IPP──────┤
                         ▼
                ┌─────────────────┐  pdftoraster   ┌───────────────┐
                │  CUPS (spooler) │───────────────►│ rastertotspl  │──► USB
                └─────────────────┘ 812x1218 gray  │  (our filter) │
                         ▲                         └───────────────┘
 any browser ──► web app ┘
```

CUPS does the PDF→raster step with its own `pdftoraster`. We only supply the
last hop. This component owns the PPD, its generator, and the AirPrint
advertisement. The filter's bytes belong to `tspl-printer-protocol`, and the
web app's side of talking to CUPS belongs to `label-web-app`.

## How it works

### The PPD is generated

`cups/LabelPrinter.ppd` is output. `scripts/make_ppd.py` is the source.

```bash
python3 scripts/make_ppd.py            # regenerate in place
python3 scripts/make_ppd.py out.ppd    # write elsewhere
```

`tests/test_ppd.py::test_ppd_is_in_sync_with_its_generator` regenerates and
diffs, and CI runs `git diff --exit-code` on it, so a hand-edit fails the build.

### The PPD/filter contract

This block in the PPD is what makes the filter's input predictable:

```
*Resolution 203dpi/203 dpi: "<</HWResolution[203 203]/cupsBitsPerColor 8
  /cupsRowCount 8/cupsRowFeed 0/cupsRowStep 0/cupsColorSpace 0>>setpagedevice"
```

`cupsColorSpace 0` is grayscale and `cupsBitsPerColor 8` gives 8bpp — exactly
what `tspl.read_pages` accepts, and it rejects anything else rather than sending
garbage to the printer. Change the resolution or colour space here and you must
change the filter's contract too. `tests/test_ppd.py` asserts both ends agree,
including that the PPD's `*DefaultDarkness` matches `tspl.Settings.darkness`.

### AirPrint discovery

Two things must line up, and both are easy to forget:

1. **`*cupsUrfSupported: "V1.4,W8,RS203,DM1,CP1"`** in the PPD. iOS only offers
   printers that declare a URF raster format. `W8` = 8-bit grayscale, `RS203` =
   203 dpi, `DM1` = no duplex.

2. **The Avahi service record** written by `scripts/airprint.sh`, advertising
   `_ipp._tcp` with the `_universal` subtype plus TXT records (`rp`, `pdl`,
   `URF`, `adminurl`). CUPS advertises queues on its own, but iOS is fussy
   enough that writing the record explicitly is the dependable route.

If an iPhone cannot see the printer, check the TXT records first — that is
almost always where the problem is. `avahi-browse -rt _ipp._tcp` on the Pi shows
what is actually being published.

### Where things land on the Pi

| Piece | Path |
|---|---|
| Filter | `$(cups-config --serverbin)/filter/rastertotspl` |
| Filter's Python | `/usr/local/lib/labelserver/labelserver/` |
| PPD | `/usr/share/ppd/labelserver/LabelPrinter.ppd` |
| Avahi record | `/etc/avahi/services/AirPrint-<queue>.service` |

### Debugging a stuck job

```bash
lpstat -p labels          # is the queue idle, or disabled?
lpstat -o labels          # what is queued
journalctl -u cups -n 50  # filter stderr lands here
cupsenable labels         # a failed filter often disables the queue
```

A filter that exits non-zero disables the queue, so a single bad job can look
like a dead printer. `cupsenable` brings it back.

To test the filter directly without CUPS:

```bash
PYTHONPATH=. uv run python cups/rastertotspl 1 me test 1 "" < page.ras > out.tspl
```

## Gotchas

### Never hand-edit the PPD

Symptom: CI fails the PPD staleness check, or `test_ppd_is_in_sync_with_its_generator`.
Cause: the file was edited instead of `scripts/make_ppd.py`. Fix: edit the
generator and rerun it.

### Media names matter more than they look

Sizes use Adobe standard names (`4x6.Fullbleed`, not `w288h432`). This is not
cosmetic: CUPS maps standard names onto IPP/PWG media names, which is how an
iPhone ends up offering "4 x 6 in" in the print sheet. `.Fullbleed` declares no
unprintable margins, correct for thermal labels which print edge to edge.

`cupstestppd` will tell you the standard name for a size if you add one and it
complains.

### The filter runs outside the web app's venv

The filter runs as the `lp` user under `cupsd`, **not** inside the web app's
venv. It imports `labelserver.tspl` from `/usr/local/lib/labelserver`, which is
why `install.sh` copies `__init__.py` and `tspl.py` there separately and why
`python3-numpy` is an apt dependency rather than only a venv one. If you add an
import to `tspl.py`, make sure it is available to the *system* Python or the
filter will fail with the job stuck in the queue.

## Decisions

Newest first. Don't delete entries. When a decision is replaced, mark it superseded.

### 2026-08-09 — One CUPS queue serves AirPrint, IPP and the web app

- **Context:** Family members print three ways (iPhone, Mac/PC, the web page).
- **Decision:** Everything converges on a single CUPS queue, and our code supplies only the last hop (`rastertotspl`).
- **Why:** The printer-specific code exists exactly once, and both AirPrint and the web app inherit it.
- **Alternatives considered:** > TODO(history): not recorded.
- **Status:** active

### 2026-08-09 — Generate the PPD from `scripts/make_ppd.py`

- **Context:** The PPD encodes the contract the filter depends on (resolution, colour space, darkness default).
- **Decision:** `cups/LabelPrinter.ppd` is generated output, checked in, and verified in sync by a test and by CI.
- **Why:** A test can then assert both ends of the contract agree, which a hand-edited file can't guarantee.
- **Alternatives considered:** > TODO(history): not recorded.
- **Status:** active

### 2026-08-09 — Write the Avahi record explicitly

- **Context:** CUPS advertises queues itself, but iOS is picky about what it will offer.
- **Decision:** `scripts/airprint.sh` writes the `_ipp._tcp` record with the `_universal` subtype and TXT records, alongside the PPD's `*cupsUrfSupported` line.
- **Why:** It's the dependable route to the iPhone seeing the printer.
- **Alternatives considered:** Relying on CUPS's own advertisement.
- **Status:** active

## Trade-offs

We own a generated PPD and a hand-written Avahi record that CUPS could mostly
produce itself, in exchange for iOS discovery that works. The filter's Python
lives in two places on the Pi (the app's venv and `/usr/local/lib/labelserver`),
which is the price of running it as `lp` under the system Python. We'd
reconsider if CUPS's own driverless support (IPP Everywhere) ever covered a
generic TSPL printer.

## Related

- `tspl-printer-protocol`: the raster this PPD makes CUPS produce, and the filter that reads it.
- `pi-deployment`: `install.sh` places the filter, PPD and Avahi record, and diagnoses a dead queue.
- `label-web-app`: how the web app submits to this queue.
