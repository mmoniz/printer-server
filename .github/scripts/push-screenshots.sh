#!/usr/bin/env bash
# push-screenshots.sh — publish a PR's screenshots without committing them to the
# branch.
#
# Usage: push-screenshots.sh <pr-number> <image>...
#
# Builds a commit on top of HEAD that adds the images under screenshots/, using a
# throwaway index so the branch, index and working tree are untouched. Pushes it
# to refs/screenshots/pr-<n> on origin, replacing any earlier one, and prints a
# markdown image link per file, pinned at that commit, for the PR's Screenshots
# section.
#
# The commit's parent is the code the screenshots were taken from, so
# ui-evidence-check.sh (GIT-11) can compare its UI files with the PR's HEAD.
# refs/screenshots/* aren't branches: they never merge and don't show in branch
# lists, but they keep the commit (and the image links) alive.
#
# Exit: 2 on a usage error, 1 if the push fails, else 0.
#
# This file is copied into each repo as .github/scripts/push-screenshots.sh.
# The canonical copy lives in the repo-standards skill. Edit that one.
# Written for macOS bash 3.2.

set -eu

SCREENSHOT_REFS=refs/screenshots   # namespace ui-evidence-check.sh fetches
SCREENSHOT_DIR=screenshots         # where the images sit in the screenshot commit

usage() { echo "usage: push-screenshots.sh <pr-number> <image>..." >&2; exit 2; }

[ $# -ge 2 ] || usage
pr=$1; shift
case "$pr" in ''|*[!0-9]*) usage ;; esac
for f in "$@"; do [ -f "$f" ] || { echo "no such file: $f" >&2; usage; }; done

head=$(git rev-parse HEAD)
index=$(mktemp "${TMPDIR:-/tmp}/push-screenshots.XXXXXX")
trap 'rm -f "$index"' EXIT
GIT_INDEX_FILE=$index git read-tree "$head"
for f in "$@"; do
  blob=$(git hash-object -w "$f")
  GIT_INDEX_FILE=$index git update-index --add --cacheinfo "100644,$blob,$SCREENSHOT_DIR/$(basename "$f")"
done
tree=$(GIT_INDEX_FILE=$index git write-tree)
shot=$(printf 'docs: screenshots for PR %s, taken at %s\n' "$pr" "$head" | git commit-tree "$tree" -p "$head")

ref="$SCREENSHOT_REFS/pr-$pr"
git update-ref "$ref" "$shot"
git push -q --force origin "$shot:$ref"

# https://github.com/<owner>/<repo> from an ssh or https GitHub remote; otherwise the raw remote URL.
url=$(git remote get-url origin)
case "$url" in
  git@github.com:*) base="https://github.com/${url#git@github.com:}" ;;
  https://github.com/*) base=$url ;;
  *) base=$url ;;
esac
base=${base%.git}

for f in "$@"; do
  name=$(basename "$f")
  printf '![%s](%s/blob/%s/%s/%s?raw=true)\n' "${name%.*}" "$base" "$shot" "$SCREENSHOT_DIR" "$name"
done
