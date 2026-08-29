---
name: label-web-app
description: The Flask upload/preview/print app and how it talks to CUPS. Use this whenever you touch labelserver/app.py, labelserver/printing.py, labelserver/urlfetch.py, the templates or stylesheet, add a route or a print option, or work on job listing, cancelling, previews, pasting/dragging in a link or image, or error messages. Also read this before writing tests that involve printing — CUPS is stubbed, and there is a specific way to do it.
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
GET  /                  upload form + current queue + jobs
POST /upload            file or url -> normalize -> render preview -> store -> redirect
GET  /review/<token>    the preview, copies, darkness, Print
GET  /preview/<tok>.png the preview image
POST /print/<token>     submit to CUPS, consume the token
POST /cancel/<job_id>   cancel a queued job
GET  /healthz           200 if the queue is ready, 503 otherwise
```

## PendingStore

Normalized labels live in memory between preview and print, keyed by a
`secrets.token_urlsafe(16)` token, with a 30-minute TTL and a 32-item cap
(oldest evicted).

In memory on purpose: the Pi boots from an SD card, and cards die from write
churn. A label nobody printed within half an hour is not worth persisting. A
token is consumed on successful print so a refresh cannot silently reprint.

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
- extensions are allow-listed; a 25 MB cap returns a flash message, not a crash
- `NormalizeError` becomes a flash message on the upload page

## Uploading by URL instead of a file

`POST /upload` accepts a `url` field as an alternative to `label`: paste a
link, or drag/paste an image from another browser tab and the front end
resolves it to one or the other client-side (a real file if the browser
handed one over, otherwise the link text) before submitting the same form.
A file takes priority if both are somehow present.

`urlfetch.fetch_url()` is the one place this app makes an outbound request
instead of only serving inbound ones, so it is hardened accordingly, not
just parsed:

- only `http`/`https`; the hostname is resolved and rejected if it's
  private, loopback, link-local, multicast or reserved — this blocks the
  app being used to probe the Pi itself, the router, or another LAN device
- redirects are **not** auto-followed (a custom `HTTPRedirectHandler` that
  returns `None` from `redirect_request` forces urllib to raise instead of
  chasing it internally); each hop is re-resolved and re-checked before
  being followed, so a link that starts public can't 302 its way to
  something private
- the response is capped at `MAX_UPLOAD_BYTES` while streaming, and its
  `Content-Type` has to match one of the allowed suffixes — a generic or
  wrong type is rejected before it reaches `normalize_upload`

If you add another outbound call anywhere in this app, run it through the
same checks rather than assuming the LAN-only deployment makes SSRF moot —
a family member's phone can still paste a link to anything.

Tested against a real local `http.server` in `tests/test_urlfetch.py` (redirect
chains and the private-address check depend on actual urllib behavior, not
just the app's own logic), with the private-address check disabled via
monkeypatch for the tests that aren't about it — that check would otherwise
reject the test server itself, since it necessarily lives on loopback.

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

## Front end

Plain Jinja templates and one hand-written stylesheet, no build step and no
dependencies — a Pi 2 is serving this to phones. `style.css` supports light and
dark via `prefers-color-scheme`. Most uploads come from a phone, so check
anything you add at a 375px viewport.

Run it locally against a real queue:

```bash
LABELSERVER_QUEUE=my_queue .venv/bin/python -m flask --app labelserver.app run --port 8080
```
