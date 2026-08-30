"""
Tests for labelserver/mailpoll.py.

poll_once() is what's worth testing here; poll_forever() is just a sleep
loop around it (exercised only enough below to prove it does stop). mail.py
itself is mocked at the module boundary -- these tests are about what
mailpoll does with what mail.py hands it, not about IMAP.
"""

from __future__ import annotations

import threading
import time
from email.message import EmailMessage

import pytest
from conftest import make_pdf

from labelserver import mailpoll
from labelserver.mail import Attachment, MailConfig, MailError, ParsedMessage, parse_message
from labelserver.mailstore import MailStore
from labelserver.printing import PrintError

CONFIG = MailConfig(host="imap.example.com", username="labels@example.com",
                    password="app-password")


@pytest.fixture
def store():
    return MailStore(":memory:")


class FakeMail:
    def __init__(self):
        self.messages: list[tuple[int, bytes]] = []
        self.parsed: dict[bytes, ParsedMessage] = {}
        self.fail_with: str | None = None

    def fetch_new(self, config, since_uid):
        if self.fail_with:
            raise MailError(self.fail_with)
        return [(uid, raw) for uid, raw in self.messages if uid > since_uid]

    def parse_message(self, raw):
        return self.parsed[raw]


@pytest.fixture
def fake_mail(monkeypatch):
    fake = FakeMail()
    monkeypatch.setattr(mailpoll, "mail", fake)
    return fake


class FakePrinting:
    """Stand-in for the lp command line -- auto-print must never shell out
    for real during a test."""

    def __init__(self):
        self.submitted = []
        self.fail_with: str | None = None

    def submit(self, pdf, queue="labels", title="label", copies=1,
               darkness=None, media="4x6.Fullbleed"):
        if self.fail_with:
            raise PrintError(self.fail_with)
        self.submitted.append({"pdf": pdf, "queue": queue, "title": title})
        return f"{queue}-{len(self.submitted)}"


@pytest.fixture(autouse=True)
def fake_printing(monkeypatch):
    """Autouse: every test in this file goes through poll_once, and a
    confident-enough attachment now tries to print for real unless this is
    patched -- see mailpoll._confidence."""
    fake = FakePrinting()
    monkeypatch.setattr(mailpoll.printing, "submit", fake.submit)
    return fake


# A block shaped nothing like a 4x6 label (square, not 2:3), but large and
# solid enough to normalize cleanly -- for exercising the "confident text,
# unconfident crop" half of the confidence score on its own.
def _square_attachment_pdf():
    return make_pdf(300, 300, [(20, 20, 260, 260)])


def test_no_new_mail_does_nothing(store, fake_mail):
    assert mailpoll.poll_once(CONFIG, store) == 0
    assert store.list_messages() == []
    assert store.get_watermark(mailpoll.WATERMARK_KEY) == 0


def test_a_printable_attachment_is_normalized_and_stored(store, fake_mail, label_4x6):
    raw = b"raw-1"
    fake_mail.messages = [(1, raw)]
    fake_mail.parsed = {raw: ParsedMessage(
        sender="amazon@example.com", subject="Your label",
        attachments=[Attachment(filename="label.pdf", data=label_4x6)])}

    count = mailpoll.poll_once(CONFIG, store)

    assert count == 1
    messages = store.list_messages()
    assert len(messages) == 1
    assert messages[0].sender == "amazon@example.com"
    assert messages[0].note == ""
    assert len(messages[0].attachments) == 1
    assert store.get_watermark(mailpoll.WATERMARK_KEY) == 1
    # label_4x6 is confidently label-shaped, which is enough on its own to
    # auto-print (see the confidence-score tests below).
    assert messages[0].attachments[0].auto_printed is True


def test_watermark_only_advances_to_the_highest_uid_seen(store, fake_mail, label_4x6):
    fake_mail.messages = [(5, b"a"), (7, b"b")]
    fake_mail.parsed = {
        b"a": ParsedMessage(sender="x", subject="x", attachments=[]),
        b"b": ParsedMessage(sender="x", subject="x", attachments=[]),
    }

    mailpoll.poll_once(CONFIG, store)
    assert store.get_watermark(mailpoll.WATERMARK_KEY) == 7


def test_email_with_no_attachment_is_recorded_with_a_note(store, fake_mail):
    raw = b"raw"
    fake_mail.messages = [(1, raw)]
    fake_mail.parsed = {raw: ParsedMessage(sender="x", subject="Print my label please",
                                           attachments=[])}

    mailpoll.poll_once(CONFIG, store)

    messages = store.list_messages()
    assert messages[0].attachments == []
    assert "No PDF or image attachment" in messages[0].note


# --- relevance filter -----------------------------------------------------

def test_irrelevant_email_is_not_stored(store, fake_mail):
    """A dedicated mailbox still gets the odd security alert -- nothing
    about label printing, no attachment. Don't clutter the admin panel
    with it, but still advance past it so it isn't re-checked forever."""
    raw = b"raw"
    fake_mail.messages = [(5, raw)]
    fake_mail.parsed = {raw: ParsedMessage(
        sender="no-reply@example.com", subject="New sign-in to your account",
        body_text="We noticed a new sign-in from a Mac.", attachments=[])}

    count = mailpoll.poll_once(CONFIG, store)

    assert count == 0
    assert store.list_messages() == []
    assert store.get_watermark(mailpoll.WATERMARK_KEY) == 5


def test_keyword_in_subject_alone_makes_an_attachmentless_email_relevant(store, fake_mail):
    raw = b"raw"
    fake_mail.messages = [(1, raw)]
    fake_mail.parsed = {raw: ParsedMessage(sender="x", subject="please print this",
                                           body_text="no link here", attachments=[])}

    count = mailpoll.poll_once(CONFIG, store)

    assert count == 1
    assert len(store.list_messages()) == 1


def test_keyword_in_body_alone_makes_an_attachmentless_email_relevant(store, fake_mail):
    raw = b"raw"
    fake_mail.messages = [(1, raw)]
    fake_mail.parsed = {raw: ParsedMessage(
        sender="x", subject="fyi",
        body_text="here's the label: https://example.com/x", attachments=[])}

    count = mailpoll.poll_once(CONFIG, store)

    assert count == 1


def test_an_attachment_is_relevant_regardless_of_wording(store, fake_mail, label_4x6):
    raw = b"raw"
    fake_mail.messages = [(1, raw)]
    fake_mail.parsed = {raw: ParsedMessage(
        sender="x", subject="hey", body_text="see attached",
        attachments=[Attachment(filename="a.pdf", data=label_4x6)])}

    count = mailpoll.poll_once(CONFIG, store)

    assert count == 1


def test_a_real_account_notification_email_is_not_relevant():
    """Regression for a genuine Gmail "Welcome to Google on your Mac OS"
    notification: no attachment, and nothing in its actual content mentions
    a label or printing. But a browser extension had injected a <style>
    with "@media print" and a <script> calling "window.print()" into the
    HTML -- literal text that used to leak through mail.py's tag-stripping
    and make this look relevant. Goes through the real parse_message(), not
    FakeMail, since the bug lived in HTML extraction, not in this filter."""
    msg = EmailMessage()
    msg["From"] = "Google <no-reply@google.com>"
    msg["Subject"] = "Welcome to Google on your Mac OS"
    msg.add_alternative(
        "<html><head>"
        "<style>@media print { .toolbar { display: none; } }</style>"
        "</head><body>"
        "<p>Get started with Google on your new device.</p>"
        "<script>document.body.onload = function() { window.print(); };</script>"
        "</body></html>",
        subtype="html")

    parsed = parse_message(bytes(msg))

    assert not mailpoll._looks_relevant(parsed)


# --- confidence score / auto-print -----------------------------------------

def test_a_confident_crop_alone_is_enough_to_auto_print(store, fake_mail, fake_printing, label_4x6):
    """label_4x6 crops confidently to a 4x6 shape; the subject says nothing
    special. A moderate bar lets that one strong visual signal carry it."""
    raw = b"raw"
    fake_mail.messages = [(1, raw)]
    fake_mail.parsed = {raw: ParsedMessage(
        sender="x", subject="here's your file",
        attachments=[Attachment(filename="label.pdf", data=label_4x6)])}

    mailpoll.poll_once(CONFIG, store, queue="mylabels")

    att = store.list_messages()[0].attachments[0]
    assert att.auto_printed is True
    assert att.print_job_id == "mylabels-1"
    assert att.print_error is None
    assert len(fake_printing.submitted) == 1
    assert fake_printing.submitted[0]["queue"] == "mylabels"
    assert fake_printing.submitted[0]["title"] == "label.pdf"


def test_a_strong_subject_alone_is_enough_to_auto_print(store, fake_mail, fake_printing):
    """The crop here isn't label-shaped (a square block), but the subject
    unambiguously names a shipping label -- that alone should carry it."""
    raw = b"raw"
    fake_mail.messages = [(1, raw)]
    fake_mail.parsed = {raw: ParsedMessage(
        sender="x", subject="Your UPS Shipping Label is attached",
        attachments=[Attachment(filename="a.pdf", data=_square_attachment_pdf())])}

    mailpoll.poll_once(CONFIG, store)

    att = store.list_messages()[0].attachments[0]
    assert att.label_shaped is False
    assert att.auto_printed is True
    assert len(fake_printing.submitted) == 1


def test_neither_signal_present_is_not_auto_printed(store, fake_mail, fake_printing):
    """A weak crop and a generic subject: falls back to manual review in
    the admin panel, same as before this feature existed."""
    raw = b"raw"
    fake_mail.messages = [(1, raw)]
    fake_mail.parsed = {raw: ParsedMessage(
        sender="x", subject="please print this",
        attachments=[Attachment(filename="a.pdf", data=_square_attachment_pdf())])}

    mailpoll.poll_once(CONFIG, store)

    att = store.list_messages()[0].attachments[0]
    assert att.auto_printed is False
    assert att.print_job_id is None
    assert att.print_error is None
    assert fake_printing.submitted == []


def test_a_confident_match_that_fails_to_print_is_recorded_not_dropped(
        store, fake_mail, fake_printing, label_4x6):
    """Confidence just means "try to print automatically," not "trust it
    blindly." A CUPS failure must still leave the attachment visible for a
    human to print manually -- the whole point of keeping the mail history
    at all -- with the reason it failed, per the "leave a trail" rule for
    anything that runs unattended."""
    fake_printing.fail_with = "the 'labels' queue is rejecting jobs"
    raw = b"raw"
    fake_mail.messages = [(1, raw)]
    fake_mail.parsed = {raw: ParsedMessage(
        sender="x", subject="Your label",
        attachments=[Attachment(filename="label.pdf", data=label_4x6)])}

    mailpoll.poll_once(CONFIG, store)

    messages = store.list_messages()
    att = messages[0].attachments[0]
    assert att.auto_printed is False
    assert att.print_job_id is None
    assert "rejecting jobs" in att.print_error
    # Not an attachment-read problem, so the message-level note is untouched.
    assert messages[0].note == ""


def test_unreadable_attachment_is_recorded_with_a_note_not_dropped(store, fake_mail):
    raw = b"raw"
    fake_mail.messages = [(1, raw)]
    fake_mail.parsed = {raw: ParsedMessage(
        sender="x", subject="bad file",
        attachments=[Attachment(filename="broken.pdf", data=b"not a real pdf")])}

    mailpoll.poll_once(CONFIG, store)

    messages = store.list_messages()
    assert messages[0].attachments == []
    assert "Could not read the attachment" in messages[0].note
    assert "broken.pdf" in messages[0].note


def test_a_mixed_batch_notes_only_the_failures(store, fake_mail, label_4x6):
    raw = b"raw"
    fake_mail.messages = [(1, raw)]
    fake_mail.parsed = {raw: ParsedMessage(
        sender="x", subject="two attachments", attachments=[
            Attachment(filename="good.pdf", data=label_4x6),
            Attachment(filename="bad.pdf", data=b"not a real pdf"),
        ])}

    mailpoll.poll_once(CONFIG, store)

    messages = store.list_messages()
    assert len(messages[0].attachments) == 1
    assert messages[0].attachments[0].filename == "good.pdf"
    assert "Some attachments could not be read" in messages[0].note
    assert "bad.pdf" in messages[0].note


def test_mail_error_propagates_so_the_caller_can_log_and_retry(store, fake_mail):
    fake_mail.fail_with = "could not connect"
    with pytest.raises(MailError):
        mailpoll.poll_once(CONFIG, store)


def test_poll_forever_stops_when_the_event_is_set(store, fake_mail):
    stop = threading.Event()
    thread = threading.Thread(
        target=mailpoll.poll_forever, args=(CONFIG, store, 0.01, stop))
    thread.start()
    time.sleep(0.05)
    stop.set()
    thread.join(timeout=2)
    assert not thread.is_alive()


def test_poll_forever_survives_a_mail_error(store, fake_mail):
    """A poll failure must not kill the loop -- the whole point of retrying
    on the next interval is that the mailbox might be reachable again."""
    fake_mail.fail_with = "temporarily unreachable"
    stop = threading.Event()
    thread = threading.Thread(
        target=mailpoll.poll_forever, args=(CONFIG, store, 0.01, stop))
    thread.start()
    time.sleep(0.05)
    stop.set()
    thread.join(timeout=2)
    assert not thread.is_alive()
