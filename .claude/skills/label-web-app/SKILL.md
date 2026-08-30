---
name: label-web-app
description: The Flask upload/preview/print app and how it talks to CUPS. Use this whenever you touch labelserver/app.py, labelserver/printing.py, labelserver/urlfetch.py, labelserver/mail.py, labelserver/mailpoll.py, labelserver/mailstore.py, the templates or stylesheet, add a route or a print option, or work on job listing, cancelling, previews, pasting/dragging in a link or image, receiving labels by email, the /admin mail history page, or error messages. Also read this before writing tests that involve printing — CUPS is stubbed, and there is a specific way to do it.
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
GET  /admin                  mail history: sender, subject, note, attachment previews
GET  /admin/preview/<id>.png a stored attachment's preview image
POST /admin/use/<id>         load a mail attachment into PendingStore, redirect to /review
POST /admin/delete/<id>      delete one email and its attachments
POST /admin/delete-all       clear all mail history
GET  /healthz                200 if the queue is ready, 503 otherwise
```

## PendingStore

Normalized labels live in memory between preview and print, keyed by a
`secrets.token_urlsafe(16)` token, with a 30-minute TTL and a 32-item cap
(oldest evicted).

In memory on purpose: the Pi boots from an SD card, and cards die from write
churn. A label nobody printed within half an hour is not worth persisting. A
token is consumed on successful print so a refresh cannot silently reprint.

Contrast this with `MailStore` below: that history is deliberately the
opposite (persisted, kept until someone deletes it), because the whole point
of the admin panel is to look at it later, possibly after a reboot. Don't
default new state to "persist it" without asking which of these two
this actually is.

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

**A link that requires being logged in cannot work here, on purpose.**
Amazon return/shipping label pages are the case that comes up: fetched
anonymously they redirect to a sign-in page or a generic error page, not the
label, because the server has no session and never will (automating a login
is out of scope, not just unimplemented). `fetch_url` can only tell "this
needs a login" apart from "this link is wrong" by one signal --
`Content-Type: text/html` where a file was expected -- so that specific case
gets a different message pointing at what actually works: drag or paste the
*rendered image* instead of the link. That path goes through the family
member's own browser and its already-authenticated fetch, not ours, which is
why it succeeds where the link can't. The hint under the URL field says this
up front so it doesn't have to be learned by hitting the error first.

Tested against a real local `http.server` in `tests/test_urlfetch.py` (redirect
chains and the private-address check depend on actual urllib behavior, not
just the app's own logic), with the private-address check disabled via
monkeypatch for the tests that aren't about it — that check would otherwise
reject the test server itself, since it necessarily lives on loopback.

## Uploading by email

The other fallback for a login-walled link: point the "email this to a
friend" feature at a dedicated mailbox, and the Pi picks it up from there.
This is the one part of the app that talks to the internet unprompted
(everything else only reacts to a request), so it was worth stopping to
choose the shape carefully rather than defaulting to whatever's easiest --
see the git history around when this was added for the actual options
weighed. The answer: poll an existing mailbox over IMAP rather than run our
own mail server. Running one would mean a domain, an MX record, port
forwarding, and this device's first ever inbound-facing service, on hardware
that has otherwise never listened for anything beyond the LAN. Polling is
outbound-only, the same shape as `urlfetch.fetch_url` -- nothing new to
defend.

Three modules, kept separate on purpose:

- **`mail.py`** is the boundary that actually speaks IMAP (`fetch_new`) and
  turns a raw email into sender/subject/attachments (`parse_message`).
  `fetch_new` is mocked in tests by monkeypatching `mail_module.imaplib.IMAP4_SSL`
  with a fake connection class, the same "patch on the module" rule as `printing`.
- **`mailpoll.py`** is the loop: `poll_once()` does one fetch-normalize-store
  pass and is what's actually tested; `poll_forever()` is a thin `while`
  around it with a `threading.Event` for a clean stop, not worth testing
  beyond "does it actually stop." A poll failure is logged and retried next
  interval, never fatal -- an unreachable mailbox now doesn't mean it's
  unreachable forever.
- **`mailstore.py`** is `MailStore`, a SQLite-backed history (see the
  PendingStore section above for why this one persists and that one
  doesn't). One long-lived connection behind a `threading.Lock`, not a
  connection per call -- simple, and correct under gunicorn's `--threads`
  the same way PendingStore is. Cascading delete needs
  `PRAGMA foreign_keys = ON` set at connection open; SQLite doesn't default
  to enforcing it.

Each email attachment goes through the exact same `normalize.normalize_upload`
+ `render_preview` pipeline as a file upload, always with `Mode.AUTO` since
there's no one there to pick a fit mode at the moment it arrives -- a bad
guess gets caught on the admin page instead of at upload time. An email with
no attachment, or one that fails to normalize, is still recorded (with a
`note` explaining why) rather than silently dropped; "the email arrived but
nothing came of it" is exactly the kind of thing this history exists to show.

`create_app()` only starts the polling thread when
`LABELSERVER_MAIL_HOST`/`_USER`/`_PASSWORD` are all set. Tests never set
them, so the thread never starts and the test suite makes no network calls
-- `MailStore` itself is still created (pointed at `:memory:` in tests via
`create_app(mail_db=":memory:")`) so `/admin` always has something to render,
configured or not. In production, `LABELSERVER_MAIL_DB` points at
`/opt/labelserver/data/mail.db`, which needs to exist and be owned by the
service user before the app can write to it (`install.sh` creates it).

That path was originally covered by an explicit `ReadWritePaths` entry under
`ProtectSystem=strict`, which looked right on paper -- correct ownership,
correct permissions, the loaded unit reporting the right value via
`systemctl show` -- and still left `sqlite3.OperationalError: unable to open
database file` on a real Pi, even though a plain `sudo -u labelserver touch`
in that same directory worked fine outside the sandbox. Chasing the exact
systemd mechanism further wasn't worth it: `labelserver.service` now uses
`ProtectSystem=full` instead, which leaves `/opt` and `/run` alone entirely
(only `/usr`, `/boot` and `/etc` become read-only) and needs no
`ReadWritePaths` for either the mail database or the CUPS socket. If you're
tempted to tighten this back to `strict` for defense in depth, be ready to
actually verify a write to `/opt/labelserver/data` survives a real restart
on real hardware, not just that the config looks right.

Credentials live in `/etc/labelserver/mail.env` (`scripts/mail.env.example`
is the template), mode 600, loaded via `EnvironmentFile=-...` in
`labelserver.service` -- the leading `-` means the unit still starts with
mail polling off if the file isn't there yet.

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

For `/admin` routes, the `mail_store` fixture hands back the app's actual
`MailStore` (via `app.config["MAIL_STORE"]`, exposed for exactly this) so a
test can call `add_message()` directly to seed history rather than going
through a fake mailbox — `tests/test_mail.py` and `tests/test_mailpoll.py`
are where the mail-fetching and polling logic itself get tested in
isolation.

## Front end

Plain Jinja templates and one hand-written stylesheet, no build step and no
dependencies — a Pi 2 is serving this to phones. `style.css` supports light and
dark via `prefers-color-scheme`. Most uploads come from a phone, so check
anything you add at a 375px viewport.

Run it locally against a real queue:

```bash
LABELSERVER_QUEUE=my_queue .venv/bin/python -m flask --app labelserver.app run --port 8080
```
