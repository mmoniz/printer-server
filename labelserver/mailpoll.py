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


# Phrases a carrier's own subject line uses when it really is a label, as
# opposed to the loose "label"/"print" substring match above that decides
# whether to keep the email at all. Checked against the subject only, not
# body_text -- body_text's HTML fallback is a crude strip (see mail.py, and
# the real Gmail notification that leaked "print" through an injected
# <script> once), so it isn't trusted for anything stronger than "mention it
# somewhere."
STRONG_LABEL_PHRASES = ("shipping label", "return label", "prepaid label")

# Each signal below is independent evidence this attachment really is a
# label someone wants printed, not just something that happened to
# normalize cleanly. A moderate bar: either one on its own is enough to
# auto-print (a confident crop with an unrelated subject, or an unmistakable
# subject with an unusually shaped crop), rather than requiring both to
# agree -- that would miss the common case where only one signal is strong.
AUTO_PRINT_CONFIDENCE = 1


def _confidence(subject: str, result: normalize.Result) -> int:
    score = 0
    if result.label_shaped:
        score += 1  # the crop detector itself is confident this is a 4x6 label
    if any(phrase in subject.lower() for phrase in STRONG_LABEL_PHRASES):
        score += 1  # the sender's own subject line says so, unambiguously
    return score


def poll_once(config: MailConfig, store: MailStore,
             queue: str = printing.DEFAULT_QUEUE) -> int:
    """Fetch whatever is new, normalize it, and store it. Returns how many
    messages were stored -- an irrelevant message (see _looks_relevant)
    still advances the watermark so it isn't re-evaluated every poll, but
    isn't added to the history a family member actually looks at.

    An attachment confident enough to be "clearly a label" (see
    _confidence) is sent straight to the printer -- still recorded here
    either way, auto-printed or not, so the admin panel remains the audit
    trail for anything that happened unattended."""
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
        for att in parsed.attachments:
            try:
                pdf, result = normalize.normalize_upload(
                    att.data, att.filename, mode=Mode.AUTO)
                preview = normalize.render_preview(pdf, width_px=420)
            except NormalizeError as exc:
                problems.append(f"{att.filename}: {exc}")
                continue

            entry = {
                "filename": att.filename, "pdf": pdf, "preview": preview,
                "summary": result.describe(), "label_shaped": result.label_shaped,
                "auto_printed": False, "print_job_id": None, "print_error": None,
            }
            if _confidence(parsed.subject, result) >= AUTO_PRINT_CONFIDENCE:
                try:
                    entry["print_job_id"] = printing.submit(
                        pdf, queue=queue, title=att.filename)
                    entry["auto_printed"] = True
                except PrintError as exc:
                    entry["print_error"] = str(exc)
            attachments.append(entry)

        if not parsed.attachments:
            note = "No PDF or image attachment found in this email."
        elif not attachments:
            note = "Could not read the attachment(s): " + "; ".join(problems)
        elif problems:
            note = "Some attachments could not be read: " + "; ".join(problems)
        else:
            note = ""

        store.add_message(parsed.sender, parsed.subject, note, attachments)
        stored += 1

    if highest != since:
        store.set_watermark(WATERMARK_KEY, highest)
    return stored


def poll_forever(config: MailConfig, store: MailStore, interval: float,
                 stop: threading.Event,
                 queue: str = printing.DEFAULT_QUEUE) -> None:
    """Runs until `stop` is set. Errors are logged, not fatal -- a mailbox
    that's unreachable this minute may well be fine next minute, and a
    background thread that silently dies is worse than one that keeps
    trying."""
    while not stop.is_set():
        try:
            poll_once(config, store, queue)
        except MailError as exc:
            logger.warning("mail poll failed: %s", exc)
        except Exception:
            logger.exception("unexpected error while polling mail")
        stop.wait(interval)
