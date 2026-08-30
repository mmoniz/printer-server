"""
Persistent history of what has arrived by email, browsable from /admin.

Deliberately the opposite of PendingStore (app.py): that one is in-memory
because nobody chose to keep a label past printing it, but mail arrives
whether or not anyone's looking, and the whole point of the admin panel is
to look at it later -- including after a reboot. SQLite keeps everything in
one file rather than scattering many small ones across the SD card, and a
single long-lived connection behind a lock keeps concurrent access simple
under gunicorn's --threads (this app runs a single worker on purpose -- see
the label-web-app skill for why that matters here too).
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from threading import Lock

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sender TEXT NOT NULL,
    subject TEXT NOT NULL,
    received_at REAL NOT NULL,
    note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS attachments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    pdf BLOB NOT NULL,
    preview BLOB NOT NULL,
    summary TEXT NOT NULL,
    label_shaped INTEGER NOT NULL,
    auto_printed INTEGER NOT NULL DEFAULT 0,
    print_job_id TEXT,
    print_error TEXT
);
CREATE TABLE IF NOT EXISTS state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# Columns added after the initial release. CREATE TABLE IF NOT EXISTS leaves
# an existing mail.db on a real Pi untouched, so anything added later needs
# an explicit, idempotent ALTER TABLE here too -- see the ProtectSystem trap
# in the mail-intake skill for why "looked right in a fresh venv" isn't
# enough evidence for this database.
_ATTACHMENT_MIGRATIONS = [
    ("auto_printed", "INTEGER NOT NULL DEFAULT 0"),
    ("print_job_id", "TEXT"),
    ("print_error", "TEXT"),
]


@dataclass
class AttachmentSummary:
    id: int
    filename: str
    summary: str
    label_shaped: bool
    auto_printed: bool
    print_job_id: str | None
    print_error: str | None


@dataclass
class AttachmentRecord(AttachmentSummary):
    pdf: bytes
    preview: bytes


@dataclass
class MailSummary:
    id: int
    sender: str
    subject: str
    received_at: float
    note: str
    attachments: list[AttachmentSummary]


class MailStore:
    def __init__(self, path: str):
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(SCHEMA)
        self._migrate()
        self._conn.commit()
        self._lock = Lock()

    def _migrate(self) -> None:
        existing = {row[1] for row in
                   self._conn.execute("PRAGMA table_info(attachments)")}
        for column, definition in _ATTACHMENT_MIGRATIONS:
            if column not in existing:
                self._conn.execute(
                    f"ALTER TABLE attachments ADD COLUMN {column} {definition}")

    def get_watermark(self, key: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM state WHERE key = ?", (key,)).fetchone()
            return int(row[0]) if row else 0

    def set_watermark(self, key: str, uid: int) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO state (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, str(uid)))
            self._conn.commit()

    def add_message(self, sender: str, subject: str, note: str,
                    attachments: list[dict]) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO messages (sender, subject, received_at, note) "
                "VALUES (?, ?, ?, ?)",
                (sender, subject, time.time(), note))
            message_id = cur.lastrowid
            for att in attachments:
                self._conn.execute(
                    "INSERT INTO attachments (message_id, filename, pdf, preview, "
                    "summary, label_shaped, auto_printed, print_job_id, print_error) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (message_id, att["filename"], att["pdf"], att["preview"],
                     att["summary"], int(att["label_shaped"]),
                     int(att.get("auto_printed", False)),
                     att.get("print_job_id"), att.get("print_error")))
            self._conn.commit()
            return message_id

    def list_messages(self) -> list[MailSummary]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, sender, subject, received_at, note FROM messages "
                "ORDER BY received_at DESC").fetchall()
            result = []
            for mid, sender, subject, received_at, note in rows:
                att_rows = self._conn.execute(
                    "SELECT id, filename, summary, label_shaped, auto_printed, "
                    "print_job_id, print_error FROM attachments "
                    "WHERE message_id = ? ORDER BY id", (mid,)).fetchall()
                attachments = [
                    AttachmentSummary(id=a_id, filename=fn, summary=summ,
                                      label_shaped=bool(shaped),
                                      auto_printed=bool(printed),
                                      print_job_id=job_id, print_error=err)
                    for a_id, fn, summ, shaped, printed, job_id, err in att_rows
                ]
                result.append(MailSummary(id=mid, sender=sender, subject=subject,
                                          received_at=received_at, note=note,
                                          attachments=attachments))
            return result

    def get_attachment(self, attachment_id: int) -> AttachmentRecord | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, filename, pdf, preview, summary, label_shaped, "
                "auto_printed, print_job_id, print_error "
                "FROM attachments WHERE id = ?", (attachment_id,)).fetchone()
            if row is None:
                return None
            a_id, fn, pdf, preview, summary, shaped, printed, job_id, err = row
            return AttachmentRecord(id=a_id, filename=fn, pdf=pdf, preview=preview,
                                    summary=summary, label_shaped=bool(shaped),
                                    auto_printed=bool(printed),
                                    print_job_id=job_id, print_error=err)

    def delete_message(self, message_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM messages WHERE id = ?", (message_id,))
            self._conn.commit()

    def delete_all(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM messages")
            self._conn.commit()

    def total_bytes(self) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(SUM(LENGTH(pdf) + LENGTH(preview)), 0) "
                "FROM attachments").fetchone()
            return row[0]
