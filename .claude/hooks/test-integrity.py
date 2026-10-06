#!/usr/bin/env python3
"""Test-integrity guard: a Claude Code PreToolUse hook for Edit and Write.

Blocks, once per session, an edit that deletes a test, changes or removes an
existing assertion, or adds a skip, and replies with the test-integrity
protocol. Claude must classify the failure before retrying; retrying the same
change in the same session passes. Adding tests and writing new test files
always pass, because that is the red step of TDD.

Test files are identified by .github/scripts/tdd-check.sh --classify, so the
hook and the TDD check can't disagree. If that script is missing, or the input
is malformed, the hook fails open (exit 0); repo-audit reports the gap.

Wire it in .claude/settings.json, never .claude/hooks.json, which Claude Code
does not read. The canonical copy lives in the repo-standards skill.

Exit codes: 0 allow, 2 block (stderr is shown to Claude).
"""

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from collections import Counter

ALLOW, BLOCK = 0, 2  # PreToolUse exit codes; on BLOCK, stderr is shown to Claude
EDIT, WRITE = "Edit", "Write"  # the tools this hook guards; settings.json matches them
CLASSIFY_TIMEOUT_SECONDS = 10  # tdd-check.sh --classify takes milliseconds per path
TEST_KIND = "test"  # the kind tdd-check.sh --classify prints for a test file
STATE_DIR = "claude-test-integrity"  # under the temp dir: one file of seen edits per session

TEST_NAME = re.compile(
    r"\bdef\s+(test_\w+)"                       # python
    r"|\bfunc\s+((?:T|t)est\w*)"                # go, swift
    r"|\bfn\s+(test_\w+)"                       # rust
    r"|@test\s+[\"']([^\"']+)"                  # bats
    r"|\b(?:it|test)\s*\(\s*[\"'`]([^\"'`]+)"   # js/ts
)
ASSERTION = re.compile(
    r"\bassert\w*\b|\bexpect\s*\(|\bXCTAssert\w*|\bt\.(?:Error|Errorf|Fatal|Fatalf)\b"
    r"|\brequire\.\w+\(|\bshould\b|^\s*\[\[?\s"  # last: bats `[ ... ]` checks
)
SKIP = re.compile(
    r"pytest\.mark\.skip|\bpytest\.skip\(|\.skip\(|\bx(?:it|describe|test)\s*\("
    r"|\bt\.Skip\w*\(|@Disabled|@Ignore|\bXCTSkip|^\s*skip\b"
)

PROTOCOL = """TEST INTEGRITY: {path}

{found}

A failing test is correct until proven otherwise. Before retrying, state in your reply:
  1. The rule this test encodes, in one sentence.
  2. Its classification, with evidence (a skill, spec or requirement change):
     - Implementation bug: the test is right. Fix the code; leave the test alone.
     - Stale test: the requirement changed. Update it and add an
       `Updated: <rule> changed per <reference>` comment next to each changed assertion.
     - Test bug: the setup is wrong (mock, fixture, import). Fix the setup; keep the assertions.
     - Unknown: stop and ask the user.
Never delete a test; skip it with a reason and let a human confirm the deletion.
If 3+ tests fail after one change, a core invariant broke: fix the change, not the tests.
Full protocol: .claude/skills/test-integrity/SKILL.md
Retrying the same edit in this session will be allowed."""


def is_test_file(project_dir, path):
    classifier = os.path.join(project_dir, ".github", "scripts", "tdd-check.sh")
    if not os.access(classifier, os.X_OK):
        return None  # unknown: fail open
    rel = os.path.relpath(path, project_dir) if os.path.isabs(path) else path
    try:
        out = subprocess.run([classifier, "--classify"], input=rel + "\n",
                             capture_output=True, text=True, cwd=project_dir,
                             timeout=CLASSIFY_TIMEOUT_SECONDS)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.startswith(TEST_KIND + "\t")


def names(text):
    return {next(g for g in m.groups() if g) for m in TEST_NAME.finditer(text)}


def assertions(text):
    return Counter(line.strip() for line in text.splitlines() if ASSERTION.search(line))


def skips(text):
    return sum(1 for line in text.splitlines() if SKIP.search(line))


def violations(old, new):
    found = []
    deleted = sorted(names(old) - names(new))
    if deleted:
        found.append("Deletes tests: " + ", ".join(deleted))
    gone = sorted((assertions(old) - assertions(new)).elements())
    if gone:
        found.append("Changes or removes assertions:\n" + "\n".join("    " + a for a in gone))
    if skips(new) > skips(old):
        found.append("Adds a skip. Every skip needs a reason, and a human confirms it.")
    return found


def seen_before(session, key):
    state_dir = os.path.join(tempfile.gettempdir(), STATE_DIR)
    os.makedirs(state_dir, exist_ok=True)
    state = os.path.join(state_dir, re.sub(r"[^\w.-]", "_", session) + ".json")
    try:
        with open(state) as f:
            keys = set(json.load(f))
    except (OSError, ValueError):
        keys = set()
    if key in keys:
        return True
    keys.add(key)
    with open(state, "w") as f:
        json.dump(sorted(keys), f)
    return False


def main():
    try:
        data = json.load(sys.stdin)
        tool, args = data["tool_name"], data["tool_input"]
        path = args["file_path"]
    except (ValueError, KeyError, TypeError):
        return ALLOW
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR") or data.get("cwd") or os.getcwd()
    if tool not in (EDIT, WRITE) or not is_test_file(project_dir, path):
        return ALLOW

    if tool == EDIT:
        old, new = args.get("old_string", ""), args.get("new_string", "")
    else:
        try:
            with open(path) as f:
                old = f.read()
        except OSError:
            return ALLOW  # a brand-new test file
        new = args.get("content", "")

    found = violations(old, new)
    if not found:
        return ALLOW
    key = hashlib.sha256("\0".join([path, old, new]).encode()).hexdigest()
    if seen_before(str(data.get("session_id", "unknown")), key):
        return ALLOW
    print(PROTOCOL.format(path=path, found="\n".join(found)), file=sys.stderr)
    return BLOCK


if __name__ == "__main__":
    sys.exit(main())
