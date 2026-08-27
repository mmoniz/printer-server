---
name: cups-print-chain
description: How CUPS, the PPD and our filter fit together, and how AirPrint discovery works. Use this whenever you touch scripts/make_ppd.py, cups/LabelPrinter.ppd, cups/rastertotspl, media sizes, resolution, queue options, or debug "the job vanished", "CUPS rendered the wrong size", or "the iPhone can't see the printer". Read this before editing the PPD — it is generated, and it defines the contract the filter depends on.
---

# The CUPS print chain

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
last hop.

## The PPD is generated — never hand-edit it

`cups/LabelPrinter.ppd` is output. `scripts/make_ppd.py` is the source.

```bash
python3 scripts/make_ppd.py            # regenerate in place
python3 scripts/make_ppd.py out.ppd    # write elsewhere
```

`tests/test_ppd.py::test_ppd_is_in_sync_with_its_generator` regenerates and
diffs, and CI runs `git diff --exit-code` on it, so a hand-edit fails the build.

## The PPD/filter contract

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

## Media names matter more than they look

Sizes use Adobe standard names (`4x6.Fullbleed`, not `w288h432`). This is not
cosmetic: CUPS maps standard names onto IPP/PWG media names, which is how an
iPhone ends up offering "4 x 6 in" in the print sheet. `.Fullbleed` declares no
unprintable margins, correct for thermal labels which print edge to edge.

`cupstestppd` will tell you the standard name for a size if you add one and it
complains.

## AirPrint discovery

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

## Where things land on the Pi

| Piece | Path |
|---|---|
| Filter | `$(cups-config --serverbin)/filter/rastertotspl` |
| Filter's Python | `/usr/local/lib/labelserver/labelserver/` |
| PPD | `/usr/share/ppd/labelserver/LabelPrinter.ppd` |
| Avahi record | `/etc/avahi/services/AirPrint-<queue>.service` |

The filter runs as the `lp` user under `cupsd`, **not** inside the web app's
venv. It imports `labelserver.tspl` from `/usr/local/lib/labelserver`, which is
why `install.sh` copies `__init__.py` and `tspl.py` there separately and why
`python3-numpy` is an apt dependency rather than only a venv one. If you add an
import to `tspl.py`, make sure it is available to the *system* Python or the
filter will fail with the job stuck in the queue.

## Debugging a stuck job

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
PYTHONPATH=. .venv/bin/python cups/rastertotspl 1 me test 1 "" < page.ras > out.tspl
```
