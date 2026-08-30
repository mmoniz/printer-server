"""
Tests for labelserver/mailstore.py.

Uses ":memory:" throughout -- fast, and there is nothing here that depends
on the filesystem specifically. Production always uses a real file (see
label-web-app skill: this history has to survive a restart).
"""

from __future__ import annotations

import sqlite3

import pytest

from labelserver.mailstore import MailStore


@pytest.fixture
def store():
    return MailStore(":memory:")


def attachment(filename="label.pdf", pdf=b"pdf-bytes", preview=b"png-bytes",
              summary="cropped to 4x6in", label_shaped=True,
              auto_printed=False, print_job_id=None, print_error=None):
    return {"filename": filename, "pdf": pdf, "preview": preview,
           "summary": summary, "label_shaped": label_shaped,
           "auto_printed": auto_printed, "print_job_id": print_job_id,
           "print_error": print_error}


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


def test_auto_print_fields_round_trip_through_list_and_get(store):
    store.add_message("amazon@example.com", "Your label", "", [
        attachment(auto_printed=True, print_job_id="labels-7"),
        attachment(filename="b.pdf", print_error="the queue is rejecting jobs"),
    ])

    printed, failed = store.list_messages()[0].attachments
    assert printed.auto_printed is True
    assert printed.print_job_id == "labels-7"
    assert printed.print_error is None
    assert failed.auto_printed is False
    assert failed.print_error == "the queue is rejecting jobs"

    record = store.get_attachment(printed.id)
    assert record.auto_printed is True
    assert record.print_job_id == "labels-7"


def test_attachment_defaults_to_not_auto_printed(store):
    store.add_message("a@example.com", "s", "", [attachment()])
    att = store.list_messages()[0].attachments[0]
    assert att.auto_printed is False
    assert att.print_job_id is None
    assert att.print_error is None


def test_a_fresh_database_already_has_the_auto_print_columns(store):
    """MailStore._migrate() adds these via ALTER TABLE for a database
    created before auto-print existed; a brand new one goes through the
    same code path via CREATE TABLE, so this doubles as a smoke test that
    the migration doesn't choke on a column that's already there."""
    cols = {row[1] for row in store._conn.execute("PRAGMA table_info(attachments)")}
    assert {"auto_printed", "print_job_id", "print_error"} <= cols


def test_opening_a_pre_auto_print_database_migrates_it_in_place(tmp_path):
    """Simulates a real mail.db written before this feature existed --
    a family's actual Pi, not just a fresh :memory: store -- to prove the
    ALTER TABLE migration runs cleanly and keeps existing history intact."""
    db_path = str(tmp_path / "old_mail.db")
    old_schema = """
    CREATE TABLE messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sender TEXT NOT NULL, subject TEXT NOT NULL,
        received_at REAL NOT NULL, note TEXT NOT NULL DEFAULT ''
    );
    CREATE TABLE attachments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
        filename TEXT NOT NULL, pdf BLOB NOT NULL, preview BLOB NOT NULL,
        summary TEXT NOT NULL, label_shaped INTEGER NOT NULL
    );
    CREATE TABLE state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    """
    conn = sqlite3.connect(db_path)
    conn.executescript(old_schema)
    conn.execute("INSERT INTO messages (sender, subject, received_at, note) "
                "VALUES ('old@example.com', 'from before', 0, '')")
    conn.execute("INSERT INTO attachments (message_id, filename, pdf, preview, "
                "summary, label_shaped) VALUES (1, 'old.pdf', ?, ?, 's', 1)",
                (b"pdf", b"preview"))
    conn.commit()
    conn.close()

    store = MailStore(db_path)

    messages = store.list_messages()
    assert messages[0].subject == "from before"
    att = messages[0].attachments[0]
    assert att.filename == "old.pdf"
    assert att.auto_printed is False
    assert att.print_job_id is None

    # And the migrated store works going forward, not just for old rows.
    store.add_message("new@example.com", "new", "",
                      [attachment(auto_printed=True, print_job_id="labels-1")])
    assert store.list_messages()[0].attachments[0].auto_printed is True


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
