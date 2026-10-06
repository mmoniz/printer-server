---
name: test-integrity
description: MANDATORY before modifying, deleting or skipping any existing test. Classify the failure as implementation bug, stale test, test bug or unknown, with evidence, before touching the test. Not needed for writing new tests.
---
<!-- component-paths: .claude/skills/test-integrity .claude/hooks/test-integrity.py -->

# Test integrity

## Overview
Load this before modifying, deleting or skipping an **existing** test. Writing new tests is the red step of TDD and doesn't need it.

**A test is a specification. A failing test is correct until proven otherwise.** The fastest way to a green suite, deleting or loosening the test, also deletes the specification.

## How it works

### 1. Understand the test

State the rule the test encodes in one sentence: "This test verifies that ___." If you can't, read the test, the code under test, and the owning skill again. You don't yet understand the test well enough to change it.

### 2. Check it against the source of truth

In priority order:
1. The owning skill (`.claude/skills/<component>/SKILL.md`), including its Decisions
2. The current implementation of the public interface under test
3. The API contract or design doc the skill links to
4. CLAUDE.md

### 3. Classify it, with evidence

| Classification | Meaning | Allowed | Forbidden |
|---|---|---|---|
| **Implementation bug** | The test is right and the code is wrong | Fix the code only | Any change to the test |
| **Stale test** | The requirement really changed | Update the assertion, and add a comment beside it: `Updated: <rule> changed per <reference>` | Changing an expectation without saying why |
| **Test bug** | The mechanics are wrong: mock target, fixture, import path, payload | Fix the setup | Changing what the test asserts |
| **Unknown** | You can't tell which of the above | Stop and ask the user | Any modification |

Write the classification and its evidence in your reply before you edit anything. For example:

> **Implementation bug.** `test_viewer_cannot_create` asserts 403, and `access-control` says viewers are read-only. The endpoint returns 200, so the code is wrong.

### 4. Hard rules

- **Never delete a test.** If it's truly obsolete, skip it with a reason that cites the requirement change, and let a human confirm the deletion.
- **Every skip needs a reason**, for example `@pytest.mark.skip(reason="…")`, `it.skip("… per <ref>")` or `t.Skip("…")`.
- **Never weaken an assertion** to match the output. `== 0.85` becoming `> 0` encodes the bug as the spec.
- **3+ failures from one change** means you broke a core invariant. Go back to your change, not the tests.
- **List every deliberately changed assertion** under *Reviewer notes* in the PR description, with its reference.

### 5. Verify

- Run the whole relevant suite, not just the test you touched.
- If you classified an **implementation bug**, confirm that `git diff --name-only` shows no test files changed.
- If you classified a **stale test**, confirm that every changed assertion has its `Updated:` comment.

### Enforcement


`.claude/hooks/test-integrity.py` is a PreToolUse hook on Edit and Write, wired in `.claude/settings.json`. The first time in a session that an edit deletes a test, changes an assertion or adds a skip, the hook blocks it and replies with this protocol. Once the classification is stated, retrying the same edit is allowed. The hook is a reminder; this protocol and code review are the real enforcement.

## Gotchas

| If you're thinking… | What's really happening |
|---|---|
| "Just update the expected value to match." | You're writing the bug into the spec. |
| "This test isn't important." | Every deleted test is a lost specification. |
| "The code is right, so the test must be outdated." | Then cite the requirement change. |
| "Too many failures; I'll narrow the test." | Many failures means one broken invariant. |
| "I'll put the test back once the fix works." | You'll write it to match the broken code. |

- **The hook only sees Edit and Write.** Rewriting a test through Bash (`sed -i`) bypasses it, and so does the protocol. Don't.

## Decisions

### 2026-10-05 — Classify before touching a test

- **Context:** Agents under pressure to get a suite green tend to change the test instead of the code.
- **Decision:** Every change to an existing test starts with a stated rule, a classification and evidence. Adopted from the `repo-standards` skill.
- **Why:** The classification makes "fix the code" the default, and leaves a reviewable trail (the `Updated:` comments and the PR's Reviewer notes).
- **Alternatives considered:** Banning test edits outright, which blocks legitimate stale-test updates. Reviewing test edits only in the PR, which is too late for an agent's loop.
- **Status:** active

## Trade-offs

- A retried edit passes the hook, so the hook can't *prove* the protocol was followed. It only guarantees the protocol was shown. Code review of the `Updated:` comments is the backstop.
- Assertion detection is a regex per language family. An unusual assertion style can slip past the hook, and that's accepted in exchange for zero dependencies.
