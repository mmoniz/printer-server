# printer-server

A home print server for 4x6 shipping labels. Runs on an old Raspberry Pi 2 with
a USB thermal label printer, and lets everyone in the house print to it:

- **AirPrint** from iPhones and iPads — share sheet → Print, nothing to install
- **A normal network printer** on Macs and PCs over IPP
- **A web page** on the Pi for uploading a label PDF from any device

Everything stays on the LAN. No cloud, no port forwarding, no accounts.

```
 iPhone/iPad ──AirPrint──┐
 Mac/PC ────────IPP──────┤
                         ▼
                ┌─────────────────┐  pdftoraster   ┌───────────────┐
                │  CUPS (spooler) │───────────────►│ rastertotspl  │──► USB
                └─────────────────┘ 812x1218 gray  │  (our filter) │
                         ▲                         └───────────────┘
 any browser ──► web app ┘
                (upload, crop to label, preview)
```

## Installing on the Pi

Raspberry Pi OS Lite (Bookworm, 32-bit) on a Pi 2, printer on USB:

```bash
git clone https://github.com/mmoniz/printer-server.git
cd printer-server
sudo ./scripts/install.sh
```

That installs CUPS and Avahi, creates the `labels` queue against whatever USB
printer it finds, registers the filter and PPD, publishes the AirPrint record,
and starts the web app on port 80. It is safe to re-run.

Then open `http://<pi-hostname>.local/`.

## The printer

The target is one of the generic 4x6 USB thermal printers sold on Amazon —
203 dpi, USB descriptor `Thermal Label / Printer`, sold under many names. The
bundled driver is a rebadged build of the open-source CUPS `rastertolabel`
filter, originally by `zhougf@beeprt.com`.

**The vendor driver cannot be used on a Pi.** It ships as a single
`Mach-O 64-bit x86_64` binary — wrong OS and wrong CPU. This repo replaces it.

### The protocol

The printer speaks **TSPL**. A 4x6 label at 203 dpi is 812x1218 dots, and the
whole job is a 174-byte preamble, a raw 1-bit bitmap and a print command:

```
SIZE 102 mm ,153 mm        <- ceil() of 101.6 x 152.4mm; the vendor rounds up
REFERENCE 0,0              <- print origin; the PPD's offset options feed this
DIRECTION 0,0
GAP 3 mm,0 mm              <- gap-separated stock
OFFSET 0 mm
DENSITY 6                  <- darkness, 0-15
SPEED 4                    <- inches/sec
SETC AUTODOTTED OFF
SETC PAUSEKEY ON
SETC WATERMARK OFF
CLS
BITMAP 0,0,102,1218,1,<124236 raw bytes>
PRINT 1,1
```

Three details cause most of the pain if you reimplement this:

1. **The bitmap is inverted.** Bit `1` is white; bit `0` burns.
2. **Rows pad to a byte boundary**, and the padding bits must be white. 812
   dots pad to 816 bits — get those 4 bits wrong and every label gets a black
   stripe down its right edge.
3. **The media size is whole millimetres, rounded up.** 4x6in is 101.6 x
   152.4mm, and the printer must be told `102 mm ,153 mm`.

### How we know this is right

[`tests/fixtures/`](tests/fixtures) holds real output captured from the vendor
binary (run under Rosetta on a Mac) alongside the raster that produced it. The
test suite renders the same rasters and asserts the result is **byte-identical**
to what the vendor driver emits — 124,420 bytes for the 4x6 fixture, matching
exactly. Our PPD was also checked to produce the same raster geometry CUPS
handed the vendor driver, so the whole chain is verified end to end.

If a change ever breaks printer compatibility, those tests fail loudly.

## Repo layout

| Path | What it is |
|---|---|
| `labelserver/tspl.py` | CUPS raster → TSPL. The printer-specific core. |
| `labelserver/normalize.py` | Uploads → a 4x6 label PDF. |
| `labelserver/printing.py` | Submitting to and querying CUPS via `lp`. |
| `labelserver/app.py` | The Flask web app. |
| `cups/rastertotspl` | CUPS filter entry point. |
| `cups/LabelPrinter.ppd` | Generated — edit `scripts/make_ppd.py`. |
| `scripts/install.sh` | One-shot Pi setup. |
| `scripts/testprint.sh` | Test label, optionally bypassing CUPS entirely. |
| `.claude/skills/` | Component guides — see below. |

## Component guides

Each non-obvious component has a skill under `.claude/skills/`, holding the
reasoning and the traps that are not visible from the code alone. Claude Code
picks these up automatically; they read fine as plain Markdown too.

| Skill | Covers |
|---|---|
| `tspl-printer-protocol` | The wire format, the inverted bitmap, row padding, golden fixtures |
| `label-normalization` | Finding a label on a carrier page, the detection constants |
| `cups-print-chain` | PPD generation, the PPD/filter contract, AirPrint discovery |
| `label-web-app` | Routes, the preview-then-print flow, testing against stubbed CUPS |
| `pi-deployment` | Installing, the hardware failure modes, tuning against real stock |

## Fitting labels to 4x6

Carrier PDFs come in three shapes, and the web app handles each:

- **already 4x6** — printed as-is
- **US Letter with the label in a corner** — the page is segmented into content
  blocks and the label-shaped one is cropped out. This matters because a plain
  ink bounding box also catches the full-width fold line and the terms text
  below it, which would shrink the label to a third of the stock.
- **a photo or screenshot** — scaled to fit

The transform is vectorial: the source page is placed onto a 4x6 page with a
transformation matrix rather than being rasterized, so barcodes stay sharp
enough to scan.

Every job shows a preview before printing, with **Automatic**, **Always crop**
and **Whole page** modes for when the guess is wrong.

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest
```

The tests stub out CUPS, so they run anywhere — no printer needed.

To run the web app locally against an existing queue:

```bash
LABELSERVER_QUEUE=my_queue .venv/bin/python -m flask --app labelserver.app run --port 8080
```

## Troubleshooting

**Nothing prints, and the job sits in the queue.** Find out whether the printer
or the software is at fault:

```bash
./scripts/testprint.sh --raw
```

That writes TSPL straight to `/dev/usb/lp0` with no CUPS involved. If a label
comes out, the printer is fine and the problem is in the queue or the filter
(`journalctl -u cups -n 50`). If nothing comes out, it is the printer, cable or
power.

**Labels creep or come out short.** The `GAP 3 mm` setting assumes
gap-separated stock. For continuous rolls, set Media Tracking to Continuous on
the queue.

**The print is offset.** Use the Horizontal/Vertical Offset options on the
queue; they feed TSPL's `REFERENCE`, so it is a settings change, not a code one.

**The Pi disappears from the network after a while.** That is the USB wifi
dongle sleeping. `install.sh` installs a unit that disables power saving —
check it ran with `systemctl status wifi-powersave-off`.

**The printer stops responding after being idle.** Some cheap printers suspend
on USB and never wake. `scripts/99-labelprinter.rules` disables autosuspend for
printer-class devices.

## Status

The printer driver, PPD, normalizer, web app and installer are written and
tested. None of it has touched real hardware yet — the Pi is still in a drawer.
The parts that need a physical printer to confirm are the `GAP` setting for the
actual label stock, the print origin, and the wifi dongle's chipset support.
