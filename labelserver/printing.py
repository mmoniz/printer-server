"""
Talking to CUPS.

Everything goes through the ``lp``/``lpstat``/``cancel`` command line tools
rather than a CUPS binding, so there is nothing to compile on the Pi and the
behaviour matches what you would get typing the same commands over SSH.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass

DEFAULT_QUEUE = "labels"
TIMEOUT = 20  # seconds; CUPS is local, so anything slower is a hang


class PrintError(Exception):
    """A print command failed."""


@dataclass(frozen=True)
class Job:
    id: str
    user: str
    size: str
    submitted: str

    @property
    def number(self) -> str:
        """The numeric part of ``labels-42``, which is what ``cancel`` wants."""
        return self.id.rsplit("-", 1)[-1]


def _run(args: list[str], stdin: bytes | None = None) -> subprocess.CompletedProcess:
    if shutil.which(args[0]) is None:
        raise PrintError(
            f"{args[0]} not found -- is CUPS installed? (sudo apt install cups-client)"
        )
    try:
        return subprocess.run(
            args, input=stdin, capture_output=True, timeout=TIMEOUT, check=False
        )
    except subprocess.TimeoutExpired as exc:
        raise PrintError(f"{args[0]} timed out after {TIMEOUT}s") from exc


def submit(pdf: bytes, queue: str = DEFAULT_QUEUE, title: str = "label",
           copies: int = 1, darkness: int | None = None,
           media: str = "4x6.Fullbleed") -> str:
    """Send a PDF to the queue. Returns the CUPS job id."""
    if not pdf:
        raise PrintError("nothing to print")
    if copies < 1:
        raise PrintError("copies must be at least 1")

    args = ["lp", "-d", queue, "-t", title, "-n", str(copies),
            "-o", f"media={media}", "-o", "fit-to-page=false"]
    if darkness is not None:
        args += ["-o", f"Darkness={darkness}"]
    args += ["--"]  # read from stdin

    proc = _run(args, stdin=pdf)
    if proc.returncode != 0:
        raise PrintError(_explain(proc.stderr.decode(errors="replace").strip(), queue))

    # lp prints: request id is labels-7 (1 file(s))
    match = re.search(r"request id is (\S+)", proc.stdout.decode(errors="replace"))
    if not match:
        raise PrintError("could not work out the job id from lp's reply")
    return match.group(1)


def _explain(stderr: str, queue: str) -> str:
    """Turn CUPS's terser errors into something a family member can act on."""
    lowered = stderr.lower()
    if "unknown destination" in lowered or "does not exist" in lowered:
        return (
            f"the '{queue}' print queue does not exist on this machine -- "
            "run scripts/install.sh to create it"
        )
    if "not accepting" in lowered:
        return f"the '{queue}' queue is rejecting jobs (cupsenable {queue} to resume)"
    return stderr or "lp failed without saying why"


def jobs(queue: str = DEFAULT_QUEUE) -> list[Job]:
    """Jobs currently queued or printing."""
    proc = _run(["lpstat", "-o", queue])
    if proc.returncode != 0:
        return []

    out = []
    for line in proc.stdout.decode(errors="replace").splitlines():
        # e.g. "labels-7   mike   12288   Sat 09 Aug 2026 08:15:02 PM EDT"
        parts = line.split(None, 3)
        if len(parts) == 4 and parts[0].startswith(queue):
            out.append(Job(id=parts[0], user=parts[1], size=parts[2],
                           submitted=parts[3]))
    return out


def cancel(job_id: str, queue: str = DEFAULT_QUEUE) -> None:
    """Cancel one job."""
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", job_id):
        raise PrintError("that does not look like a job id")

    proc = _run(["cancel", job_id])
    if proc.returncode != 0:
        raise PrintError(proc.stderr.decode(errors="replace").strip()
                         or f"could not cancel {job_id}")


def queue_state(queue: str = DEFAULT_QUEUE) -> tuple[bool, str]:
    """Return (ready, human readable status) for the queue."""
    proc = _run(["lpstat", "-p", queue])
    text = proc.stdout.decode(errors="replace").strip()

    if proc.returncode != 0 or not text:
        return False, f"queue '{queue}' not found"

    first = text.splitlines()[0]
    ready = "is idle" in first or "now printing" in first
    if "disabled" in first:
        return False, first
    return ready, first


def printers() -> list[str]:
    """All configured queue names, for diagnostics."""
    proc = _run(["lpstat", "-a"])
    if proc.returncode != 0:
        return []
    return [line.split()[0] for line in proc.stdout.decode().splitlines()
            if line.strip()]
