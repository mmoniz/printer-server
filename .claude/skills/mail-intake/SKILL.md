---
name: mail-intake
description: Receiving labels by email as the fallback for a login-walled link that can't even be dragged as an image. Use this whenever you touch labelserver/mail.py, labelserver/mailpoll.py, labelserver/mailstore.py, the admin.html template, the /admin routes, IMAP polling, the relevance/keyword filter, or investigate "the email never showed up", "unrelated mail is cluttering the admin panel", mail credentials, or the mail history/admin panel. Read this before changing how the mail database is stored or secured, or what counts as relevant.
---
<!-- component-paths: labelserver/mail.py labelserver/mailpoll.py labelserver/mailstore.py labelserver/templates/admin.html scripts/mail.env.example tests/test_mail.py tests/test_mailpoll.py tests/test_mailstore.py -->

# Receiving labels by email

## Overview

Amazon (and most carriers) offer "email this label to someone" as their own
fallback for a page that needs you signed in to view — see the
`url-link-intake` skill for why that matters here. Point that "someone" at a
dedicated mailbox, and this polls it periodically over IMAP.

This component owns the IMAP boundary, the poll loop, the persistent mail
history, and the `/admin` panel that shows it. Normalizing each attachment is
`label-normalization`'s job, and the upload route is `label-web-app`'s.

## How it works

### Three modules, kept separate on purpose

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

### Auto-print

`LABELSERVER_MAIL_AUTOPRINT=1` (`mail.env`, off by default) makes
`poll_once` submit an attachment straight to CUPS instead of only leaving it
in `/admin` for someone to press Print. The gate is `result.label_shaped` --
the same boolean `normalize.py` already uses internally to decide whether a
crop is trustworthy (`trust_rotation = label_shaped`), not a separate
confidence metric. A page that's clearly a 4x6 label prints itself; a full
page, a photo, or anything the crop heuristic wasn't confident about still
waits in the admin history for a human, because a silent wrong guess wastes
stock in a way a queued item never does. The message is still stored either
way -- auto-printed or not -- with the note recording which attachments were
sent and their CUPS job id, so `/admin` stays the audit trail even when
nobody had to look at it to get the label printed. A `PrintError` during
auto-print (queue down, disabled, etc.) is folded into the same `note` field
used for unreadable attachments, not raised -- one bad print attempt
shouldn't take down the poll loop.

### The relevance filter

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

`body_text` (`mail.parse_message`) prefers the `text/plain` part (`PLAIN_TEXT`)
and only falls back to a crude `<[^>]+>` tag strip of `text/html` (`HTML_TEXT`)
when no plain-text alternative exists -- good enough for a keyword scan, not
for display, and not intended to be shown anywhere.

### Configuration and where things live

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
mail polling off if the file isn't there yet.

### Testing

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

## Gotchas

### Gmail needs an app password with the spaces removed

Symptom: IMAP fails with a generic `AUTHENTICATIONFAILED` that doesn't say why.
Cause: Gmail (the common case) needs an **app password**, not the account's
regular password — those only exist once 2-Step Verification is on for that
account — and the value has to go into `mail.env` with the spaces Google
displays it with stripped out; a password containing them silently becomes a
different, wrong credential to IMAP.

### The `ProtectSystem=strict` trap

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

### A tag-only HTML strip lets "print" through

A real Gmail notification exposed this: a browser extension had injected a
`<style>` with `@media print` and a `<script>` calling `window.print()`, and the
literal word "print" survived a tag-only strip, making an unrelated account
email look relevant. The fallback therefore drops `<script>`/`<style>`
*content*, not just their tags, before the generic strip.

### `poll_once()` counts stored messages, not fetched ones

A test asserting on its return value has to account for messages the relevance
filter drops.

## Decisions

Newest first. Don't delete entries. When a decision is replaced, mark it superseded.

### 2026-10-05 — Name the IMAP status and MIME types

- **Context:** The magic-value audit (ENG-14) flagged `"OK"` (3 uses) and the `text/plain` and `text/html` comparisons in `mail.py`, and `420` for the preview width in `mailpoll.py`.
- **Decision:** `IMAP_OK`, `PLAIN_TEXT`, `HTML_TEXT`, and `normalize.REVIEW_PREVIEW_WIDTH_PX`. A test for a mailbox folder that won't open was added first, since it wasn't covered.
- **Why:** One definition each, next to the code that owns it.
- **Alternatives considered:** Leaving the literals, which the linter rejects.
- **Status:** active

### 2026-09-14 — Auto-print only confidently label-shaped attachments

- **Context:** Waiting for a person to press Print in `/admin` defeats the point of emailing a label (commit bf570ac).
- **Decision:** `LABELSERVER_MAIL_AUTOPRINT=1` (off by default) submits an attachment to CUPS straight away when `result.label_shaped` is true. Anything else waits in the history for a human. A `PrintError` goes into the message's note, not an exception.
- **Why:** A silent wrong guess wastes stock in a way a queued item never does, and `/admin` stays the audit trail either way.
- **Alternatives considered:** A separate confidence metric, rather than reusing the boolean `normalize.py` already trusts for rotation.
- **Status:** active

### 2026-08-30 — Filter unrelated mail before it reaches the admin panel

- **Context:** Even a dedicated mailbox gets the odd security alert or newsletter (commits fe4cb17 and 1bd6c12).
- **Decision:** `_looks_relevant()` accepts a printable attachment on its own, otherwise requires `label` or `print` in the subject or body. An irrelevant message still advances the IMAP watermark but isn't stored. The HTML fallback drops `<script>`/`<style>` content.
- **Why:** "Contains a link" would filter out almost nothing, since nearly every commercial email has one. The keywords already catch a genuine carrier link's surrounding text.
- **Alternatives considered:** A separate link-based heuristic.
- **Status:** active

### 2026-08-29 — Poll the mailbox over IMAP, don't run a mail server

- **Context:** This is the one part of the app that talks to the internet unprompted (commit e98a316).
- **Decision:** Poll a dedicated mailbox outbound-only, the same shape as `urlfetch.fetch_url`.
- **Why:** Our own inbound SMTP server would mean a domain, an MX record, port forwarding, and this device's first ever inbound-facing service. Polling has nothing new to defend and needs no domain.
- **Alternatives considered:** Running an SMTP server.
- **Status:** active

### 2026-08-29 — `ProtectSystem=full`, not `strict`

- **Context:** `strict` with a `ReadWritePaths` entry looked correct in every way inspectable and still failed with "unable to open database file" on a real Pi, crash-looping the service (commit 3468a3e).
- **Decision:** The unit uses `ProtectSystem=full`.
- **Why:** It leaves `/opt` and `/run` alone, so neither the mail database nor the CUPS socket needs `ReadWritePaths`.
- **Alternatives considered:** Chasing the exact systemd mechanism, which wasn't worth it.
- **Status:** active

## Trade-offs

Polling means a labelled email isn't printed until the next poll, and a
keyword filter will occasionally drop a relevant email or keep an irrelevant
one. In return the Pi never listens for anything beyond the LAN and holds no
domain. The history is persistent, so it writes to the SD card, which is
accepted for a store people want to read later. We'd reconsider the filter if
a real label email is repeatedly dropped.

## Related

- `url-link-intake`: why a login-walled link ends up here.
- `label-web-app`: `PendingStore`, the deliberate opposite of `MailStore`.
- `pi-deployment`: `install.sh` creates the data directory and `mail.env`, and the `labelserver.service` hardening.
- `label-normalization`: `label_shaped`, which gates auto-print.
