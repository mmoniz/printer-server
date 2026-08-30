"""
Tests for labelserver/mailstore.py.

Uses ":memory:" throughout -- fast, and there is nothing here that depends
on the filesystem specifically. Production always uses a real file (see
label-web-app skill: this history has to survive a restart).
"""

from __future__ import annotations

import pytest

from labelserver.mailstore import MailStore


@pytest.fixture
def store():
    return MailStore(":memory:")


def attachment(filename="label.pdf", pdf=b"pdf-bytes", preview=b"png-bytes",
              summary="cropped to 4x6in", label_shaped=True):
    return {"filename": filename, "pdf": pdf, "preview": preview,
           "summary": summary, "label_shaped": label_shaped}


def test_empty_store_has_no_messages(store):
    assert store.list_messages() == []
    assert store.total_bytes() == 0


def test_add_and_list_a_message_with_attachments(store):
    store.add_message("amazon@example.com", "Your label", "",
                      [attachment(filename="a.pdf"), attachment(filename="b.png")])

    messages = store.list_messages()
    assert len(messages) == 1
    msg = messages[0]
    assert msg.sender == "amazon@example.com"
    assert msg.subject == "Your label"
    assert msg.note == ""
    assert [a.filename for a in msg.attachments] == ["a.pdf", "b.png"]


def test_messages_without_attachments_are_still_recorded(store):
    store.add_message("someone@example.com", "just a link",
                      "No PDF or image attachment found in this email.", [])

    messages = store.list_messages()
    assert len(messages) == 1
    assert messages[0].attachments == []
    assert "No PDF or image" in messages[0].note


def test_newest_message_listed_first(store):
    first = store.add_message("a@example.com", "first", "", [attachment()])
    # received_at is time.time() at insert; force a distinguishable order
    # without depending on real clock resolution between two fast calls.
    store._conn.execute("UPDATE messages SET received_at = 1 WHERE id = ?", (first,))
    second = store.add_message("b@example.com", "second", "", [attachment()])
    store._conn.execute("UPDATE messages SET received_at = 2 WHERE id = ?", (second,))
    store._conn.commit()

    messages = store.list_messages()
    assert [m.subject for m in messages] == ["second", "first"]


def test_get_attachment_returns_full_bytes(store):
    store.add_message("a@example.com", "s", "", [attachment(pdf=b"the-pdf",
                                                             preview=b"the-preview")])
    attachment_id = store.list_messages()[0].attachments[0].id

    record = store.get_attachment(attachment_id)
    assert record.pdf == b"the-pdf"
    assert record.preview == b"the-preview"


def test_get_attachment_returns_none_for_unknown_id(store):
    assert store.get_attachment(999) is None


def test_delete_message_removes_its_attachments_too(store):
    mid = store.add_message("a@example.com", "s", "", [attachment()])
    attachment_id = store.list_messages()[0].attachments[0].id

    store.delete_message(mid)

    assert store.list_messages() == []
    assert store.get_attachment(attachment_id) is None


def test_delete_all_clears_everything(store):
    store.add_message("a@example.com", "one", "", [attachment()])
    store.add_message("b@example.com", "two", "", [attachment()])

    store.delete_all()

    assert store.list_messages() == []
    assert store.total_bytes() == 0


def test_total_bytes_sums_pdf_and_preview_sizes(store):
    store.add_message("a@example.com", "s", "",
                      [attachment(pdf=b"1234567890", preview=b"12345")])
    assert store.total_bytes() == 15


def test_watermark_defaults_to_zero_then_persists(store):
    assert store.get_watermark("last_uid") == 0
    store.set_watermark("last_uid", 42)
    assert store.get_watermark("last_uid") == 42
    store.set_watermark("last_uid", 43)
    assert store.get_watermark("last_uid") == 43
