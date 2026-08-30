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

import pytest

from labelserver import mailpoll
from labelserver.mail import Attachment, MailConfig, MailError, ParsedMessage
from labelserver.mailstore import MailStore

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
