#!/usr/bin/env bash
# ui-evidence-check.sh — a PR that changes the UI shows it: the description's
# `## Screenshots` section has a before and an after image.
#
# Usage: ui-evidence-check.sh --body-file <file> [<base-ref>]
#          <file>      the PR description (CI writes it from the event payload)
#          <base-ref>  what the PR merges into (default origin/<default>)
# Output: STATUS<TAB>GIT-11<TAB>message
# Exit:   1 on FAIL, 2 on a usage error, else 0.
#
# UI paths are the globs in a `<!-- ui-paths: glob glob -->` marker at the start
# of a line in CONTRIBUTING.md (`*` matches across `/`). No marker, or an empty
# one, means the repo has no UI and the check skips.
#
# In the Screenshots section, a line starting `**Before:**` needs a markdown
# image or an <img> tag, or `n/a` plus a reason for a screen that didn't exist.
# A line starting `**After:**` always needs an image. HTML comments don't count,
# so the untouched template fails.
#
# Freshness: images linked at a commit (`/blob/<sha>/`) are compared by content.
# They're fresh while that commit's UI files are the same as HEAD's, so amends
# and rebases that leave the UI alone keep them fresh. The commit is usually a
# screenshot commit from push-screenshots.sh, on top of the code it shows and
# kept under refs/screenshots/* (fetched here, since checkouts don't). Images not
# linked at a commit pass, with a note that freshness went unchecked. A data or
# backend change that alters the screen is invisible here.
#
# This file is copied into each repo as .github/scripts/ui-evidence-check.sh.
# The canonical copy lives in the repo-standards skill. Edit that one.
# Written for macOS bash 3.2.

set -u
set -f  # ui-paths globs are patterns, not filenames to expand

SECTION='Screenshots'      # the PR template heading this check reads
MAX_LISTED_FILES=3         # UI files named in a FAIL
IMAGE='!\[[^]]*\]\([^)]+\)|<img[[:space:]][^>]*src='
PINNED='/blob/[0-9a-f]{7,40}/'   # a GitHub link to a file at one commit
SCREENSHOT_REFS=refs/screenshots   # where push-screenshots.sh keeps screenshot commits

body_file=""; base=""
while [ $# -gt 0 ]; do
  case "$1" in
    --body-file) body_file=${2:-}; shift 2 ;;
    -*) echo "unknown option: $1" >&2; exit 2 ;;
    *) base=$1; shift ;;
  esac
done

globs=""
if [ -f CONTRIBUTING.md ]; then
  globs=$(sed -n 's/^<!-- *ui-paths: *\(.*[^ ]\) *-->.*/\1/p' CONTRIBUTING.md | head -1)
fi
if [ -z "$globs" ]; then
  printf 'SKIP\tGIT-11\tNo ui-paths marker in CONTRIBUTING.md, so no UI to screenshot\n'; exit 0
fi

if [ -z "$base" ]; then
  default=$(git symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null | sed 's#^origin/##')
  base=origin/${default:-main}
fi
if ! git rev-parse --verify --quiet "$base" >/dev/null; then
  printf 'SKIP\tGIT-11\tBase %s not found\n' "$base"; exit 0
fi

is_ui() {
  for g in $globs; do
    # shellcheck disable=SC2254 # $g is intentionally a pattern
    case "$1" in $g) return 0 ;; esac
  done
  return 1
}

ui_files=""; all_ui=""; n=0
while IFS= read -r f; do
  [ -z "$f" ] && continue
  if is_ui "$f"; then
    n=$((n + 1)); all_ui="$all_ui
$f"
    [ "$n" -le "$MAX_LISTED_FILES" ] && ui_files="$ui_files $f"
  fi
done <<EOF
$(git diff --name-only "$base...HEAD")
EOF

if [ "$n" -eq 0 ]; then
  printf 'PASS\tGIT-11\tNo UI paths changed\n'; exit 0
fi
if [ -z "$body_file" ] || [ ! -f "$body_file" ]; then
  echo "usage: ui-evidence-check.sh --body-file <file> [<base-ref>]" >&2; exit 2
fi

# The section's lines, with HTML comments removed (single- and multi-line).
section=$(sed -e ':a' -e '/<!--/{' -e '/-->/!{N;ba' -e '}' -e 's/<!--.*-->//g' -e '}' "$body_file" \
  | awk -v h="$SECTION" '/^## /{ on = ($0 ~ "^## *" h "[[:space:]]*$") ; next } on')

line_for() { printf '%s\n' "$section" | grep -iE "^\*\*$1:?\*\*:?" | head -1; }
before=$(line_for Before); after=$(line_for After)

missing=""
if ! printf '%s' "$before" | grep -Eq "$IMAGE" && ! printf '%s' "$before" | grep -Eiq '\*\*:?[[:space:]]*n/a[^[:alnum:]]+[[:alnum:]]'; then
  missing="$missing **Before:** (an image, or n/a: <reason>)"
fi
if ! printf '%s' "$after" | grep -Eq "$IMAGE"; then
  missing="$missing **After:** (an image)"
fi

more=""; [ "$n" -gt "$MAX_LISTED_FILES" ] && more=" and $((n - MAX_LISTED_FILES)) more"
if [ -z "$section" ]; then
  printf 'FAIL\tGIT-11\tUI changed (%s%s) but the PR has no ## %s section with before and after images\n' \
    "${ui_files# }" "$more" "$SECTION"
  exit 1
elif [ -n "$missing" ]; then
  printf 'FAIL\tGIT-11\tUI changed (%s%s); ## %s is missing%s\n' "${ui_files# }" "$more" "$SECTION" "$missing"
  exit 1
fi
# UI files that differ between commit $1 and HEAD.
ui_changed_since() {
  git diff --name-only "$1" HEAD 2>/dev/null | while IFS= read -r f; do is_ui "$f" && printf ' %s' "$f"; done
}

shas=$(printf '%s\n%s\n' "$before" "$after" | grep -oE "$PINNED" | cut -d/ -f3 | sort -u)
if [ -z "$shas" ]; then
  printf 'PASS\tGIT-11\tUI changed (%s%s) and the PR shows before and after (not pinned to a commit, so freshness was not checked)\n' \
    "${ui_files# }" "$more"
  exit 0
fi
fetched=""
for sha in $shas; do
  short=$(printf '%s' "$sha" | cut -c1-7)
  if ! git cat-file -e "$sha^{commit}" 2>/dev/null && [ -z "$fetched" ]; then
    git fetch -q --no-tags origin "+$SCREENSHOT_REFS/*:$SCREENSHOT_REFS/*" 2>/dev/null || true
    fetched=1
  fi
  if ! git cat-file -e "$sha^{commit}" 2>/dev/null; then
    printf 'FAIL\tGIT-11\tScreenshots are stale: they link %s, which is not on origin (not pushed, or replaced). Retake them and publish with .github/scripts/push-screenshots.sh <pr> <images>\n' "$short"
    exit 1
  fi
  changed=$(ui_changed_since "$sha")
  if [ -n "$changed" ]; then
    printf 'FAIL\tGIT-11\tScreenshots are stale: retake both, because the UI changed since %s (%s), and publish them with .github/scripts/push-screenshots.sh\n' "$short" "${changed# }"
    exit 1
  fi
done
printf 'PASS\tGIT-11\tUI changed (%s%s) and the PR shows current before and after screenshots\n' "${ui_files# }" "$more"
