"""
The web page family members actually use.

Flow: upload a PDF or photo -> we normalize it to 4x6 and show a preview ->
they press Print. The preview matters; it is the only chance to notice that a
label came out sideways before wasting stock.

Normalized labels are held in memory (not on the SD card) between preview and
print, keyed by a random token, and expire on their own.

Labels can also arrive by email -- for links that need you signed in to view
(Amazon returns, for one), that's the fallback. A background thread polls a
mailbox over IMAP (mail.py, mailpoll.py) and stores what it finds in
mailstore.py, browsable from /admin. Unlike PendingStore, that history is
meant to be looked at later, so it is persisted rather than kept in memory.
"""

from __future__ import annotations

import io
import os
import secrets
import threading
import time
from dataclasses import dataclass, field
from threading import Lock

from flask import (Flask, abort, flash, redirect, render_template, request,
                   send_file, url_for)

from . import mail, mailpoll, normalize, printing, urlfetch
from .mailstore import MailStore
from .normalize import ALLOWED_SUFFIXES, Mode, NormalizeError
from .printing import PrintError
from .urlfetch import FetchError

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
PENDING_TTL_SECONDS = 30 * 60
MAX_PENDING = 32
MAX_COPIES = 20


@dataclass
class Pending:
    """A normalized label waiting for someone to press Print."""

    pdf: bytes
    preview: bytes
    filename: str
    summary: str
    label_shaped: bool = True
    created: float = field(default_factory=time.time)


class PendingStore:
    """Small TTL cache. Deliberately in memory: labels are not worth persisting."""

    def __init__(self, ttl: float = PENDING_TTL_SECONDS, limit: int = MAX_PENDING):
        self._items: dict[str, Pending] = {}
        self._lock = Lock()
        self._ttl = ttl
        self._limit = limit

    def _expire(self) -> None:
        cutoff = time.time() - self._ttl
        for token in [t for t, p in self._items.items() if p.created < cutoff]:
            del self._items[token]
        while len(self._items) > self._limit:
            oldest = min(self._items, key=lambda t: self._items[t].created)
            del self._items[oldest]

    def add(self, pending: Pending) -> str:
        token = secrets.token_urlsafe(16)
        with self._lock:
            self._items[token] = pending
            self._expire()
        return token

    def get(self, token: str) -> Pending | None:
        with self._lock:
            self._expire()
            return self._items.get(token)

    def pop(self, token: str) -> Pending | None:
        with self._lock:
            return self._items.pop(token, None)


def create_app(queue: str = printing.DEFAULT_QUEUE,
              mail_db: str = "mail.db") -> Flask:
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES
    # Only used to sign flash messages on a trusted home LAN.
    app.secret_key = os.environ.get("LABELSERVER_SECRET", secrets.token_hex(16))
    app.config["QUEUE"] = queue

    store = PendingStore()
    mail_store = MailStore(mail_db)
    app.config["MAIL_STORE"] = mail_store

    mail_config = None
    host = os.environ.get("LABELSERVER_MAIL_HOST")
    user = os.environ.get("LABELSERVER_MAIL_USER")
    password = os.environ.get("LABELSERVER_MAIL_PASSWORD")
    if host and user and password:
        mail_config = mail.MailConfig(
            host=host, username=user, password=password,
            port=int(os.environ.get("LABELSERVER_MAIL_PORT", "993")),
            folder=os.environ.get("LABELSERVER_MAIL_FOLDER", "INBOX"))
        stop_event = threading.Event()
        interval = float(os.environ.get("LABELSERVER_MAIL_POLL_SECONDS", "300"))
        thread = threading.Thread(
            target=mailpoll.poll_forever,
            args=(mail_config, mail_store, interval, stop_event),
            daemon=True)
        thread.start()

    @app.template_filter("kb")
    def kb(value: int) -> str:
        return f"{value / 1024:.0f} KB"

    @app.template_filter("dt")
    def dt(value: float) -> str:
        return time.strftime("%b %d, %Y %H:%M", time.localtime(value))

    def queue_banner():
        try:
            ready, status = printing.queue_state(app.config["QUEUE"])
        except PrintError as exc:
            return False, str(exc)
        return ready, status

    @app.get("/")
    def index():
        ready, status = queue_banner()
        try:
            current = printing.jobs(app.config["QUEUE"])
        except PrintError:
            current = []
        return render_template("index.html", ready=ready, status=status,
                               jobs=current, queue=app.config["QUEUE"])

    @app.post("/upload")
    def upload():
        upload_file = request.files.get("label")
        url = request.form.get("url", "").strip()

        if upload_file is not None and upload_file.filename:
            filename = upload_file.filename
            data = upload_file.read()
        elif url:
            try:
                data, filename = urlfetch.fetch_url(url, MAX_UPLOAD_BYTES)
            except FetchError as exc:
                flash(str(exc), "error")
                return redirect(url_for("index"))
        else:
            flash("Choose a file, or paste a link to one, first.", "error")
            return redirect(url_for("index"))

        suffix = os.path.splitext(filename)[1].lower()
        if suffix not in ALLOWED_SUFFIXES:
            flash(f"{suffix or 'That file type'} is not supported. "
                  "Upload a PDF or an image.", "error")
            return redirect(url_for("index"))

        try:
            mode = Mode(request.form.get("mode", Mode.AUTO.value))
        except ValueError:
            mode = Mode.AUTO

        try:
            pdf, result = normalize.normalize_upload(data, filename, mode=mode)
            preview = normalize.render_preview(pdf, width_px=420)
        except NormalizeError as exc:
            flash(str(exc), "error")
            return redirect(url_for("index"))

        token = store.add(Pending(pdf=pdf, preview=preview,
                                  filename=filename,
                                  summary=result.describe(),
                                  label_shaped=result.label_shaped))

        return redirect(url_for("review", token=token))

    @app.get("/review/<token>")
    def review(token):
        pending = store.get(token)
        if pending is None:
            flash("That preview expired. Upload the label again.", "error")
            return redirect(url_for("index"))

        ready, status = queue_banner()
        return render_template("review.html", token=token, pending=pending,
                               ready=ready, status=status,
                               max_copies=MAX_COPIES)

    @app.get("/preview/<token>.png")
    def preview(token):
        pending = store.get(token)
        if pending is None:
            abort(404)
        return send_file(io.BytesIO(pending.preview), mimetype="image/png")

    @app.post("/print/<token>")
    def do_print(token):
        pending = store.get(token)
        if pending is None:
            flash("That preview expired. Upload the label again.", "error")
            return redirect(url_for("index"))

        try:
            copies = int(request.form.get("copies", "1"))
        except ValueError:
            copies = 1
        copies = max(1, min(MAX_COPIES, copies))

        darkness = request.form.get("darkness")
        darkness_value = None
        if darkness:
            try:
                darkness_value = max(0, min(15, int(darkness)))
            except ValueError:
                darkness_value = None

        try:
            job_id = printing.submit(pending.pdf, queue=app.config["QUEUE"],
                                     title=pending.filename, copies=copies,
                                     darkness=darkness_value)
        except PrintError as exc:
            flash(f"Could not print: {exc}", "error")
            return redirect(url_for("review", token=token))

        store.pop(token)
        flash(f"Sent to the printer ({copies} "
              f"{'copy' if copies == 1 else 'copies'}, job {job_id}).", "success")
        return redirect(url_for("index"))

    @app.post("/cancel/<job_id>")
    def cancel(job_id):
        try:
            printing.cancel(job_id, queue=app.config["QUEUE"])
            flash(f"Cancelled {job_id}.", "success")
        except PrintError as exc:
            flash(f"Could not cancel: {exc}", "error")
        return redirect(url_for("index"))

    @app.get("/admin")
    def admin():
        ready, status = queue_banner()
        return render_template("admin.html", messages=mail_store.list_messages(),
                               total_bytes=mail_store.total_bytes(),
                               mail_configured=mail_config is not None,
                               ready=ready, status=status)

    @app.get("/admin/preview/<int:attachment_id>.png")
    def admin_preview(attachment_id):
        record = mail_store.get_attachment(attachment_id)
        if record is None:
            abort(404)
        return send_file(io.BytesIO(record.preview), mimetype="image/png")

    @app.post("/admin/use/<int:attachment_id>")
    def admin_use(attachment_id):
        record = mail_store.get_attachment(attachment_id)
        if record is None:
            flash("That email attachment is gone.", "error")
            return redirect(url_for("admin"))

        token = store.add(Pending(pdf=record.pdf, preview=record.preview,
                                  filename=record.filename,
                                  summary=record.summary,
                                  label_shaped=record.label_shaped))
        return redirect(url_for("review", token=token))

    @app.post("/admin/delete/<int:mail_id>")
    def admin_delete(mail_id):
        mail_store.delete_message(mail_id)
        flash("Deleted.", "success")
        return redirect(url_for("admin"))

    @app.post("/admin/delete-all")
    def admin_delete_all():
        mail_store.delete_all()
        flash("Mail history cleared.", "success")
        return redirect(url_for("admin"))

    @app.get("/healthz")
    def healthz():
        ready, status = queue_banner()
        return {"ready": ready, "status": status,
                "queue": app.config["QUEUE"]}, (200 if ready else 503)

    @app.errorhandler(413)
    def too_large(_):
        flash(f"That file is too big (limit "
              f"{MAX_UPLOAD_BYTES // (1024 * 1024)} MB).", "error")
        return redirect(url_for("index")), 302

    return app


app = create_app(os.environ.get("LABELSERVER_QUEUE", printing.DEFAULT_QUEUE),
                 mail_db=os.environ.get("LABELSERVER_MAIL_DB", "mail.db"))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=True)
