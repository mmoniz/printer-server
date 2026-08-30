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

from . import mail, normalize
from .mail import MailConfig, MailError
from .mailstore import MailStore
from .normalize import Mode, NormalizeError

logger = logging.getLogger(__name__)

WATERMARK_KEY = "last_uid"


def poll_once(config: MailConfig, store: MailStore) -> int:
    """Fetch whatever is new, normalize it, and store it. Returns the count."""
    since = store.get_watermark(WATERMARK_KEY)
    messages = mail.fetch_new(config, since)

    highest = since
    for uid, raw in messages:
        highest = max(highest, uid)
        parsed = mail.parse_message(raw)

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
            attachments.append({
                "filename": att.filename, "pdf": pdf, "preview": preview,
                "summary": result.describe(), "label_shaped": result.label_shaped,
            })

        if not parsed.attachments:
            note = "No PDF or image attachment found in this email."
        elif not attachments:
            note = "Could not read the attachment(s): " + "; ".join(problems)
        elif problems:
            note = "Some attachments could not be read: " + "; ".join(problems)
        else:
            note = ""

        store.add_message(parsed.sender, parsed.subject, note, attachments)

    if highest != since:
        store.set_watermark(WATERMARK_KEY, highest)
    return len(messages)


def poll_forever(config: MailConfig, store: MailStore, interval: float,
                 stop: threading.Event) -> None:
    """Runs until `stop` is set. Errors are logged, not fatal -- a mailbox
    that's unreachable this minute may well be fine next minute, and a
    background thread that silently dies is worse than one that keeps
    trying."""
    while not stop.is_set():
        try:
            poll_once(config, store)
        except MailError as exc:
            logger.warning("mail poll failed: %s", exc)
        except Exception:
            logger.exception("unexpected error while polling mail")
        stop.wait(interval)
