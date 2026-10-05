---
name: url-link-intake
description: Uploading a label by pasting a link, or dragging/pasting an image from another browser tab. Use this whenever you touch labelserver/urlfetch.py, the url field on the upload form, drag/paste handling in index.html, or investigate "the link didn't work", SSRF concerns, or a login-walled carrier page (Amazon returns, etc.). Read this before adding any other outbound request anywhere in the app.
---
<!-- component-paths: labelserver/urlfetch.py tests/test_urlfetch.py -->

# Uploading by link

## Overview

`POST /upload` (see the `label-web-app` skill for the rest of that route)
accepts a `url` field as an alternative to `label`: paste a link, or
drag/paste an image from another browser tab and the front end resolves it
to one or the other client-side (a real file if the browser handed one over,
otherwise the link text) before submitting the same form. A file takes
priority if both are somehow present.

This component owns `labelserver/urlfetch.py`, the one place the app makes an
outbound request, and the drag/paste handling in `index.html`. It leaves the
rest of the upload route to `label-web-app` and email intake to `mail-intake`.

## How it works

### Drag/paste resolution, client-side

In `index.html`, dropping or pasting can hand over either a real `File` (most
browsers convert an `<img>` drag, or a clipboard "copy image," into one) or
just text (a dragged `<a>`, a copied link, "copy image address," or any
browser — Safari included — that doesn't export image bytes on drag). The
front end checks `dataTransfer.files`/`clipboardData.items` for a file first
and falls back to the link text, populating whichever of `label`/`url` applies
and clearing the other. Paste is scoped to the upload form (or `document.body`
when nothing else has focus) so pasting elsewhere on the page is inert, and a
paste that lands inside the URL field itself is left to the browser's normal
paste behavior rather than intercepted.

### `urlfetch.fetch_url()` is the one place this app calls out unprompted

Everything else in the web app only reacts to a request; fetching a pasted
URL is different, so it is hardened accordingly, not just parsed:

- only `http`/`https`; the hostname is resolved and rejected if it's
  private, loopback, link-local, multicast or reserved — this blocks the
  app being used to probe the Pi itself, the router, or another LAN device
- redirects are **not** auto-followed (a custom `HTTPRedirectHandler` that
  returns `None` from `redirect_request` forces urllib to raise instead of
  chasing it internally); each hop is re-resolved and re-checked before
  being followed, so a link that starts public can't 302 its way to
  something private
- the response is capped at `MAX_UPLOAD_BYTES` while streaming, and its
  `Content-Type` has to match one of `normalize.ALLOWED_SUFFIXES` — a
  generic or wrong type is rejected before it reaches `normalize_upload`

### Testing

Tested against a real local `http.server` in `tests/test_urlfetch.py` (redirect
chains and the private-address check depend on actual urllib behavior, not
just the app's own logic), with the private-address check disabled via
monkeypatch for the tests that aren't about it — that check would otherwise
reject the test server itself, since it necessarily lives on loopback. The
one test that *is* about the check (`test_a_redirect_target_is_revalidated_independently`)
swaps in a fake check instead, to prove each hop's host is looked up on its
own rather than only the URL the fetch started with.

`tests/test_app.py`'s `urlfetch` fixture (`FakeUrlfetch`) monkeypatches
`app_module.urlfetch.fetch_url` for route-level tests — same "patch on the
module" rule as the CUPS fakes in `label-web-app`.

## Gotchas

### Any other outbound call needs the same checks

If you add another outbound call anywhere in this app, run it through the
same checks rather than assuming the LAN-only deployment makes SSRF moot —
a family member's phone can still paste a link to anything.

### A link that requires being logged in cannot work here, on purpose

Amazon return/shipping label pages are the case that comes up: fetched
anonymously they redirect to a sign-in page or a generic error page, not the
label, because the server has no session and never will (automating a login
is out of scope, not just unimplemented). `fetch_url` can only tell "this
needs a login" apart from "this link is wrong" by one signal --
`Content-Type: text/html` (`HTML_CONTENT_TYPE`) where a file was expected -- so
that specific case gets a different message pointing at what actually works:
drag or paste the *rendered image* instead of the link. That path goes through
the family member's own browser and its already-authenticated fetch, not ours,
which is why it succeeds where the link can't. The hint under the URL field says
this up front so it doesn't have to be learned by hitting the error first. When a
login-walled page can't even be dragged as an image (a canvas-rendered
label, say), the remaining fallback is the `mail-intake` skill: point the
site's own "email this to someone" feature at the mailbox instead.

### The private-address check rejects the test server

Symptom: a new `urlfetch` test fails with a "private address" error against
`127.0.0.1`. Cause: the check is working. Disable it via monkeypatch for the
test, as the existing ones do, unless the test is about the check.

## Decisions

Newest first. Don't delete entries. When a decision is replaced, mark it superseded.

### 2026-10-05 — Name the HTML content type

- **Context:** The magic-value audit (ENG-14) flagged the `"text/html"` comparison that detects a login wall.
- **Decision:** `HTML_CONTENT_TYPE`, defined next to the other module constants.
- **Why:** The login-wall detection hangs on that one signal, so it should be findable by name.
- **Alternatives considered:** Leaving the literal, which the linter rejects.
- **Status:** active

### 2026-08-29 — Harden the fetch rather than just parse the URL

- **Context:** Pasting a link makes the server request something on a family member's behalf, unlike every other route (commit 7e7ed6a).
- **Decision:** `fetch_url` allows only http/https, resolves and rejects private, loopback, link-local, multicast and reserved addresses, does not auto-follow redirects (each hop is re-resolved and re-checked), and caps the streamed size and the content type.
- **Why:** A link that starts public must not be able to 302 its way to the Pi, the router or another LAN device, and the LAN-only deployment doesn't make SSRF moot.
- **Alternatives considered:** > TODO(history): not recorded.
- **Status:** active

### 2026-08-29 — Never automate a login; route around it

- **Context:** Amazon return labels are behind a login, and the server has no session (commit f6aeae2 added the guidance).
- **Decision:** Detect the likely login wall by `Content-Type: text/html` and point at what works: drag or paste the rendered image, which uses the family member's own authenticated browser. The email path (`mail-intake`) is the last fallback.
- **Why:** Automating a login is out of scope, not just unimplemented.
- **Alternatives considered:** Storing credentials or a session cookie on the Pi.
- **Status:** active

## Trade-offs

A pasted link only works for public, direct file URLs. We give up convenience
for login-walled labels in exchange for never holding anyone's credentials, and
the drag/paste and email paths cover most of what that leaves. We'd reconsider
the redirect handling only with a concrete need for a redirect through a
different host that still resolves publicly, since each hop is already allowed
if it passes the same checks.

## Related

- `label-web-app`: the `/upload` route this feeds, and the module-patching test rule.
- `mail-intake`: the fallback when a page can't even be dragged as an image.
- `label-normalization`: `ALLOWED_SUFFIXES` is shared with this path.
