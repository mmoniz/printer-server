---
name: label-web-app
description: The Flask upload/preview/print app and how it talks to CUPS. Use this whenever you touch labelserver/app.py, labelserver/printing.py, the templates or stylesheet, add a route or a print option, or work on job listing, cancelling, previews, or error messages. Also read this before writing tests that involve printing — CUPS is stubbed, and there is a specific way to do it. For the URL-paste and email intake paths specifically, see the url-link-intake and mail-intake skills instead.
---
<!-- component-paths: labelserver/app.py labelserver/printing.py labelserver/templates/base.html labelserver/templates/index.html labelserver/templates/review.html labelserver/static tests/test_app.py tests/test_printing.py -->

# The web app

## Overview

`labelserver/app.py` is the page the family actually uses: upload a label,
look at a preview, press Print. `labelserver/printing.py` is the CUPS side.

Built with `create_app(queue)` rather than a module-level app so tests can spin
up isolated instances. The module-level `app` reads `LABELSERVER_QUEUE` from the
environment for gunicorn and the dev server.

This skill is the core file-upload path and the routes and state everything else
builds on. `/upload`'s `url` field and the `/admin/*` routes are covered in the
`url-link-intake` and `mail-intake` skills respectively.

## How it works

### Request flow

```
GET  /                       upload form + current queue + jobs
POST /upload                 file or url -> normalize -> render preview -> store -> redirect
GET  /review/<token>         the preview, copies, darkness, Print
GET  /preview/<tok>.png      the preview image
POST /print/<token>          submit to CUPS, consume the token
POST /cancel/<job_id>        cancel a queued job
GET  /admin                  mail history -- see the mail-intake skill
GET  /healthz                200 if the queue is ready, 503 otherwise
```

### The preview is not decoration

Upload does **not** print. It normalizes, stores the result, and redirects to a
review page showing exactly what will come out.

Keep a human in the loop before anything reaches the printer, and keep the mode
switches (Automatic / Always crop / Whole page) easy to reach so a wrong guess is
a five-second fix rather than a wasted label.

### PendingStore

Normalized labels live in memory between preview and print, keyed by a
`secrets.token_urlsafe(16)` token, with a 30-minute TTL and a 32-item cap
(oldest evicted: `PENDING_TTL_SECONDS`, `MAX_PENDING`).

A token is consumed on successful print so a refresh cannot silently reprint.

### Talking to CUPS

`printing.py` shells out to `lp`, `lpstat` and `cancel` rather than using a CUPS
binding. The fragments of CUPS's wording it matches on (`QUEUE_MISSING_MARKERS`,
`IDLE_MARKER` and the rest) are named constants at the top of the module.

`_explain()` translates CUPS's terser errors into something a family member can
act on ("the 'labels' print queue does not exist on this machine — run
scripts/install.sh"). When you add an error path, add a translation; the person
reading it is not going to run `journalctl`.

`cancel()` validates the job id against `[A-Za-z0-9_.-]+` before it reaches a
subprocess. Keep that if you add more CUPS calls.

### Input handling

Everything from a form is treated as hostile-ish, not because the LAN is
dangerous but because a 500 is a bad experience:

- unknown fit mode falls back to `Mode.AUTO` rather than raising
- copies clamp to 1..`MAX_COPIES` (20); nonsense falls back to 1
- darkness clamps to 0..15
- extensions are allow-listed (`normalize.ALLOWED_SUFFIXES`, shared with the
  URL and mail intake paths); a 25 MB cap returns a flash message, not a crash
- `NormalizeError` becomes a flash message on the upload page. Flash categories
  are the `FLASH_ERROR` and `FLASH_SUCCESS` constants, which the templates turn
  into CSS classes.

### Testing

`tests/test_app.py` monkeypatches `app_module.printing`'s four functions with
`FakeCups`, so the suite runs anywhere — CI included — with no printing stack.
Patch the functions **on the module** (`app_module.printing`), not the `printing`
import in the test, or the app will still call the real thing.

```python
def test_something(client, cups, letter_with_label):
    token = token_from(upload(client, letter_with_label))
    client.post(f"/print/{token}", data={"copies": "2"})
    assert cups.submitted[0]["copies"] == 2
```

`cups.fail_with` makes any call raise `PrintError`, and `cups.ready = False`
simulates an offline printer — both are worth asserting on when you add a route,
since the printer being unavailable is a normal state, not an edge case.

`tests/test_printing.py` pins the parsing of `lpstat` output one module down, by
stubbing `printing._run`, since `test_app.py` replaces those functions wholesale.

The `client` fixture creates the app with `mail_db=":memory:"` so tests never
touch disk for the mail history that `create_app()` always sets up regardless
of whether a route under test cares about it — see `mail-intake` if you're
adding a fixture that does.

### Front end

Plain Jinja templates and one hand-written stylesheet, no build step and no
dependencies — a Pi 2 is serving this to phones. `style.css` supports light and
dark via `prefers-color-scheme`. Most uploads come from a phone, so check
anything you add at a 375px viewport.

Run it locally against a real queue:

```bash
LABELSERVER_QUEUE=my_queue uv run python -m flask --app labelserver.app run --port 8080
```

## Gotchas

### More than one gunicorn worker makes previews 404

Each worker is a separate OS process with its own `create_app()` call and
therefore its own empty `PendingStore` -- a second worker doesn't share it,
doesn't get told about it, nothing. An upload landing on worker A and the
browser's `GET /preview/<token>.png` landing on worker B (there's no affinity
between requests on different connections) is a 404 with no error in the logs,
which reads as "the preview is just broken" rather than what it actually is.
That is why `labelserver.service` runs gunicorn with exactly one worker. If this
app ever needs more concurrency than `--threads` provides within one process,
the store needs to move to something shared (disk, sqlite, whatever) first --
turning up `--workers` alone silently reintroduces this bug.

### Don't default new state to "persist it"

`PendingStore` is in memory on purpose; `MailStore` (`mail-intake` skill) is
deliberately the opposite: persisted, kept until someone deletes it, because the
whole point of the admin panel is to look at it later, possibly after a reboot.
Ask which of the two a new piece of state actually is.

### The preview of an upload can look right while the wrong thing prints

Not a risk today, because upload never prints. Anything that adds a path
skipping the review page removes the only safeguard against a mis-detected
crop, so don't.

## Decisions

Newest first. Don't delete entries. When a decision is replaced, mark it superseded.

### 2026-10-05 — Name the Flask config key, flash categories and CUPS wording

- **Context:** The magic-value audit (ENG-14, ENG-15) flagged the `"QUEUE"` config key (7 uses), the `"error"` and `"success"` flash categories (14 uses) and the CUPS phrases matched in `printing.py`.
- **Decision:** `QUEUE_CONFIG`, `FLASH_ERROR`, `FLASH_SUCCESS` and the `printing.py` marker constants, behavior unchanged. `tests/test_printing.py` was added first as characterization tests.
- **Why:** One definition each, so a typo can't silently miss a match.
- **Alternatives considered:** Leaving the Flask idioms as literals, which the linter would flag for the CUPS phrases anyway.
- **Status:** active

### 2026-08-29 — Run gunicorn with one worker

- **Context:** Preview images 404ed under gunicorn's two worker processes, because each had its own `PendingStore` (commit 8a109c7).
- **Decision:** `labelserver.service` runs one worker with several threads.
- **Why:** The store is process-local.
- **Alternatives considered:** Moving the store to disk or sqlite, which costs SD-card writes for labels nobody prints.
- **Status:** active

### 2026-08-09 — Preview before print

- **Context:** Label detection is a heuristic over documents we do not control. The first end-to-end run cropped a Letter page to nearly the whole sheet and would have printed the label at a third size; the preview caught it.
- **Decision:** Upload never prints. It redirects to a review page with the mode switches within reach.
- **Why:** A human check is cheaper than a wasted label.
- **Alternatives considered:** Printing immediately and offering a reprint.
- **Status:** active

### 2026-08-09 — Keep pending labels in memory

- **Context:** The Pi boots from an SD card, and cards die from write churn.
- **Decision:** `PendingStore` is in memory with a 30-minute TTL and a 32-item cap.
- **Why:** A label nobody printed within half an hour is not worth persisting.
- **Alternatives considered:** > TODO(history): not recorded beyond the SD-card reasoning.
- **Status:** active

### 2026-08-09 — Shell out to `lp`, `lpstat` and `cancel`

- **Context:** Talking to CUPS from Python.
- **Decision:** `printing.py` runs the CUPS command-line tools instead of using a binding.
- **Why:** Nothing to compile on a Pi 2, and the behaviour matches typing the same commands over SSH, so debugging on the box is identical to debugging in code.
- **Alternatives considered:** A CUPS binding (pycups), which needs compiling.
- **Status:** active

## Trade-offs

Parsing CUPS's human-readable output (`lpstat`) is brittle against a wording
change in CUPS, which is why the markers are named and tested. One worker caps
concurrency at a handful of threads, which is plenty for a household. We'd
reconsider the in-memory store if the app ever needs more than one process.

## Related

- `label-normalization`: what turns an upload into the preview.
- `url-link-intake`, `mail-intake`: the other intake paths into the same store.
- `cups-print-chain`: the queue this app submits to.
- `pi-deployment`: the systemd unit that sets the worker count.
