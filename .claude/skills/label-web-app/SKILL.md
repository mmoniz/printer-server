---
name: label-web-app
description: The Flask upload/preview/print app and how it talks to CUPS. Use this whenever you touch labelserver/app.py, labelserver/printing.py, the templates or stylesheet, add a route or a print option, or work on job listing, cancelling, previews, or error messages. Also read this before writing tests that involve printing — CUPS is stubbed, and there is a specific way to do it. For the URL-paste and email intake paths specifically, see the url-link-intake and mail-intake skills instead.
---

# The web app

`labelserver/app.py` is the page the family actually uses: upload a label,
look at a preview, press Print. `labelserver/printing.py` is the CUPS side.

Built with `create_app(queue)` rather than a module-level app so tests can spin
up isolated instances. The module-level `app` reads `LABELSERVER_QUEUE` from the
environment for gunicorn and the dev server.

## The preview is not decoration

Upload does **not** print. It normalizes, stores the result, and redirects to a
review page showing exactly what will come out.

This exists because label detection is a heuristic over documents we do not
control. During development the first end-to-end run cropped a Letter page to
nearly the whole sheet and would have printed the label at a third size — the
preview caught it. Keep a human in the loop before anything reaches the printer,
and keep the mode switches (Automatic / Always crop / Whole page) easy to reach
so a wrong guess is a five-second fix rather than a wasted label.

## Request flow

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

`/upload`'s `url` field and the `/admin/*` routes are covered in the
`url-link-intake` and `mail-intake` skills respectively; this skill is the
core file-upload path and the routes/state everything else builds on.

## PendingStore

Normalized labels live in memory between preview and print, keyed by a
`secrets.token_urlsafe(16)` token, with a 30-minute TTL and a 32-item cap
(oldest evicted).

In memory on purpose: the Pi boots from an SD card, and cards die from write
churn. A label nobody printed within half an hour is not worth persisting. A
token is consumed on successful print so a refresh cannot silently reprint.

Contrast this with `MailStore` (`mail-intake` skill): that history is
deliberately the opposite (persisted, kept until someone deletes it), because
the whole point of the admin panel is to look at it later, possibly after a
reboot. Don't default new state to "persist it" without asking which of these
two this actually is.

This is also why `labelserver.service` runs gunicorn with exactly one
worker. Each worker is a separate OS process with its own `create_app()`
call and therefore its own empty `PendingStore` -- a second worker doesn't
share it, doesn't get told about it, nothing. An upload landing on worker A
and the browser's `GET /preview/<token>.png` landing on worker B (there's no
affinity between requests on different connections) is a 404 with no error
in the logs, which reads as "the preview is just broken" rather than what it
actually is. If this app ever needs more concurrency than `--threads`
provides within one process, the store needs to move to something shared
(disk, sqlite, whatever) first -- turning up `--workers` alone silently
reintroduces this bug.

## Talking to CUPS

`printing.py` shells out to `lp`, `lpstat` and `cancel` rather than using a CUPS
binding. Nothing to compile on a Pi 2, and the behaviour matches what you would
get typing the same commands over SSH — which makes debugging on the box
identical to debugging in code.

`_explain()` translates CUPS's terser errors into something a family member can
act on ("the 'labels' print queue does not exist on this machine — run
scripts/install.sh"). When you add an error path, add a translation; the person
reading it is not going to run `journalctl`.

`cancel()` validates the job id against `[A-Za-z0-9_.-]+` before it reaches a
subprocess. Keep that if you add more CUPS calls.

## Input handling

Everything from a form is treated as hostile-ish, not because the LAN is
dangerous but because a 500 is a bad experience:

- unknown fit mode falls back to `Mode.AUTO` rather than raising
- copies clamp to 1..`MAX_COPIES` (20); nonsense falls back to 1
- darkness clamps to 0..15
- extensions are allow-listed (`normalize.ALLOWED_SUFFIXES`, shared with the
  URL and mail intake paths); a 25 MB cap returns a flash message, not a crash
- `NormalizeError` becomes a flash message on the upload page

## Testing

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

The `client` fixture creates the app with `mail_db=":memory:"` so tests never
touch disk for the mail history that `create_app()` always sets up regardless
of whether a route under test cares about it — see `mail-intake` if you're
adding a fixture that does.

## Front end

Plain Jinja templates and one hand-written stylesheet, no build step and no
dependencies — a Pi 2 is serving this to phones. `style.css` supports light and
dark via `prefers-color-scheme`. Most uploads come from a phone, so check
anything you add at a 375px viewport.

Run it locally against a real queue:

```bash
LABELSERVER_QUEUE=my_queue .venv/bin/python -m flask --app labelserver.app run --port 8080
```
