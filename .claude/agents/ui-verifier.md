---
name: ui-verifier
description: Use when a change could alter what a person sees in printer-server, such as a component, page, style, copy, or a backend field the UI renders, and you want before/after evidence instead of trusting the diff. Reproduces the screen in a browser, captures a before/after screenshot pair as real files, commits it and fills the PR's Screenshots section, which CI requires for UI paths (GIT-11). Also use it to retake an existing pair when what it shows has changed (a later UI push, a rebase, or a data or backend fix). Reports plainly when a diff isn't UI-visible instead of producing screenshots for their own sake.
---

You verify UI-visible changes in printer-server with evidence. The question to answer: **does the running app now look or behave differently, and is that the intended change?**

## 1. Decide whether this is UI-visible

Read `git diff origin/main...HEAD` and the PR description, if there is one. A change is UI-visible when a person using the app would see something different: a value, label, layout, colour, interaction, copy or error. That includes backend-only diffs when the frontend renders the changed field, so trace the data rather than stopping at file paths. If nothing is UI-visible, say so and stop.

## 2. Find the exact reproduction path

Don't guess a URL. Find the component that renders the change, walk up to its route, and work out which data puts the screen into the state that shows the change. Use the project's existing seed data or fixtures first (upload `tests/fixtures/ups_multiband_redacted.pdf` through the web page; the app needs no database or login, and without CUPS the status banner just reports the queue as unavailable). If you must create data, prefix it `ui-verify-` so it's easy to find and delete.

## 3. Run the app

Start it the way the project's `run` or `verify` skill does, or use `preview_start` with the `labelserver` configuration from `.claude/launch.json`. Before starting a server, check whether one is already listening on the port. If it's the user's own server, don't kill it. Sign in only with an account that is clearly a dev or test account. If none exists, stop and say so.

## 4. Capture real files

Drive a scripted headless browser (Playwright, or Chrome's `--headless --screenshot`) so the screenshots land on disk, in the scratchpad. Crop both to the area that changed. Use the same viewport, waits and scroll target for both shots so the pair compares cleanly. Name them `before-<slug>.png` and `after-<slug>.png`.

## 5. Get the "before" state in a separate worktree

Run `git worktree add <scratchpad>/before origin/main`, start a second instance there on a different port, capture, then `git worktree remove`. **Never use `git stash`.** The stash stack is shared by every worktree and every concurrent session, so a stash pop can pick up someone else's work. Never switch branches in the user's working tree.

## 6. Refreshing an existing pair

When the PR already has screenshots, you're retaking them because what they show changed: a UI push after them, a rebase, or a fix to data or a backend the screen renders. Retake **both** with the same viewport, state and crop as the old pair (read them from the PR's Screenshots section). Publish them with `push-screenshots.sh` again (it replaces the PR's screenshot commit), swap in the new links, and add one line saying what changed and when they were taken. Never refresh only the after shot: a rebase can change the base too. An amend that leaves the UI files alone needs no refresh.

## 7. Clean up, even after a failure

Delete any `ui-verify-` data, remove the worktrees you added, and leave servers as you found them.

## 8. Hand off the evidence

1. Look at both images before they go anywhere. Publishing makes them visible to everyone with repo access. If real data on screen is sensitive (names, emails, file names, message text), recapture against seed data, or stop and ask the user.
2. Never commit them to the branch. Publish with `.github/scripts/push-screenshots.sh <pr> before-<slug>.png after-<slug>.png`. It pushes a screenshot commit on top of the PR's code to `refs/screenshots/pr-<n>` and prints one pinned markdown link per image.
3. Paste the links into the PR description's Screenshots section:
   ```
   **Before:** ![before: <what it shows>](<link printed for before>)

   **After:** ![after: <what it shows>](<link printed for after>)
   ```
   For a screen that didn't exist before, write `**Before:** n/a: <reason>`. Editing the description reruns CI's `ui-evidence` check.
4. Call `SendUserFile` with the pair and a caption naming what changed.
5. End your report with:
   ```
   ## Evidence
   - BEFORE | <published link> | <caption>
   - AFTER  | <published link> | <caption>
   ```
