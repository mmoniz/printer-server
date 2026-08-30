# printer-server

A home print server for 4x6 shipping labels, running on a Raspberry Pi 2
with a USB thermal printer. Family members print by AirPrint, by IPP from a
Mac/PC, or by uploading a label — a file, a pasted link, or an email — to a
web page on the Pi. Everything stays on the LAN: no cloud, no port
forwarding, no accounts. See [README.md](README.md) for the pitch and
architecture diagram.

## Where to look

Each component has a skill under `.claude/skills/` holding the reasoning and
traps that aren't visible from the code alone — load the relevant one before
touching that area, not just when something breaks:

| Skill | Covers | Read before touching |
|---|---|---|
| `tspl-printer-protocol` | The TSPL wire format, inverted bitmap, row padding, golden fixtures | `labelserver/tspl.py`, `cups/rastertotspl` |
| `label-normalization` | Finding a label on a carrier page, crop/rotate/fit, detection constants | `labelserver/normalize.py` |
| `cups-print-chain` | PPD generation, the PPD/filter contract, AirPrint discovery | `scripts/make_ppd.py`, `cups/LabelPrinter.ppd` |
| `label-web-app` | Core Flask routes, `PendingStore`, talking to CUPS, testing against stubbed CUPS | `labelserver/app.py`, `labelserver/printing.py` |
| `url-link-intake` | Uploading by pasted link or dragged image, SSRF hardening, login-walled pages | `labelserver/urlfetch.py` |
| `mail-intake` | Uploading by email, IMAP polling, the mail history admin panel | `labelserver/mail*.py`, `admin.html` |
| `pi-deployment` | Installing, the hardware/network failure modes, tuning against real stock | `scripts/install.sh`, the systemd units |

A change usually touches exactly one of these; if it touches two, check both
skills rather than guessing which conventions carry over.

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest                          # full suite, no printer or Pi needed
.venv/bin/python -m pytest tests/test_normalize.py   # one file
shellcheck -S warning scripts/*.sh                   # required before touching scripts/
python3 scripts/make_ppd.py && git diff --exit-code cups/LabelPrinter.ppd  # PPD staleness check
```

Run the web app locally against a real or fake queue:

```bash
LABELSERVER_QUEUE=my_queue .venv/bin/python -m flask --app labelserver.app run --port 8080
```

CI (`.github/workflows/ci.yml`) runs pytest, shellcheck, and the PPD
staleness check — all three should pass locally before pushing.

## Conventions that span every component

**Stub external boundaries by monkeypatching on the module, not the
import.** CUPS (`printing.submit`/`cancel`/`queue_state`/`jobs`), IMAP
(`mail.imaplib.IMAP4_SSL`), the network (`urlfetch`'s opener) — every one of
these is faked in tests via `monkeypatch.setattr(app_module.printing, ...)`
style patching of the module attribute, never the name imported into the
test file. Patching the import leaves the app still calling the real thing.

**Model a real layout in tests, not a convenient one.** A fixture that only
passes because it's tidy proves nothing — see `tests/conftest.py`'s
`letter_with_label` (a fold line and terms text specifically placed to break
a naive bounding box) and `tests/fixtures/ups_multiband_redacted.pdf` (an
actual carrier PDF kept alongside its minimal synthetic equivalent, since a
real one exercises the messier layout a hand-built fixture wouldn't think
to). When a real file is the right fixture and it carries personal
information, redact it — this repo is **public** on GitHub, and git history
doesn't forget. Check a new fixture for metadata leakage
(`PdfReader.metadata`, `.xmp_metadata`, `.extract_text()`) before committing
it, not just the visible content.

**The Pi 2 constrains dependencies.** armv7, 32-bit, 1 GB RAM, and the venv
reuses apt's numpy/Pillow builds (`--system-site-packages`) rather than
compiling from source. Before adding a runtime dependency, confirm it ships
an armv7 wheel or an apt package — see `pi-deployment`.

**Comments use `--`, not an em dash.** Consistent across the whole codebase
and the skills themselves; match it in anything you write here.

**Leave a trail for anything that runs unattended.** The hardware watchdog,
the network watchdog, and persistent journald all exist because a background
process failing silently at 2 AM is worse than a slower failure with a log
line explaining why — see `pi-deployment`'s failure-mode list for the actual
incidents that drove each one. Apply the same instinct to new background
work: `mailpoll.py`'s poll failures are logged and retried, never silently
swallowed.

**A login-walled link genuinely cannot be fetched by the server, on
purpose.** Amazon return labels are the case that comes up. Automating a
login is out of scope regardless of how convenient it would be — the
`url-link-intake` and `mail-intake` skills cover the two real fallbacks
(drag/paste the rendered image; email the label to a dedicated mailbox
instead), which both route around the problem through a channel that
already has the necessary session, rather than trying to acquire one.
