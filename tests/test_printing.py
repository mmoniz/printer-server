"""Characterization tests for the lpstat parsing in labelserver.printing.

The web-app tests stub printing.queue_state/jobs wholesale, so the parsing of
CUPS's output is pinned here, one module down, by stubbing the subprocess.
"""

import subprocess

import pytest

from labelserver import printing


def lpstat_says(monkeypatch, stdout: str, returncode: int = 0):
    def fake_run(args, stdin=None):
        return subprocess.CompletedProcess(args, returncode, stdout.encode(), b"")

    monkeypatch.setattr(printing, "_run", fake_run)


@pytest.mark.parametrize(
    ("line", "ready"),
    [
        ("printer labels is idle.  enabled since Sun Oct  4 21:10:38 2026", True),
        ("printer labels now printing labels-7.  enabled since Sun Oct  4", True),
        ("printer labels disabled since Sun Oct  4 21:10:38 2026 -", False),
        ("printer labels is stopped.", False),
    ],
)
def test_queue_state_reads_the_first_lpstat_line(monkeypatch, line, ready):
    lpstat_says(monkeypatch, line + "\n\tReason text\n")
    assert printing.queue_state("labels") == (ready, line)


def test_queue_state_reports_a_missing_queue(monkeypatch):
    lpstat_says(monkeypatch, "", returncode=1)
    ready, status = printing.queue_state("labels")
    assert not ready
    assert "not found" in status


def test_jobs_parses_each_lpstat_line(monkeypatch):
    lpstat_says(
        monkeypatch,
        "labels-7   mike   12288   Sat 09 Aug 2026 08:15:02 PM EDT\n"
        "labels-8   anna   9216   Sat 09 Aug 2026 08:16:11 PM EDT\n",
    )
    jobs = printing.jobs("labels")
    assert [(j.id, j.user, j.size) for j in jobs] == [
        ("labels-7", "mike", "12288"),
        ("labels-8", "anna", "9216"),
    ]
    assert jobs[0].submitted == "Sat 09 Aug 2026 08:15:02 PM EDT"
    assert jobs[0].number == "7"


def test_jobs_ignores_lines_that_are_not_jobs_in_this_queue(monkeypatch):
    lpstat_says(monkeypatch, "other-3   mike   100   Sat 09 Aug 2026\nshort line\n")
    assert printing.jobs("labels") == []
