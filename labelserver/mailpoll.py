"""
Background loop that checks the mailbox on a timer and stores whatever's
new. Started from create_app() only when mail is actually configured (a
host, username and password all present), so tests and installs that don't
use this feature never touch the network.

poll_once() is the part actually worth testing and is kept free of the
threading loop around it, the same way urlfetch.fetch_url() is kept free of
the Flask route that calls it.
"""

from __future__ import annotations

import logging
import threading

from . import mail, normalize, printing
from .mail import MailConfig, MailError, ParsedMessage
from .mailstore import MailStore
from .normalize import Mode, NormalizeError
from .printing import PrintError

logger = logging.getLogger(__name__)

WATERMARK_KEY = "last_uid"

# A dedicated mailbox still gets the odd security alert or newsletter --
# nobody creates an inbox that receives *only* the mail they want. A
# printable attachment is relevant on its own regardless of wording; short
# of that, the subject or body has to actually mention what this mailbox is
# for. That catches "email a link" cases too without a separate check: a
# carrier's own print/label link almost always contains one of these words
# in its surrounding text or its own path.
RELEVANT_KEYWORDS = ("label", "print")


def _looks_relevant(parsed: ParsedMessage) -> bool:
    if parsed.attachments:
        return True
    haystack = f"{parsed.subject}\n{parsed.body_text}".lower()
    return any(keyword in haystack for keyword in RELEVANT_KEYWORDS)


def poll_once(
    config: MailConfig,
    store: MailStore,
    *,
    auto_print: bool = False,
    queue: str = printing.DEFAULT_QUEUE,
) -> int:
    """Fetch whatever is new, normalize it, and store it. Returns how many
    messages were stored -- an irrelevant message (see _looks_relevant)
    still advances the watermark so it isn't re-evaluated every poll, but
    isn't added to the history a family member actually looks at.

    When `auto_print` is set, an attachment normalizes straight to the
    printer instead of waiting in /admin for someone to press Print --
    but only when `result.label_shaped` is true, the same signal
    normalize.py already trusts elsewhere (see `trust_rotation` in
    normalize.py) to mean "this crop looks like an actual 4x6 label, not a
    guess." A full page that merely *contains* a label, or anything the
    crop heuristic wasn't confident about, still lands in the queue for a
    human to check -- auto-printing a wrong guess wastes stock silently,
    which is worse than making someone tap Print.
    """
    since = store.get_watermark(WATERMARK_KEY)
    messages = mail.fetch_new(config, since)

    highest = since
    stored = 0
    for uid, raw in messages:
        highest = max(highest, uid)
        parsed = mail.parse_message(raw)

        if not _looks_relevant(parsed):
            continue

        attachments = []
        problems = []
        printed = []
        for att in parsed.attachments:
            try:
                pdf, result = normalize.normalize_upload(
                    att.data, att.filename, mode=Mode.AUTO
                )
                preview = normalize.render_preview(
                    pdf, width_px=normalize.REVIEW_PREVIEW_WIDTH_PX
                )
            except NormalizeError as exc:
                problems.append(f"{att.filename}: {exc}")
                continue
            attachments.append(
                {
                    "filename": att.filename,
                    "pdf": pdf,
                    "preview": preview,
                    "summary": result.describe(),
                    "label_shaped": result.label_shaped,
                }
            )

            if auto_print and result.label_shaped:
                try:
                    job_id = printing.submit(pdf, queue=queue, title=att.filename)
                    printed.append(f"{att.filename} (job {job_id})")
                except PrintError as exc:
                    problems.append(f"{att.filename}: could not auto-print: {exc}")

        if not parsed.attachments:
            note = "No PDF or image attachment found in this email."
        elif not attachments:
            note = "Could not read the attachment(s): " + "; ".join(problems)
        elif problems:
            note = "Some attachments could not be read: " + "; ".join(problems)
        else:
            note = ""

        if printed:
            printed_note = "Auto-printed " + ", ".join(printed) + "."
            note = f"{printed_note} {note}" if note else printed_note

        store.add_message(parsed.sender, parsed.subject, note, attachments)
        stored += 1

    if highest != since:
        store.set_watermark(WATERMARK_KEY, highest)
    return stored


def poll_forever(
    config: MailConfig,
    store: MailStore,
    interval: float,
    stop: threading.Event,
    *,
    auto_print: bool = False,
    queue: str = printing.DEFAULT_QUEUE,
) -> None:
    """Runs until `stop` is set. Errors are logged, not fatal -- a mailbox
    that's unreachable this minute may well be fine next minute, and a
    background thread that silently dies is worse than one that keeps
    trying."""
    while not stop.is_set():
        try:
            poll_once(config, store, auto_print=auto_print, queue=queue)
        except MailError as exc:
            logger.warning("mail poll failed: %s", exc)
        except Exception:
            logger.exception("unexpected error while polling mail")
        stop.wait(interval)
