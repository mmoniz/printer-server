---
name: mail-intake
description: Receiving labels by email as the fallback for a login-walled link that can't even be dragged as an image. Use this whenever you touch labelserver/mail.py, labelserver/mailpoll.py, labelserver/mailstore.py, the admin.html template, the /admin routes, IMAP polling, the relevance/keyword filter, or investigate "the email never showed up", "unrelated mail is cluttering the admin panel", mail credentials, or the mail history/admin panel. Read this before changing how the mail database is stored or secured, or what counts as relevant.
---

# Receiving labels by email

Amazon (and most carriers) offer "email this label to someone" as their own
fallback for a page that needs you signed in to view — see the
`url-link-intake` skill for why that matters here. Point that "someone" at a
dedicated mailbox, and this polls it periodically over IMAP.

## Why polling, not our own mail server

This is the one part of the app that talks to the internet unprompted
(everything else only reacts to a request), so it was worth stopping to
choose the shape carefully rather than defaulting to whatever's easiest.
Running our own inbound SMTP server would mean a domain, an MX record, port
forwarding, and this device's first ever inbound-facing service, on
hardware that has otherwise never listened for anything beyond the LAN.
Polling is outbound-only, the same shape as `urlfetch.fetch_url` — nothing
new to defend, no domain required.

## Three modules, kept separate on purpose

- **`mail.py`** is the boundary that actually speaks IMAP (`fetch_new`) and
  turns a raw email into sender/subject/attachments (`parse_message`).
  `fetch_new` is mocked in tests by monkeypatching `mail_module.imaplib.IMAP4_SSL`
  with a fake connection class, the same "patch on the module" rule used
  throughout this project.
- **`mailpoll.py`** is the loop: `poll_once()` does one fetch-normalize-store
  pass and is what's actually tested; `poll_forever()` is a thin `while`
  around it with a `threading.Event` for a clean stop, not worth testing
  beyond "does it actually stop." A poll failure is logged and retried next
  interval, never fatal -- an unreachable mailbox now doesn't mean it's
  unreachable forever.
- **`mailstore.py`** is `MailStore`, a SQLite-backed history — deliberately
  persistent, unlike `PendingStore` (`label-web-app` skill), because the
  whole point of the admin panel is to look at this later, possibly after a
  reboot. One long-lived connection behind a `threading.Lock`, not a
  connection per call -- simple, and correct under gunicorn's `--threads`
  the same way `PendingStore` is. Cascading delete needs
  `PRAGMA foreign_keys = ON` set at connection open; SQLite doesn't default
  to enforcing it.

Each email attachment goes through the exact same `normalize.normalize_upload`
+ `render_preview` pipeline as a file upload, always with `Mode.AUTO` since
there's no one there to pick a fit mode at the moment it arrives -- a bad
guess gets caught on the admin page instead of at upload time. An email with
no attachment, or one that fails to normalize, is still recorded (with a
`note` explaining why) rather than silently dropped; "the email arrived but
nothing came of it" is exactly the kind of thing this history exists to show.

## The relevance filter

Nobody creates a mailbox that receives *only* mail they want -- even a
dedicated one gets the odd security alert or newsletter. `mailpoll._looks_relevant()`
keeps those out of the admin panel: a printable attachment is relevant on
its own regardless of wording, and short of that, the subject or body has
to actually mention `label` or `print` (`RELEVANT_KEYWORDS`,
case-insensitive substring match against `parsed.subject` + `parsed.body_text`).

This deliberately isn't "does the email contain a link" as a separate
check. Almost every commercial email contains *some* URL (an unsubscribe
link, if nothing else), so that signal alone would filter out very little.
A genuine carrier print/label link's surrounding text -- or the link's own
path, e.g. `.../ShipperLabel` -- almost always contains one of the keywords
anyway, so the existing text check already catches the "email with a link,
no attachment" case without a second, weaker heuristic to maintain.

An irrelevant message still advances the IMAP watermark (`highest = max(...)`
runs before the relevance check in `poll_once`), so it's evaluated once and
never re-checked on the next poll -- it's just never passed to
`store.add_message()`. `poll_once()`'s return value is the count actually
**stored**, not the count fetched; a test asserting on it should account for
messages the filter drops.

`body_text` (`mail.parse_message`) prefers the `text/plain` part and only
falls back to a crude `<[^>]+>` tag strip of `text/html` when no plain-text
alternative exists -- good enough for a keyword scan, not for display, and
not intended to be shown anywhere. That fallback drops `<script>`/`<style>`
*content*, not just their tags, before the generic strip -- a real Gmail
notification exposed this: a browser extension had injected a `<style>`
with `@media print` and a `<script>` calling `window.print()`, and the
literal word "print" survived a tag-only strip, making an unrelated account
email look relevant.

## Configuration and where things live

`create_app()` only starts the polling thread when
`LABELSERVER_MAIL_HOST`/`_USER`/`_PASSWORD` are all set. Tests never set
them, so the thread never starts and the test suite makes no network calls
-- `MailStore` itself is still created (pointed at `:memory:` in tests via
`create_app(mail_db=":memory:")`) so `/admin` always has something to render,
configured or not. In production, `LABELSERVER_MAIL_DB` points at
`/opt/labelserver/data/mail.db`, which needs to exist and be owned by the
service user before the app can write to it (`install.sh` creates it).

Credentials live in `/etc/labelserver/mail.env` (`scripts/mail.env.example`
is the template), mode 600, loaded via `EnvironmentFile=-...` in
`labelserver.service` -- the leading `-` means the unit still starts with
mail polling off if the file isn't there yet. Gmail (the common case) needs
an **app password**, not the account's regular password — those only exist
once 2-Step Verification is on for that account — and the value has to go
into `mail.env` with the spaces Google displays it with stripped out; a
password containing them silently becomes a different, wrong credential to
IMAP and fails with a generic `AUTHENTICATIONFAILED` that doesn't say why.

## The `ProtectSystem=strict` trap

That mail database path was originally covered by an explicit
`ReadWritePaths` entry under `ProtectSystem=strict` in `labelserver.service`,
which looked right on paper -- correct ownership, correct permissions, the
loaded unit reporting the right value via `systemctl show` -- and still
failed with `sqlite3.OperationalError: unable to open database file` on a
real Pi, even though a plain `sudo -u labelserver touch` in that same
directory worked fine outside the sandbox. Chasing the exact systemd
mechanism further wasn't worth it: the unit now uses `ProtectSystem=full`
instead, which leaves `/opt` and `/run` alone entirely (only `/usr`, `/boot`
and `/etc` become read-only) and needs no `ReadWritePaths` for either the
mail database or the CUPS socket. If you're tempted to tighten this back to
`strict` for defense in depth, be ready to actually verify a write to
`/opt/labelserver/data` survives a real restart on real hardware, not just
that the config looks right.

## Testing

`tests/test_mail.py` covers `parse_message` (pure, built with synthetic
`EmailMessage`s, including the `body_text` extraction) and `fetch_new`
(mocked `imaplib.IMAP4_SSL`). `tests/test_mailpoll.py` covers
`poll_once`/`poll_forever` against a fake `mail` module and a real
(`:memory:`) `MailStore`, including the relevance filter's cases (keyword
in subject alone, keyword in body alone, attachment regardless of wording,
and the negative case: neither, not stored). `tests/test_mailstore.py`
covers the store's CRUD directly.

For `/admin` routes in `tests/test_app.py`, the `mail_store` fixture hands
back the app's actual `MailStore` (via `app.config["MAIL_STORE"]`, exposed
for exactly this) so a test can call `add_message()` directly to seed
history rather than going through a fake mailbox.
