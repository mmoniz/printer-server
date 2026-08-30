"""
Web app tests.

CUPS is stubbed out so these run on any machine, including CI runners with no
printing stack at all.
"""

import io

import pytest

from conftest import make_pdf
from labelserver import app as app_module
from labelserver import printing
from labelserver.printing import Job, PrintError
from labelserver.urlfetch import FetchError


class FakeCups:
    """Stand-in for the lp/lpstat/cancel command line."""

    def __init__(self):
        self.submitted = []
        self.cancelled = []
        self.jobs = []
        self.ready = True
        self.status = "printer labels is idle."
        self.fail_with = None

    def submit(self, pdf, queue="labels", title="label", copies=1,
               darkness=None, media="4x6.Fullbleed"):
        if self.fail_with:
            raise PrintError(self.fail_with)
        self.submitted.append(
            {"pdf": pdf, "queue": queue, "title": title, "copies": copies,
             "darkness": darkness, "media": media}
        )
        return f"{queue}-{len(self.submitted)}"

    def cancel(self, job_id, queue="labels"):
        if self.fail_with:
            raise PrintError(self.fail_with)
        self.cancelled.append(job_id)

    def queue_state(self, queue="labels"):
        return self.ready, self.status

    def get_jobs(self, queue="labels"):
        return self.jobs


@pytest.fixture
def cups(monkeypatch):
    fake = FakeCups()
    monkeypatch.setattr(app_module.printing, "submit", fake.submit)
    monkeypatch.setattr(app_module.printing, "cancel", fake.cancel)
    monkeypatch.setattr(app_module.printing, "queue_state", fake.queue_state)
    monkeypatch.setattr(app_module.printing, "jobs", fake.get_jobs)
    return fake


@pytest.fixture
def client(cups):
    application = app_module.create_app(mail_db=":memory:")
    application.config.update(TESTING=True, SECRET_KEY="test")
    return application.test_client()


@pytest.fixture
def mail_store(client):
    return client.application.config["MAIL_STORE"]


class FakeUrlfetch:
    """Stand-in for the network -- tests never make a real request."""

    def __init__(self):
        self.requested = []
        self.result = (b"", "label.pdf")
        self.fail_with = None

    def fetch_url(self, url, max_bytes):
        self.requested.append((url, max_bytes))
        if self.fail_with:
            raise FetchError(self.fail_with)
        return self.result


@pytest.fixture
def urlfetch(monkeypatch):
    fake = FakeUrlfetch()
    monkeypatch.setattr(app_module.urlfetch, "fetch_url", fake.fetch_url)
    return fake


def upload(client, data, filename="label.pdf", mode="auto"):
    return client.post(
        "/upload",
        data={"label": (io.BytesIO(data), filename), "mode": mode},
        content_type="multipart/form-data",
        follow_redirects=False,
    )


def token_from(response):
    assert response.status_code == 302, response.data
    assert "/review/" in response.headers["Location"]
    return response.headers["Location"].rsplit("/", 1)[-1]


# --- the happy path ------------------------------------------------------

def test_index_renders(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"Choose a label" in resp.data


def test_upload_then_print(client, cups, letter_with_label):
    token = token_from(upload(client, letter_with_label))

    review = client.get(f"/review/{token}")
    assert review.status_code == 200
    assert b"cropped to" in review.data

    png = client.get(f"/preview/{token}.png")
    assert png.status_code == 200
    assert png.data[:8] == b"\x89PNG\r\n\x1a\n"

    printed = client.post(f"/print/{token}", data={"copies": "2", "darkness": "9"},
                          follow_redirects=True)
    assert printed.status_code == 200
    assert b"Sent to the printer" in printed.data

    assert len(cups.submitted) == 1
    job = cups.submitted[0]
    assert job["copies"] == 2
    assert job["darkness"] == 9
    assert job["media"] == "4x6.Fullbleed"
    assert job["pdf"][:5] == b"%PDF-"


def test_printing_consumes_the_token(client, letter_with_label):
    token = token_from(upload(client, letter_with_label))
    client.post(f"/print/{token}")

    again = client.post(f"/print/{token}", follow_redirects=True)
    assert b"expired" in again.data


def test_pasted_link_is_fetched_and_normalized(client, cups, urlfetch, label_4x6):
    urlfetch.result = (label_4x6, "label.pdf")

    resp = client.post("/upload",
                       data={"url": " https://example.com/label.pdf ", "mode": "auto"},
                       content_type="multipart/form-data", follow_redirects=False)
    token = token_from(resp)

    assert urlfetch.requested == [("https://example.com/label.pdf",
                                   app_module.MAX_UPLOAD_BYTES)]
    assert client.get(f"/review/{token}").status_code == 200


def test_file_wins_over_url_when_both_are_present(client, cups, urlfetch, label_4x6):
    resp = client.post(
        "/upload",
        data={"label": (io.BytesIO(label_4x6), "label.pdf"),
             "url": "https://example.com/other.pdf", "mode": "auto"},
        content_type="multipart/form-data", follow_redirects=False)
    token_from(resp)
    assert urlfetch.requested == []


def test_unreachable_link_is_reported(client, urlfetch):
    urlfetch.fail_with = "Could not reach that link: timed out"
    resp = client.post("/upload", data={"url": "https://example.com/label.pdf"},
                       content_type="multipart/form-data", follow_redirects=True)
    assert b"Could not reach that link" in resp.data


def test_fetched_link_still_enforces_the_suffix_allowlist(client, urlfetch):
    urlfetch.result = (b"whatever", "label.exe")
    resp = client.post("/upload", data={"url": "https://example.com/label.exe"},
                       content_type="multipart/form-data", follow_redirects=True)
    assert b"not supported" in resp.data


def test_png_upload_is_accepted(client, cups):
    from PIL import Image

    img = Image.new("RGB", (400, 600), "white")
    img.paste((0, 0, 0), (40, 40, 360, 560))
    buf = io.BytesIO()
    img.save(buf, format="PNG")

    token = token_from(upload(client, buf.getvalue(), "label.png"))
    assert client.get(f"/review/{token}").status_code == 200


# --- input validation ----------------------------------------------------

def test_missing_file_is_reported(client):
    resp = client.post("/upload", data={"mode": "auto"},
                       content_type="multipart/form-data", follow_redirects=True)
    assert b"Choose a file, or paste a link" in resp.data


def test_unsupported_type_is_reported(client):
    resp = upload(client, b"MZ\x90\x00", "virus.exe")
    body = client.get(resp.headers["Location"]).data if resp.status_code == 302 else resp.data
    assert b"not supported" in body


def test_blank_page_is_reported(client, blank_page):
    resp = upload(client, blank_page)
    assert resp.status_code == 302
    assert client.get(resp.headers["Location"], follow_redirects=True).data.count(b"blank")


def test_corrupt_pdf_is_reported(client):
    resp = upload(client, b"%PDF-1.4 not really a pdf")
    assert resp.status_code == 302
    body = client.get(resp.headers["Location"], follow_redirects=True).data
    assert b"flash error" in body


def test_copies_are_clamped(client, cups, label_4x6):
    token = token_from(upload(client, label_4x6))
    client.post(f"/print/{token}", data={"copies": "9999"})
    assert cups.submitted[0]["copies"] == app_module.MAX_COPIES


def test_nonsense_copies_fall_back_to_one(client, cups, label_4x6):
    token = token_from(upload(client, label_4x6))
    client.post(f"/print/{token}", data={"copies": "lots"})
    assert cups.submitted[0]["copies"] == 1


def test_unknown_token_is_handled(client):
    assert client.get("/review/nope", follow_redirects=True).status_code == 200
    assert client.get("/preview/nope.png").status_code == 404


# --- printer trouble -----------------------------------------------------

def test_print_failure_is_shown_to_the_user(client, cups, label_4x6):
    token = token_from(upload(client, label_4x6))
    cups.fail_with = "the 'labels' print queue does not exist on this machine"

    resp = client.post(f"/print/{token}", follow_redirects=True)
    assert b"Could not print" in resp.data
    assert b"does not exist" in resp.data


def test_offline_printer_disables_the_print_button(client, cups, label_4x6):
    token = token_from(upload(client, label_4x6))
    cups.ready = False
    cups.status = "printer labels is stopped."

    resp = client.get(f"/review/{token}")
    assert b"disabled" in resp.data
    assert b"printer is not ready" in resp.data


def test_jobs_are_listed_and_cancellable(client, cups):
    cups.jobs = [Job(id="labels-7", user="mike", size="12288",
                     submitted="Sat 09 Aug 2026")]

    listing = client.get("/")
    assert b"labels-7" in listing.data

    client.post("/cancel/labels-7")
    assert cups.cancelled == ["labels-7"]


def test_healthz(client, cups):
    assert client.get("/healthz").status_code == 200

    cups.ready = False
    assert client.get("/healthz").status_code == 503


# --- admin / mail history -------------------------------------------------

def test_admin_page_with_no_mail_configured(client):
    resp = client.get("/admin")
    assert resp.status_code == 200
    assert b"isn't set up" in resp.data
    assert b"No mail yet" in resp.data


def test_admin_lists_a_received_message_and_its_preview(client, mail_store, label_4x6):
    from labelserver import normalize
    pdf, result = normalize.normalize_upload(label_4x6, "label.pdf")
    preview = normalize.render_preview(pdf)
    mail_store.add_message("amazon@example.com", "Your return label", "",
                           [{"filename": "label.pdf", "pdf": pdf, "preview": preview,
                             "summary": result.describe(),
                             "label_shaped": result.label_shaped}])

    listing = client.get("/admin")
    assert b"Your return label" in listing.data
    assert b"amazon@example.com" in listing.data
    assert b"label.pdf" in listing.data

    attachment_id = mail_store.list_messages()[0].attachments[0].id
    png = client.get(f"/admin/preview/{attachment_id}.png")
    assert png.status_code == 200
    assert png.data[:8] == b"\x89PNG\r\n\x1a\n"


def test_admin_shows_a_note_when_no_attachment_was_found(client, mail_store):
    mail_store.add_message("someone@example.com", "just a link",
                           "No PDF or image attachment found in this email.", [])
    resp = client.get("/admin")
    assert b"No PDF or image attachment" in resp.data


def test_admin_use_sends_a_mail_attachment_through_the_normal_review_flow(
        client, cups, mail_store, label_4x6):
    from labelserver import normalize
    pdf, result = normalize.normalize_upload(label_4x6, "label.pdf")
    preview = normalize.render_preview(pdf)
    mail_store.add_message("amazon@example.com", "label", "",
                           [{"filename": "label.pdf", "pdf": pdf, "preview": preview,
                             "summary": result.describe(),
                             "label_shaped": result.label_shaped}])
    attachment_id = mail_store.list_messages()[0].attachments[0].id

    resp = client.post(f"/admin/use/{attachment_id}", follow_redirects=False)
    token = token_from(resp)

    review = client.get(f"/review/{token}")
    assert review.status_code == 200

    client.post(f"/print/{token}")
    assert cups.submitted[0]["pdf"] == pdf


def test_admin_use_with_unknown_attachment_is_handled(client):
    resp = client.post("/admin/use/999", follow_redirects=True)
    assert b"gone" in resp.data


def test_admin_delete_removes_one_message(client, mail_store):
    mail_store.add_message("a@example.com", "keep", "", [])
    doomed = mail_store.add_message("b@example.com", "delete me", "", [])

    client.post(f"/admin/delete/{doomed}")

    subjects = [m.subject for m in mail_store.list_messages()]
    assert subjects == ["keep"]


def test_admin_delete_all_clears_history(client, mail_store):
    mail_store.add_message("a@example.com", "one", "", [])
    mail_store.add_message("b@example.com", "two", "", [])

    client.post("/admin/delete-all")

    assert mail_store.list_messages() == []


# --- pending store -------------------------------------------------------

def test_pending_store_expires_old_entries():
    store = app_module.PendingStore(ttl=0.0)
    token = store.add(app_module.Pending(b"pdf", b"png", "a.pdf", "summary"))
    assert store.get(token) is None


def test_pending_store_evicts_beyond_limit():
    store = app_module.PendingStore(limit=2)
    tokens = [store.add(app_module.Pending(b"", b"", f"{i}.pdf", ""))
              for i in range(4)]

    alive = [t for t in tokens if store.get(t) is not None]
    assert len(alive) == 2
    assert alive == tokens[-2:], "should keep the newest"


# --- printing helpers ----------------------------------------------------

def test_job_number_is_extracted():
    assert Job("labels-42", "mike", "1", "now").number == "42"


def test_cancel_rejects_bogus_ids():
    with pytest.raises(PrintError, match="job id"):
        printing.cancel("labels-1; rm -rf /")


def test_submit_rejects_empty_pdf():
    with pytest.raises(PrintError, match="nothing to print"):
        printing.submit(b"")


def test_explain_translates_cups_errors():
    assert "run scripts/install.sh" in printing._explain(
        "lp: Error - unknown destination `labels'", "labels")
    assert "rejecting jobs" in printing._explain(
        "lp: Destination labels is not accepting jobs.", "labels")
    assert printing._explain("", "labels")  # never returns empty


def test_unknown_mode_falls_back_to_auto(client, letter_with_label):
    """A hand-crafted form post must not 500 the server."""
    resp = client.post(
        "/upload",
        data={"label": (io.BytesIO(letter_with_label), "l.pdf"), "mode": "wat"},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 302
    assert "/review/" in resp.headers["Location"]
