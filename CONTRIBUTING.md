# Contributing to printer-server

<!-- branch-prefixes: fix feat docs skills infra chore refactor test -->

## One-time setup

After cloning, turn on the repo's git hooks. The pre-push hook blocks direct pushes to `main`:

```bash
git config core.hooksPath .githooks
```

`main` is also protected on GitHub: changes land only through PRs, and those PRs must pass the `tdd` and `build` checks.

## Workflow

1. Sync and branch from the latest `origin/main`:
   ```bash
   git fetch origin
   git switch -c <prefix>/<short-description> origin/main
   ```
2. Use one branch per session, and one PR with one commit for it. Fold follow-ups and minor fixes into that commit (`git commit --amend`, `git push --force-with-lease`) and reframe the PR's title and description, rather than stacking fix-up commits or opening more PRs. A change that must merge on its own (an upgrade, a standards change other work depends on) gets its own PR. Don't reuse a branch after it has merged.
3. Keep the branch current by rebasing on `origin/main` before you open a PR, then re-run the tests. A green run on the old base proves nothing about the new one.
4. Open a PR into `main` using the PR template (What / Why / Reviewer notes / Test plan). Nobody commits directly to `main`.

## Commit subjects and PR titles

Use `<prefix>: <outcome>`, with the same prefixes as branch names. Describe what's now true, not what you did:

- ✅ `fix: a throttled sync answers 429, not 502`
- ❌ `fix sync bug`, or `Updated handler`

PRs are squash-merged, so the PR title becomes the commit on `main`. CI's `pr-title` check enforces the format, and `Revert "…"` titles are allowed.

## Branch prefixes

Branch names are `<prefix>/<kebab-description>`, for example `fix/login-redirect-loop`.

| Prefix | Use for |
|---|---|
| `fix` | Bug fixes |
| `feat` | New functionality |
| `docs` | Documentation only (README, CONTRIBUTING, TECH-DEBT) |
| `skills` | Adding or changing skills |
| `infra` | CI, hooks, Dependabot, tooling, Pi deployment scripts |
| `chore` | Dependency bumps and housekeeping |
| `refactor` | Restructuring with no behavior change |
| `test` | Tests only |

To add a prefix, add it to this table and to the `branch-prefixes` comment at the top of this file. The audit script reads that comment.

## Test-driven development

Write the test first. Watch it fail, make it pass with the least code, then refactor. Bug fixes start with a test that reproduces the bug.

CI's `tdd` check (`.github/scripts/tdd-check.sh`) fails a PR that changes source files without changing any tests. These are exempt:

- docs (`*.md`, `docs/`), including skill docs. Scripts inside skills are source and need tests.
- changes to test code only
- non-behavioral files: `.github/`, `.githooks/`, lockfiles, images, license
- anything matched by the `tdd-exempt` globs below

**Changing an existing test?** First classify why it fails: implementation bug, stale test, test bug or unknown. The full protocol is in [`.claude/skills/test-integrity/SKILL.md`](.claude/skills/test-integrity/SKILL.md), and CLAUDE.md summarizes it. Any deliberately changed assertion gets an `Updated: … per …` comment and a line under Reviewer notes in the PR.

The same rule applies on every branch, whatever its prefix. A refactor that touches source still needs a test change, such as a characterization test that pins the behavior being preserved.

<!-- tdd-exempt: .python-version cups/LabelPrinter.ppd -->

## UI changes

A PR that changes what someone sees shows it. Its description's **Screenshots** section has a before and an after screenshot, taken with the same viewport, state and scroll position. Take the before shot from the base branch in a `git worktree`, never with `git stash`. A screen that didn't exist gets `n/a: <reason>` for before. Don't commit them. `.github/scripts/push-screenshots.sh <pr> <before> <after>` publishes them in a screenshot commit under `refs/screenshots/`, which never merges, and prints the links to paste. If the repo has the `ui-verifier` agent (`.claude/agents/ui-verifier.md`), it captures the pair.

CI's `ui-evidence` check (`.github/scripts/ui-evidence-check.sh`) fails a PR that changes a path matching the `ui-paths` globs below and lacks either image. The globs are the UI's own files. A backend change that alters what the UI renders needs screenshots too, but CI can't see that, so it's on you and the reviewer. An empty marker means the repo has no UI.

**Keep them current.** Retake both screenshots whenever what they show changes, just as you'd fix the title or description: a change to a UI file, or a fix to the data or backend the screen renders. CI catches the first; the second is on you. Publish the new pair with the same script, paste the new links, and note what changed. An amend or rebase that leaves the UI files alone needs nothing.

<!-- ui-paths: labelserver/templates/* labelserver/static/* -->

## Engineering baseline

- Pin toolchain versions (`.python-version`, which is 3.11 because that's what the Pi runs) and commit lockfiles.
- The formatter, linter, type checker and tests all pass in CI. Run them locally with `uv sync --locked`, `uv run ruff check .` and `uv run pytest`.
- No magic numbers or strings: name any literal whose meaning isn't obvious where it's used, and define it once. The linter's magic-value rules (ruff's `PLR2004`, with strings included) enforce it in CI. Tests are exempt, because their expected values are the spec.
- Dependency updates arrive through Dependabot. Review and merge them promptly.
- Before adopting or upgrading a tool, check its current stable version in the official docs, then record the choice in the owning skill's Decisions.

## Skills are part of the change

This repo is skills-first: `.claude/skills/<component>/SKILL.md` is the source of truth for each component.

- If you change how a component behaves, update its skill in the same PR.
- If you make a design decision, add a dated entry to the skill's **Decisions** section. Say what you chose, why, and what you rejected.
- If you add a component, add its skill and list it in the index in `CLAUDE.md`.
- If you hit a gotcha, add it to **Gotchas** so the next person doesn't.

## Tech debt

If you knowingly take a shortcut, add a row to `TECH-DEBT.md` in the same PR. When you pay one off, move its row to **Resolved** and link the PR.

## PR checklist

- [ ] Branch is prefixed and based on current `origin/main`
- [ ] Tests written first; `.github/scripts/tdd-check.sh` passes
- [ ] Format, lint, typecheck and tests pass
- [ ] UI changes have before and after screenshots in the PR
- [ ] Affected skills updated (behavior, gotchas, decisions)
- [ ] New shortcuts logged in `TECH-DEBT.md`
- [ ] Shell scripts pass `shellcheck -S warning scripts/*.sh`, and `cups/LabelPrinter.ppd` is regenerated (`python3 scripts/make_ppd.py`) when `scripts/make_ppd.py` changes
