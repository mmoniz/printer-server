#!/usr/bin/env bash
# commit-check.sh — commit subjects and PR titles read `<prefix>: <outcome>`,
# using the same approved prefixes as branch names.
#
# Usage: commit-check.sh --title "<PR title>"   check one PR title (CI mode)
#        commit-check.sh [<base-ref>]           check each non-merge commit subject
#                                               in <base>..HEAD (default origin/<default>)
# Output: STATUS<TAB>GIT-09<TAB>message, and in range mode a GIT-12 line: one
#         commit per PR is preferred, so more than one warns (never fails)
# Exit:   1 on FAIL, else 0.
#
# Prefixes come from the `<!-- branch-prefixes: ... -->` marker at the start of a
# line in CONTRIBUTING.md, else DEFAULT_PREFIXES. `Revert "..."` subjects pass.
# Squash merges use the PR title as the commit subject, so the title check in CI
# is what keeps the default branch's history consistent.
#
# This file is copied into each repo as .github/scripts/commit-check.sh. The
# canonical copy lives in the repo-standards skill. Edit that one.
# Written for macOS bash 3.2.

set -u

DEFAULT_PREFIXES="fix feat docs backend web skills infra chore refactor test"
MAX_EXAMPLES=3  # bad subjects quoted in a FAIL; enough to show the pattern
PREFERRED_COMMITS=1  # GIT-12: one commit per PR; minor fixes are amended in

prefixes=""
if [ -f CONTRIBUTING.md ]; then
  prefixes=$(sed -n 's/^<!-- *branch-prefixes: *\(.*[^ ]\) *-->.*/\1/p' CONTRIBUTING.md | head -1)
fi
[ -z "$prefixes" ] && prefixes=$DEFAULT_PREFIXES
pattern="^(($(echo "$prefixes" | tr ' ' '|')): [^ ]|Revert \")"

ok() { echo "$1" | grep -Eq "$pattern"; }

if [ "${1:-}" = --title ]; then
  title=${2:-}
  if ok "$title"; then
    printf 'PASS\tGIT-09\tPR title ok\n'; exit 0
  fi
  printf 'FAIL\tGIT-09\tPR title "%s" should read <prefix>: <outcome>; prefixes: %s\n' "$title" "$prefixes"
  exit 1
fi

default=$(git symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null | sed 's#^origin/##')
[ -z "$default" ] && default=main
base=${1:-origin/$default}
if ! git rev-parse --verify --quiet "$base" >/dev/null; then
  printf 'SKIP\tGIT-09\tBase %s not found\n' "$base"; exit 0
fi

total=0; bad=0; examples=""
while IFS= read -r subject; do
  [ -z "$subject" ] && continue
  total=$((total + 1))
  if ! ok "$subject"; then
    bad=$((bad + 1))
    [ "$bad" -le "$MAX_EXAMPLES" ] && examples="$examples \"$subject\""
  fi
done <<EOF
$(git log --no-merges --format=%s "$base..HEAD")
EOF

status=0
if [ "$total" -eq 0 ]; then
  printf 'SKIP\tGIT-09\tNo commits since %s\n' "$base"
elif [ "$bad" -eq 0 ]; then
  printf 'PASS\tGIT-09\tAll %s commit subject(s) read <prefix>: <outcome>\n' "$total"
else
  printf 'FAIL\tGIT-09\t%s of %s commit subject(s) lack a <prefix>: (e.g.%s); prefixes: %s\n' "$bad" "$total" "$examples" "$prefixes"
  status=1
fi

if [ "$total" -gt "$PREFERRED_COMMITS" ]; then
  # shellcheck disable=SC2016 # the backticks are literal, quoting commands in the message
  printf 'WARN\tGIT-12\t%s commits since %s; one per PR is preferred. Fold minor fixes in with `git commit --amend` and `git push --force-with-lease`, then reframe the description\n' "$total" "$base"
elif [ "$total" -eq "$PREFERRED_COMMITS" ]; then
  printf 'PASS\tGIT-12\tOne commit since %s\n' "$base"
fi
exit $status
