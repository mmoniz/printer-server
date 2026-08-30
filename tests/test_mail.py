"""
Tests for labelserver/mail.py.

parse_message() is pure and tested directly against synthetic emails.
fetch_new() talks to imaplib, so it's tested against a fake IMAP4_SSL the
same way test_app.py fakes the lp/lpstat command line -- monkeypatched on
the module, not on the imported name, so the app under test still calls it.
"""

from __future__ import annotations

from email.message import EmailMessage

import pytest

from labelserver import mail as mail_module
from labelserver.mail import MailConfig, MailError, fetch_new, parse_message

CONFIG = MailConfig(host="imap.example.com", username="labels@example.com",
                    password="app-password")


# --- parse_message ---------------------------------------------------------

def test_extracts_a_pdf_attachment():
    msg = EmailMessage()
    msg["From"] = "Amazon <ship-confirm@amazon.com>"
    msg["Subject"] = "Your return label"
    msg.set_content("Here's your label.")
    msg.add_attachment(b"%PDF-1.4 fake", maintype="application",
                       subtype="pdf", filename="ShipperLabel.pdf")

    parsed = parse_message(bytes(msg))

    assert parsed.sender == "Amazon <ship-confirm@amazon.com>"
    assert parsed.subject == "Your return label"
    assert len(parsed.attachments) == 1
    assert parsed.attachments[0].filename == "ShipperLabel.pdf"
    assert parsed.attachments[0].data == b"%PDF-1.4 fake"


def test_extracts_an_image_attachment():
    msg = EmailMessage()
    msg["From"] = "friend@example.com"
    msg["Subject"] = "label"
    msg.set_content("see attached")
    msg.add_attachment(b"\x89PNG fake", maintype="image", subtype="png",
                       filename="label.png")

    parsed = parse_message(bytes(msg))
    assert parsed.attachments[0].filename == "label.png"


def test_ignores_non_printable_attachments():
    msg = EmailMessage()
    msg["From"] = "someone@example.com"
    msg["Subject"] = "not a label"
    msg.set_content("body")
    msg.add_attachment(b"zip bytes", maintype="application",
                       subtype="zip", filename="archive.zip")

    parsed = parse_message(bytes(msg))
    assert parsed.attachments == []


def test_no_attachments_at_all():
    msg = EmailMessage()
    msg["From"] = "someone@example.com"
    msg["Subject"] = "just a link"
    msg.set_content("http://example.com/label")

    parsed = parse_message(bytes(msg))
    assert parsed.attachments == []


def test_missing_headers_fall_back_to_placeholders():
    msg = EmailMessage()
    msg.set_content("no headers at all")

    parsed = parse_message(bytes(msg))
    assert parsed.sender == "unknown sender"
    assert parsed.subject == "(no subject)"


def test_multiple_attachments_are_all_extracted():
    msg = EmailMessage()
    msg["From"] = "someone@example.com"
    msg["Subject"] = "two labels"
    msg.set_content("body")
    msg.add_attachment(b"one", maintype="application", subtype="pdf",
                       filename="a.pdf")
    msg.add_attachment(b"two", maintype="image", subtype="jpeg",
                       filename="b.jpg")

    parsed = parse_message(bytes(msg))
    assert {a.filename for a in parsed.attachments} == {"a.pdf", "b.jpg"}


# --- body text (feeds the relevance filter in mailpoll.py) ---------------

def test_plain_text_body_is_captured():
    msg = EmailMessage()
    msg["From"] = "someone@example.com"
    msg["Subject"] = "fyi"
    msg.set_content("Please print this label for me, thanks!")

    parsed = parse_message(bytes(msg))
    assert "print this label" in parsed.body_text


def test_html_only_body_is_stripped_and_captured():
    msg = EmailMessage()
    msg["From"] = "someone@example.com"
    msg["Subject"] = "fyi"
    msg.add_alternative(
        "<html><body><p>Here is your <b>label</b>.</p></body></html>",
        subtype="html")

    parsed = parse_message(bytes(msg))
    assert "label" in parsed.body_text
    assert "<b>" not in parsed.body_text


def test_plain_text_preferred_over_html_when_both_present():
    msg = EmailMessage()
    msg["From"] = "someone@example.com"
    msg["Subject"] = "fyi"
    msg.set_content("plain version")
    msg.add_alternative("<html><body>html version</body></html>", subtype="html")

    parsed = parse_message(bytes(msg))
    assert "plain version" in parsed.body_text


def test_no_body_at_all_gives_empty_string():
    msg = EmailMessage()
    msg["From"] = "someone@example.com"
    msg["Subject"] = "fyi"
    msg.add_attachment(b"data", maintype="application", subtype="pdf",
                       filename="a.pdf")

    parsed = parse_message(bytes(msg))
    assert parsed.body_text == ""


# --- fetch_new ---------------------------------------------------------

class FakeIMAP:
    """Stand-in for imaplib.IMAP4_SSL."""

    login_should_fail = False
    select_should_fail = False
    messages: dict[int, bytes] = {}

    def __init__(self, host, port):
        self.host = host
        self.port = port

    def login(self, user, password):
        if self.login_should_fail:
            import imaplib
            raise imaplib.IMAP4.error("bad credentials")

    def select(self, folder, readonly=False):
        return ("OK", [b"1"]) if not self.select_should_fail else ("NO", [b""])

    def uid(self, command, arg1, arg2=None):
        if command == "search":
            uids = sorted(self.messages)
            return ("OK", [" ".join(str(u) for u in uids).encode()])
        if command == "fetch":
            uid = int(arg1)
            raw = self.messages.get(uid)
            if raw is None:
                return ("OK", [None])
            return ("OK", [(b"1 (RFC822 {n})", raw)])
        raise AssertionError(f"unexpected uid command: {command}")

    def logout(self):
        pass


@pytest.fixture
def fake_imap(monkeypatch):
    FakeIMAP.login_should_fail = False
    FakeIMAP.select_should_fail = False
    FakeIMAP.messages = {}
    monkeypatch.setattr(mail_module.imaplib, "IMAP4_SSL", FakeIMAP)
    return FakeIMAP


def test_fetch_new_returns_messages_after_the_watermark(fake_imap):
    fake_imap.messages = {1: b"raw one", 2: b"raw two", 3: b"raw three"}
    result = fetch_new(CONFIG, since_uid=1)
    assert result == [(2, b"raw two"), (3, b"raw three")]


def test_fetch_new_returns_nothing_when_up_to_date(fake_imap):
    fake_imap.messages = {1: b"raw one"}
    assert fetch_new(CONFIG, since_uid=1) == []


def test_fetch_new_reports_a_login_failure(fake_imap):
    fake_imap.login_should_fail = True
    with pytest.raises(MailError, match="Could not log in"):
        fetch_new(CONFIG, since_uid=0)


def test_fetch_new_reports_an_unreachable_host(monkeypatch):
    def boom(host, port):
        raise OSError("no route to host")
    monkeypatch.setattr(mail_module.imaplib, "IMAP4_SSL", boom)

    with pytest.raises(MailError, match="Could not connect"):
        fetch_new(CONFIG, since_uid=0)
