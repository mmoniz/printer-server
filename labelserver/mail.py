"""
Pulling labels out of an inbox instead of a file upload or a link.

Amazon (and most carriers) offer "email this label to someone" as their
fallback for a page that needs you signed in to view -- see urlfetch.py for
why that matters here. Rather than run our own mail server (a much bigger
commitment: a domain, MX records, port forwarding, and this device's first
ever inbound-facing service), the family points that "someone" at a
dedicated mailbox, and mailpoll.py checks it periodically over IMAP. Nothing
here ever listens for a connection; the Pi only ever calls out.

This module is the boundary that actually talks to the mail server, kept
separate and mockable the same way urlfetch.py's opener is: fetch_new()
returns raw bytes, parse_message() turns those into something printable, and
neither needs a real mailbox to test.
"""

from __future__ import annotations

import email
import imaplib
import os
import re
from dataclasses import dataclass, field
from email.message import Message
from email.policy import default as email_policy

from .normalize import ALLOWED_SUFFIXES


class MailError(Exception):
    """The mailbox could not be reached or read."""


@dataclass
class MailConfig:
    host: str
    username: str
    password: str
    port: int = 993
    folder: str = "INBOX"


@dataclass
class Attachment:
    filename: str
    data: bytes


@dataclass
class ParsedMessage:
    sender: str
    subject: str
    body_text: str = ""
    attachments: list[Attachment] = field(default_factory=list)


def fetch_new(config: MailConfig, since_uid: int) -> list[tuple[int, bytes]]:
    """Return (uid, raw_message) for every message with UID > since_uid.

    Messages are left on the server, read-only -- this app is not
    necessarily the only thing that uses the mailbox, and re-fetching an old
    UID is harmless since the caller tracks its own watermark.
    """
    try:
        conn = imaplib.IMAP4_SSL(config.host, config.port)
    except OSError as exc:
        raise MailError(f"Could not connect to {config.host}: {exc}") from exc

    try:
        try:
            conn.login(config.username, config.password)
        except imaplib.IMAP4.error as exc:
            raise MailError(f"Could not log in: {exc}") from exc

        status, _ = conn.select(config.folder, readonly=True)
        if status != "OK":
            raise MailError(f"Could not open the '{config.folder}' folder")

        status, data = conn.uid("search", None, f"UID {since_uid + 1}:*")
        if status != "OK":
            raise MailError("Could not search the mailbox")

        # An empty result still matches IMAP's highest existing UID (its way
        # of saying "nothing there"), so filter defensively rather than
        # trusting the range was honored.
        uids = sorted({int(u) for u in data[0].split()} if data and data[0] else set())
        uids = [u for u in uids if u > since_uid]

        messages = []
        for uid in uids:
            status, msg_data = conn.uid("fetch", str(uid), "(RFC822)")
            if status != "OK" or not msg_data or msg_data[0] is None:
                continue
            messages.append((uid, msg_data[0][1]))
        return messages
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def _decode_part(part: Message) -> str:
    payload = part.get_payload(decode=True)
    if not payload:
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def _extract_body_text(msg: Message) -> str:
    """Concatenate the readable body, preferring plain text over HTML.

    This is for the relevance filter's keyword scan, not display -- the HTML
    tag stripping is crude on purpose, good enough to search but not to render.
    """
    plain_parts, html_parts = [], []
    for part in msg.walk():
        if part.is_multipart() or part.get_filename():
            continue
        content_type = part.get_content_type()
        if content_type == "text/plain":
            plain_parts.append(_decode_part(part))
        elif content_type == "text/html":
            html_parts.append(_decode_part(part))

    if plain_parts:
        return "\n".join(plain_parts)
    if html_parts:
        return "\n".join(re.sub(r"<[^>]+>", " ", h) for h in html_parts)
    return ""


def parse_message(raw: bytes) -> ParsedMessage:
    """Pull sender, subject, body text and printable attachments out of a
    raw email."""
    msg: Message = email.message_from_bytes(raw, policy=email_policy)

    attachments = []
    for part in msg.walk():
        if part.is_multipart():
            continue
        filename = part.get_filename()
        if not filename:
            continue
        suffix = os.path.splitext(filename)[1].lower()
        if suffix not in ALLOWED_SUFFIXES:
            continue
        payload = part.get_payload(decode=True)
        if payload:
            attachments.append(Attachment(filename=filename, data=payload))

    return ParsedMessage(
        sender=str(msg.get("From", "unknown sender")),
        subject=str(msg.get("Subject", "(no subject)")),
        body_text=_extract_body_text(msg),
        attachments=attachments,
    )
