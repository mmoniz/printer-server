# Family Label Print Server — Plan

A print server on a Raspberry Pi 2 that lets everyone on the home wifi print
4x6 shipping labels (UPS/USPS) to a USB thermal label printer.

## Goals

- iPhones/iPads print via AirPrint (share sheet → Print, no app install)
- Macs/PCs see it as a normal network printer (IPP, auto-discovered on macOS)
- A web page on the Pi where anyone uploads a PDF label and prints it, with
  automatic rotate/crop/scale to 4x6
- LAN only. No port forwarding, no cloud.

## Hardware

| Piece | Notes |
|---|---|
| Raspberry Pi 2 | armv7 (32-bit), 1 GB RAM — plenty for CUPS + a small web app |
| USB wifi dongle | Verify chipset on first boot; ethernet as fallback |
| Thermal label printer | Generic 4x6, USB, 203 dpi |

Use a solid 2A+ supply — Pi 2 + wifi dongle + USB printer on an undersized
supply is a classic source of flaky behavior.

## Printer: identified and decoded ✅

The printer is already installed on the Mac (`Thermal_Label_Printer`), which
let us reverse-engineer it without plugging anything in.

**What it is.** The macOS driver is a rebadged build of the open-source CUPS
`rastertolabel` filter, from `/Library/Printers/LabelPrinter/`:

- PPD: `*Manufacturer: "LabelPrinter"`, `*ModelName: "Label Printer"`,
  `*cupsModelNumber: 20`, `*DefaultResolution: 203dpi`
- Device URI `usb://Thermal%20Label/Printer` — a generic USB descriptor, so on
  Linux it binds to `usblp` (`/dev/usb/lp0`) and CUPS's stock `usb://` backend
  works with no vendor code
- 4x6 is supported: `*PageSize w288h432/4.00x6.00"` (288 x 432 pt)

**The vendor driver cannot be reused on the Pi.** `file` reports the filter as
`Mach-O 64-bit executable x86_64` — wrong OS *and* wrong CPU. This is the usual
dead end with cheap thermal printers, and why we write our own filter.

**Language: TSPL.** Extracted from the binary and confirmed by running it. The
complete output for a 4x6 page is a 174-byte preamble, a raw 1-bit bitmap, and
a print command:

```
SIZE 102 mm ,153 mm      <- ceil() of 101.6 x 152.4mm; vendor rounds up
REFERENCE 0,0
DIRECTION 0,0
GAP 3 mm,0 mm            <- gap-separated label stock
OFFSET 0 mm
DENSITY 6                <- PPD *DefaultDarkness
SPEED 4                  <- PPD *DefaultzePrintRate
SETC AUTODOTTED OFF
SETC PAUSEKEY ON
SETC WATERMARK OFF
CLS
BITMAP 0,0,102,1218,1,<124236 raw bytes>
PRINT 1,1
```

Geometry at 203 dpi: **812 x 1218 dots**, 102 bytes/row (812 bits padded to
816). Critically, **the bitmap is inverted — bit 1 = white, bit 0 = burn** —
and the 4 padding bits per row must be 1, or you get a black stripe down the
right edge of every label.

**Verified reimplementation.** [rastertotspl.py](rastertotspl.py) is a ~100-line
CUPS filter that turns a CUPS raster into the above. Fed the same raster, it
produces output **byte-identical to the vendor's binary** (all 124,420 bytes).
It uses numpy `packbits` for the pack step, so it runs in ~40 ms rather than
looping over a million pixels in Python — which matters on a Pi 2.

This means the riskiest part of the project is already done and validated.

## Architecture

```
 iPhone/iPad ──AirPrint──┐
 Mac/PC ────────IPP──────┤
                         ▼
                ┌─────────────────┐   pdftoraster    ┌──────────────┐
                │  CUPS (spooler) │─────────────────►│rastertotspl  │──► USB
                └─────────────────┘  (812x1218 gray) │  (our filter)│
                         ▲                           └──────────────┘
 any browser ──► Flask web app ── lp ──┘
                (upload + normalize)
```

Both entry paths converge on one CUPS queue, so the printer-specific code
exists exactly once and everything inherits it.

- **OS**: Raspberry Pi OS Lite (32-bit), headless, SSH on.
- **CUPS**: hosts the queue. We supply a PPD (adapted from the vendor's, which
  is plain text and portable) whose `*cupsFilter` points at `rastertotspl.py`.
  CUPS's own `pdftoraster` does PDF → 8-bit gray raster; our filter does the
  rest. Default media 4x6, `Shared Yes`, listening on the LAN.
- **Avahi**: advertises the queue as AirPrint (`_ipp._tcp` + `_universal`
  subtype, URF TXT records) and IPP Everywhere. This is what makes iPhones and
  Macs find it with zero client setup.
- **Web app** (Flask + systemd, port 80):
  - Upload PDF/PNG → preview → Print
  - Normalization: carrier PDFs are often 8.5x11 with the label in one
    quadrant — detect and crop to the label, rotate to portrait, scale to 4x6
  - Submits via `lp -d labels`; queue view with a cancel button
- **Density/speed** are per-job knobs from the PPD, so we can expose a
  darkness slider on the web page without touching the filter.

## Known unknowns (need the hardware)

- **`GAP 3 mm`** assumes gap-separated stock. Fanfold 4x6 usually is; if yours
  is continuous or black-mark, this line changes (`GAP 0,0` / `BLINE`). One
  test print tells us.
- **Print origin** — cheap printers vary by a millimetre or two. The PPD's
  `AdjustHorizontal` / `AdjustVertical` options map to TSPL `REFERENCE`, so
  it's a config fix, not a code fix.
- **Wifi dongle chipset** — some need firmware packages on Pi OS.

## Milestones

1. ~~Identify the printer and its language~~ — **done, byte-verified**
2. **Pi base setup** — OS, wifi, static DHCP reservation, `labels.local` mDNS
3. **Raw print path** — pipe a known-good TSPL file straight to `/dev/usb/lp0`
   and confirm a correctly-sized label comes out. Tunes GAP and origin.
4. **CUPS + our filter** — install PPD + `rastertotspl.py`, print from a Mac
5. **AirPrint** — Avahi records, print from an iPhone share sheet
6. **Web app** — upload → normalize → preview → print
7. **Hardening** — reboot test, then the reliability list below

## Reliability details

- Disable wifi power management (`iw dev wlan0 set power_save off`) or the Pi
  will vanish from the network when idle.
- Some cheap printers drop off USB when idle — if seen, a udev rule disabling
  autosuspend for its USB ID fixes it.
- Keep logs off the SD card (volatile journald); Pi 2 SD cards die from churn.
- `Restart=always` on the web app service.

## Non-goals (for now)

- Printing from outside the LAN (Tailscale could add this later)
- Accounts/auth on the web page — trusted home LAN
- Regular-paper printing

---

### Note on the reverse-engineering session

The Mac was described as a Linux machine; it's actually macOS on Apple silicon
(`Darwin 25.5.0 arm64`). That turned out not to matter — Rosetta 2 ran the
x86_64 vendor filter, which is how we captured ground-truth output to diff
against. Reproducing it on the Pi is what needed the rewrite.
