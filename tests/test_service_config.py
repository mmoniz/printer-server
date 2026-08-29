"""
Guards a config mistake that has no other way to get caught: PendingStore
(labelserver/app.py) is a plain in-process dict, so labelserver.service must
run gunicorn with exactly one worker. A second worker process gets its own
empty store, and an upload landing on worker A with its preview request
landing on worker B is a silent 404 -- no exception, no log line, just a
broken <img>. See the label-web-app skill for the full explanation.

Nothing else exercises this file, since it's infrastructure config rather
than application code, so a regression here would otherwise only surface as
a confusing bug report on the real Pi.
"""

import re
from pathlib import Path

SERVICE_FILE = Path(__file__).parent.parent / "scripts" / "labelserver.service"


def test_gunicorn_runs_exactly_one_worker():
    exec_start = SERVICE_FILE.read_text()
    match = re.search(r"--workers\s+(\d+)", exec_start)
    assert match, "ExecStart should pass --workers to gunicorn"
    assert match.group(1) == "1", (
        "PendingStore is an in-process dict; more than one worker means "
        "some requests silently miss it. See the label-web-app skill."
    )
