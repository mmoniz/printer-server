---
name: url-link-intake
description: Uploading a label by pasting a link, or dragging/pasting an image from another browser tab. Use this whenever you touch labelserver/urlfetch.py, the url field on the upload form, drag/paste handling in index.html, or investigate "the link didn't work", SSRF concerns, or a login-walled carrier page (Amazon returns, etc.). Read this before adding any other outbound request anywhere in the app.
---

# Uploading by link

`POST /upload` (see the `label-web-app` skill for the rest of that route)
accepts a `url` field as an alternative to `label`: paste a link, or
drag/paste an image from another browser tab and the front end resolves it
to one or the other client-side (a real file if the browser handed one over,
otherwise the link text) before submitting the same form. A file takes
priority if both are somehow present.

## Drag/paste resolution, client-side

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

## `urlfetch.fetch_url()` is the one place this app calls out unprompted

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

If you add another outbound call anywhere in this app, run it through the
same checks rather than assuming the LAN-only deployment makes SSRF moot —
a family member's phone can still paste a link to anything.

## A link that requires being logged in cannot work here, on purpose

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
up front so it doesn't have to be learned by hitting the error first. When a
login-walled page can't even be dragged as an image (a canvas-rendered
label, say), the remaining fallback is the `mail-intake` skill: point the
site's own "email this to someone" feature at the mailbox instead.

## Testing

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
