# printer-server

A home print server for 4x6 shipping labels, running on a Raspberry Pi 2
with a USB thermal printer. Family members print by AirPrint, by IPP from a
Mac/PC, or by uploading a label — a file, a pasted link, or an email — to a
web page on the Pi. Everything stays on the LAN: no cloud, no port
forwarding, no accounts. See [README.md](README.md) for the pitch and
architecture diagram.

<!-- doc-kit: off | A household's own print server with no product audience: its few users learn the one-page web app by using it, and it has no release process -->

## Skills are the source of truth

This repo is skills-first. How the system works lives in `.claude/skills/`, not in this file, a wiki, or commit messages.

- Every component has its own skill at `.claude/skills/<component>/SKILL.md`. It covers how the component works, its gotchas, the history behind its decisions, and the trade-offs taken.
- Before changing a component, load its skill. If the code contradicts the skill, stop and work out which one is wrong before going further. Tell the user which it was.
- A change isn't done until the component's skill reflects it. Add new decisions to the skill's **Decisions** log with the date and the reason.
- A new component gets a new skill in the same PR. Copy the section layout of an existing skill.
- Keep this file short. Component detail belongs in a skill, not here.

### Skills index

Load the skill before working in its area, not just when something breaks. **MANDATORY** skills must be read before that kind of change. A change usually touches exactly one of these; if it touches two, check both skills rather than guessing which conventions carry over.

| Domain | Skill | Use When |
|---|---|---|
| **Test integrity** | [`test-integrity`](.claude/skills/test-integrity/SKILL.md) | **MANDATORY** before modifying, deleting or skipping an existing test |
| **TSPL protocol** | [`tspl-printer-protocol`](.claude/skills/tspl-printer-protocol/SKILL.md) | **MANDATORY** before touching `labelserver/tspl.py`, `cups/rastertotspl` or the golden fixtures. The mistakes are silent and only show up on physical labels. |
| **CUPS chain** | [`cups-print-chain`](.claude/skills/cups-print-chain/SKILL.md) | **MANDATORY** before touching `scripts/make_ppd.py` or `cups/LabelPrinter.ppd`. The PPD is generated and defines the contract the filter depends on. |
| Label normalization | [`label-normalization`](.claude/skills/label-normalization/SKILL.md) | Any change to `labelserver/normalize.py`, especially a detection constant |
| Web app | [`label-web-app`](.claude/skills/label-web-app/SKILL.md) | Core Flask routes, `PendingStore`, talking to CUPS, or any test that stubs CUPS (`labelserver/app.py`, `labelserver/printing.py`) |
| Link intake | [`url-link-intake`](.claude/skills/url-link-intake/SKILL.md) | Uploading by pasted link or dragged image, SSRF hardening, login-walled pages (`labelserver/urlfetch.py`) |
| Mail intake | [`mail-intake`](.claude/skills/mail-intake/SKILL.md) | Uploading by email, IMAP polling, the mail history admin panel (`labelserver/mail*.py`, `admin.html`) |
| Pi deployment | [`pi-deployment`](.claude/skills/pi-deployment/SKILL.md) | Installing, the hardware and network failure modes, tuning against real stock (`scripts/install.sh`, the systemd units) |

## Git rules

At the start of every session, before editing anything:

1. `git fetch origin`
2. Check that you're **not** on `main` and that your branch contains `origin/main`:
   `git merge-base --is-ancestor origin/main HEAD && echo synced`
3. If you're on `main`, if the branch is behind, or if the branch holds work unrelated to this session's task, start a fresh branch:
   `git switch -c <prefix>/<short-description> origin/main`

Local `main` is often stale. Always branch from `origin/main`, and refresh the local copy with `git branch -f main origin/main` when it isn't checked out. If a branch was cut from a stale base, `git rebase origin/main` and **re-run the tests** before pushing.

Each session gets its own branch. Don't add new work to a branch left over from an earlier session.

**One session, one PR, one commit.** Follow-up requests and minor fixes in the same session stay on the session's branch, amended into its commit (`git commit --amend`, then `git push --force-with-lease`), with the PR title and description reframed to cover the whole change. Open a second PR only when a change has to merge on its own: an earlier PR already merged, a runtime or framework upgrade, a standards change other work waits on, or the user asks for one. Split commits only when reviewers need to see the steps separately, and say why in the description.

Branch names are `<prefix>/<kebab-description>`, where the prefix says what kind of work it is: `fix`, `feat`, `docs`, `skills`, `infra`, `chore`, `refactor`, `test`. CONTRIBUTING.md defines each prefix.

Commit subjects and PR titles use the same prefixes and describe the outcome: `<prefix>: <outcome>`, e.g. `fix: a throttled sync answers 429, not 502`. PR descriptions follow `.github/pull_request_template.md`.

`main` is protected. Never push to it directly, and never try to get around the protection (`--no-verify`, admin bypass). Every change lands through a PR.

**Auto-fix is on for every PR.** As soon as you open a PR, or pick up one from an earlier session, turn on the Claude Code app's Auto-fix for it (`set_monitor` with `auto_fix: true`) without asking. Then fix CI failures, merge conflicts and review comments as its events arrive, instead of polling CI.
<!-- pr-autofix: on -->

## Test-driven development

Every behavior change is test-first:

1. **Red:** write or change a test that describes the behavior. Run it and confirm it fails for the reason you expect. Report that failure.
2. **Green:** write the least code that makes it pass. Run the relevant suite.
3. **Refactor:** clean up with the tests still green.

- A bug fix starts with a test that reproduces the bug.
- **A failing test is correct until proven otherwise.** Before modifying, deleting or skipping an existing test, state the rule it encodes and classify it, with evidence:
  - **Implementation bug:** fix the code and leave the test alone.
  - **Stale test:** the requirement changed. Update it, and add an `Updated: <rule> changed per <reference>` comment beside each changed assertion.
  - **Test bug:** fix the setup (mock, fixture, import) and keep the assertions.
  - **Unknown:** stop and ask.

  Never delete a test; skip it with a reason and let a human confirm. If 3+ tests fail after one change, fix the change, not the tests. List every deliberately changed assertion under Reviewer notes in the PR. The full protocol is the MANDATORY `test-integrity` skill, and the `.claude/hooks/test-integrity.py` hook enforces this step.
- Exempt: docs (including skill docs) and changes to test code only. Scripts inside skills are code and need tests. The rule is the same on every branch, whatever its prefix, so refactors need test changes too.
- Before opening a PR, run `.github/scripts/tdd-check.sh`. CI runs it too.

## Engineering practices

Use current best practice, verified against sources rather than remembered:

- Before choosing or upgrading a language, framework, library, CI action or tool, check its current stable version and recommended usage in the official docs or release notes. Record significant choices in the owning skill's Decisions, with the source and date.
- Don't introduce deprecated APIs or patterns. When you come across one, fix it or log it in TECH-DEBT.md.
- Keep the baseline green: linter, tests, shellcheck, actionlint, the secret scan and the PPD staleness check pass in CI on every PR. Toolchain versions are pinned (`.python-version`), `uv.lock` is committed, and dependency updates are automated with Dependabot.
- No magic numbers or strings. A literal whose meaning isn't obvious where it's used gets a name, defined once next to the code that owns it: a constant, an enum member, or a table. `0`, `1`, `""`, user-facing text and expected values in tests stay inline. `ruff check .` enforces this for the Python (`PLR2004`, strings included); for shell, CI YAML and the systemd units, apply it by hand.
- **The Pi 2 constrains dependencies.** armv7, 32-bit, 1 GB RAM, and the venv reuses apt's numpy/Pillow builds (`--system-site-packages`) rather than compiling from source. Before adding a runtime dependency, confirm it ships an armv7 wheel or an apt package, then add it to **both** `requirements.txt` (what the Pi installs) and `pyproject.toml` (what `uv.lock` is built from); `tests/test_dependencies.py` fails if they differ. See `pi-deployment`.
- Keep secrets out of the repo. Validate input at trust boundaries. Give CI tokens and services the least privilege they need.
- **Stub external boundaries by monkeypatching on the module, not the import.** CUPS (`printing.submit`/`cancel`/`queue_state`/`jobs`), IMAP (`mail.imaplib.IMAP4_SSL`), the network (`urlfetch`'s opener) — every one of these is faked in tests via `monkeypatch.setattr(app_module.printing, ...)` style patching of the module attribute, never the name imported into the test file. Patching the import leaves the app still calling the real thing.
- **Model a real layout in tests, not a convenient one.** A fixture that only passes because it's tidy proves nothing — see `tests/conftest.py`'s `letter_with_label` (a fold line and terms text specifically placed to break a naive bounding box) and `tests/fixtures/ups_multiband_redacted.pdf` (an actual carrier PDF kept alongside its minimal synthetic equivalent, since a real one exercises the messier layout a hand-built fixture wouldn't think to). When a real file is the right fixture and it carries personal information, redact it — this repo is **public** on GitHub, and git history doesn't forget. Check a new fixture for metadata leakage (`PdfReader.metadata`, `.xmp_metadata`, `.extract_text()`) before committing it, not just the visible content.
- **Leave a trail for anything that runs unattended.** The hardware watchdog, the network watchdog, and persistent journald all exist because a background process failing silently at 2 AM is worse than a slower failure with a log line explaining why — see `pi-deployment`'s failure-mode list for the actual incidents that drove each one. Apply the same instinct to new background work: `mailpoll.py`'s poll failures are logged and retried, never silently swallowed.
- **A login-walled link genuinely cannot be fetched by the server, on purpose.** Amazon return labels are the case that comes up. Automating a login is out of scope regardless of how convenient it would be — the `url-link-intake` and `mail-intake` skills cover the two real fallbacks (drag/paste the rendered image; email the label to a dedicated mailbox instead), which both route around the problem through a channel that already has the necessary session, rather than trying to acquire one.
- **Comments use `--`, not an em dash.** Consistent across the whole codebase and the skills themselves; match it in anything you write here.

## Agents

Use project agents from `.claude/agents/` when the task matches one. An agent that needs a second copy of the code uses `git worktree`, never `git stash`, because the stash stack is shared by every worktree and session.

Commands you give the user to run never carry a placeholder for a value you already have; fill it in. If you don't know the value, ask for it, and after they've run the command ask whether to store it for next time. If you can guess it, declare it as a variable on its own line first and reference it below, so there is one spot to change. Give your best guess as the value and say plainly that you guessed.
<!-- command-style: on -->

## Tech debt

Log known shortcuts and deferred fixes in `TECH-DEBT.md`, not as TODOs in code. If you take a shortcut, log it in the same PR.

## Commands

```bash
uv sync                                              # create .venv on the pinned Python (3.11) from uv.lock
uv run pytest                                        # full suite, no printer or Pi needed
uv run pytest tests/test_normalize.py                # one file
uv run ruff check .                                  # lint, including magic values
shellcheck -S warning scripts/*.sh                   # required before touching scripts/
python3 scripts/make_ppd.py && git diff --exit-code cups/LabelPrinter.ppd  # PPD staleness check
actionlint                                           # workflows
.github/scripts/tdd-check.sh                         # TDD check vs origin/main
```

Run the web app locally against a real or fake queue:

```bash
LABELSERVER_QUEUE=my_queue uv run python -m flask --app labelserver.app run --port 8080
```

CI (`.github/workflows/ci.yml`) runs the TDD check, the PR-title check, the UI-evidence check, a secret scan, and a `build` job (actionlint, ruff, pytest, shellcheck, the PPD staleness check). All of it should pass locally before pushing.
