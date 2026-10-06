<!-- Title: `<prefix>: <outcome>`, using the same prefix as the branch. CI checks it. -->

## What

<!-- The change, in terms of the behavior someone would notice. -->

## Why

<!-- The problem or requirement behind it. Link the issue or design doc. -->

## Screenshots

<!-- Required when the PR changes a path in CONTRIBUTING.md's `ui-paths` marker; delete
     the section otherwise. Same viewport, state and scroll position in both shots.
     Before comes from the base branch (git worktree, never git stash). For a screen
     that didn't exist, write `n/a: <reason>` instead. Don't commit the images:
     `.github/scripts/push-screenshots.sh <pr> <before> <after>` publishes them and
     prints the links. CI checks this (GIT-11). Retake both when what they show
     changes: a UI change (CI catches it), or a fix to data or a backend the screen
     renders (CI can't see that). Say what changed and when. -->

**Before:** <!-- ![before](https://github.com/<owner>/<repo>/blob/<sha>/<path>.png?raw=true) -->

**After:** <!-- ![after](https://github.com/<owner>/<repo>/blob/<sha>/<path>.png?raw=true) -->

## Reviewer notes

<!-- Deliberately changed assertions, and the requirement change behind each one.
     Risky areas, follow-ups, and anything logged in TECH-DEBT.md. -->

## Test plan

- [ ] Tests written first; `.github/scripts/tdd-check.sh` passes
- [ ] UI changes: before and after screenshots above
- [ ] Affected skills updated
