---
name: pi-deployment
description: Installing and running the label server on the Raspberry Pi, plus the hardware failure modes this setup is prone to. Use this whenever you touch scripts/install.sh, the systemd units, the udev rules, scripts/testprint.sh, or diagnose "nothing prints", "the Pi vanished from the network", "the printer stopped responding", or labels coming out misaligned. Read this before adding a runtime dependency — the Pi 2 constrains what can be installed.
---

# Deploying on the Pi

Target: Raspberry Pi 2 (armv7, 32-bit, 1 GB RAM), Raspberry Pi OS Lite
Bookworm, USB wifi dongle, printer on USB.

```bash
sudo ./scripts/install.sh
```

Idempotent — safe to re-run after any change. `QUEUE=` and `APP_USER=`
override the defaults.

## What install.sh does

Packages → `labelserver` system user (in `lpadmin`) → app to `/opt/labelserver`
→ filter and PPD into the CUPS directories → queue against the first USB
printer found → CUPS LAN sharing → AirPrint record → systemd service → udev and
wifi tweaks.

If no USB printer is detected it warns and continues, so the rest of the setup
still completes. Plug the printer in and re-run.

## The Pi 2 constrains dependencies

The venv is created with `--system-site-packages` so it reuses the **apt** builds
of numpy and Pillow. Compiling those from source on a Pi 2 takes a very long
time. Before adding a runtime dependency, check it has an armv7 wheel or an apt
package — `pypdfium2` was chosen partly because it publishes
`manylinux_2_17_armv7l`.

The CUPS filter is a separate case: it runs as the `lp` user under the **system**
Python, not the venv. Its imports must be satisfied system-wide, which is why
`python3-numpy` is an apt dependency and `install.sh` verifies `import numpy`
before finishing.

## Five failure modes worth knowing about

These are the ones that cost an evening if you don't know them.

**The Pi disappears from the network after being idle.** USB wifi dongles sleep,
and the link only comes back when something on the Pi sends traffic outward — so
it looks dead from every other device. `wifi-powersave-off.service` disables
power saving on every `wl*` interface at boot. Check with
`systemctl status wifi-powersave-off`.

**The wifi dongle fails to reassociate after a cold boot.** Separate from the
idle-sleep case above: after a power event, the dongle can come up without
ever getting back onto the network, and a `systemctl status` on the Pi itself
would show everything healthy since only the link is down. This is
distinguishable from an app-level problem because it takes *everything* down —
including `sshd`, which doesn't depend on the labelserver app — not just the
web app. `network-watchdog.timer` pings the gateway every few minutes and
escalates: restart networking, then reboot if that doesn't bring it back.
Check with `systemctl status network-watchdog.timer` and
`journalctl -u network-watchdog -n 50`.

**The printer stops responding after being idle.** Cheap thermal printers let
the host suspend them and then fail to wake, which presents as a queue that
accepts a job and never prints it. `99-labelprinter.rules` disables autosuspend
for printer-class USB devices.

**A single bad job looks like a dead printer.** A filter exiting non-zero
disables the whole queue. `cupsenable labels` brings it back;
`journalctl -u cups -n 50` says why it failed.

**The Pi never comes back after a power event.** All the app services
(`labelserver`, `cups`, `avahi-daemon`, `wifi-powersave-off`) are `enable
--now`'d, so a clean boot restarts everything on its own — but that assumes
the boot itself succeeds. An unclean shutdown (a power outage) is exactly
when a Pi 2's SD card is likely to corrupt its filesystem and stall on an
fsck, and there's nobody there to power-cycle it. `install.sh` enables the
Pi's hardware watchdog (`dtparam=watchdog=on` plus a
`/etc/systemd/system.conf.d/watchdog.conf` drop-in) so systemd reboots the
board if it ever stops petting the watchdog — a hung boot self-heals instead
of sitting dark. This needs a reboot after install to take effect, since
`dtparam` is read at boot time. It doesn't fix corruption on the SD card
itself; if repeated reboots don't bring it back, pull the card and run
`fsck` on another machine, or reflash.

## Diagnosing "nothing prints"

Start by splitting the stack in half:

```bash
./scripts/testprint.sh --raw
```

This writes TSPL straight to `/dev/usb/lp0` — no CUPS, no filter, no queue. The
test label has a full border, corner boxes and a centre bar, so alignment
problems are visible at a glance.

- **A label comes out** → the printer, cable and power are fine. The problem is
  the queue or the filter; see the `cups-print-chain` skill.
- **Nothing comes out** → printer, cable or power. Check `ls -l /dev/usb/`.

Without `--raw` the same label goes through the queue with `lp -o raw`, which
tests CUPS while still bypassing the filter — useful for narrowing further.

## Tuning against real stock

Three things can only be settled with the printer in hand, and all three are
configuration rather than code:

| Symptom | Fix |
|---|---|
| Labels creep or come out short | `GAP 3 mm` assumes gap-separated stock. Set Media Tracking to Continuous for continuous rolls. |
| Print sits off-centre | Horizontal/Vertical Offset options on the queue; they feed TSPL `REFERENCE`. |
| Too faint or too scorched | Darkness (0-15), per job from the web app or as a queue default. |

Resist changing constants in `tspl.py` for these — the PPD options exist so the
same code serves different stock.

## The service

`labelserver.service` runs gunicorn (2 workers, 4 threads) on port 80 as an
unprivileged user, using `AmbientCapabilities=CAP_NET_BIND_SERVICE` to bind the
low port without running as root. Hardened with `ProtectSystem=strict`,
`NoNewPrivileges` and a `MemoryMax` of 512M; `ReadWritePaths=/run/cups` is what
lets it reach the CUPS socket. If you add anything that writes to disk, it needs
a `ReadWritePaths` entry or it will fail with a confusing permission error.

```bash
systemctl status labelserver
journalctl -u labelserver -f
curl -s localhost/healthz
```

## Verifying a fresh install

1. `lpstat -p labels` — queue exists and is idle
2. `./scripts/testprint.sh --raw` — hardware works
3. `./scripts/testprint.sh` — CUPS works
4. Upload a real carrier PDF through the web page and check the preview
5. Print from a Mac, then an iPhone — proves sharing and Avahi
6. **Reboot and repeat 1-5.** Most of this is boot-time wiring, and a reboot is
   the only honest test of it.
