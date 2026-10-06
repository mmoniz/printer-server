#!/usr/bin/env bash
# tdd-check.sh — flag source changes that arrive without test changes.
#
# Usage: tdd-check.sh [<base-ref>]        check <base>...HEAD as one change (PR/CI mode)
#        tdd-check.sh --history [<N>]     check the last N non-merge commits on the
#                                         default branch, one by one (audit mode;
#                                         N defaults to DEFAULT_HISTORY)
#        tdd-check.sh --classify          read paths on stdin, print kind<TAB>path
# Output: STATUS<TAB>ID<TAB>message   (TST-05 for PR mode, TST-04 for history mode)
# Exit:   1 if PR mode FAILs, else 0.
#
# A file is one of three kinds:
#   test    : lives under test/ tests/ __tests__/ spec/ testdata/ fixtures/, or is
#             named like *_test.* *.test.* *.spec.* test_*.py *Test.java ...
#   exempt  : docs (including skill docs; scripts inside skills are source),
#             non-behavioral files (see is_exempt), plus globs in
#             a `<!-- tdd-exempt: glob glob -->` marker in CONTRIBUTING.md
#   source  : everything else
# A change fails when it touches source and no test file. Exemptions go by path
# only, never by branch name, since a branch's scope can grow past its name.
#
# This file is copied into each repo as .github/scripts/tdd-check.sh so CI can run
# it. The canonical copy lives in the repo-standards skill. Edit that one.
# Written for macOS bash 3.2.

set -u
set -f  # globs in tdd-exempt are patterns, not filenames to expand

DEFAULT_HISTORY=30     # --history sample: about a month of commits on a solo repo
MAX_LISTED_FILES=3     # source files named in a TST-05 FAIL
MAX_LISTED_COMMITS=5   # untested commits named in a TST-04 WARN

extra_exempt=""
if [ -f CONTRIBUTING.md ]; then
  extra_exempt=$(sed -n 's/^<!-- *tdd-exempt: *\(.*[^ ]\) *-->.*/\1/p' CONTRIBUTING.md | head -1)
fi

is_test() {
  case "$1" in
    test/*|tests/*|*/test/*|*/tests/*|__tests__/*|*/__tests__/*|spec/*|*/spec/*|\
    testdata/*|*/testdata/*|fixtures/*|*/fixtures/*) return 0 ;;
  esac
  case "${1##*/}" in
    *_test.*|*.test.*|*.spec.*|*_spec.*|test_*.py|conftest.py|\
    *Test.java|*Test.kt|*Tests.swift|*Test.swift|*Tests.cs|*Test.cs) return 0 ;;
  esac
  return 1
}

is_exempt() {
  case "$1" in
    *.md|*.mdx|*.rst|*.txt|docs/*|*/docs/*) return 0 ;;   # docs, incl. skill docs
    .github/*|.githooks/*|.gitignore|.gitattributes|.editorconfig|LICENSE*) return 0 ;;
    *.lock|*-lock.json|*-lock.yaml|go.sum|*.png|*.jpg|*.jpeg|*.gif|*.svg|*.ico) return 0 ;;
  esac
  for g in $extra_exempt; do
    # shellcheck disable=SC2254 # $g is intentionally a pattern
    case "$1" in $g) return 0 ;; esac
  done
  return 1
}

# classify <newline-separated files> -> sets n_src, n_test, src_list
classify() {
  n_src=0; n_test=0; src_list=""
  while IFS= read -r f; do
    [ -z "$f" ] && continue
    if is_test "$f"; then n_test=$((n_test + 1))
    elif is_exempt "$f"; then :
    else n_src=$((n_src + 1)); [ "$n_src" -le "$MAX_LISTED_FILES" ] && src_list="$src_list $f"
    fi
  done <<EOF
$1
EOF
}

default_branch() {
  d=$(git symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null | sed 's#^origin/##')
  [ -n "$d" ] && { echo "$d"; return; }
  git rev-parse --verify --quiet origin/main >/dev/null && { echo main; return; }
  git rev-parse --verify --quiet origin/master >/dev/null && { echo master; return; }
  echo main
}

if [ "${1:-}" = --classify ]; then
  while IFS= read -r f; do
    [ -z "$f" ] && continue
    if is_test "$f"; then k="test"; elif is_exempt "$f"; then k="exempt"; else k="source"; fi
    printf '%s\t%s\n' "$k" "$f"
  done
  exit 0
fi

if [ "${1:-}" = --history ]; then
  n=${2:-$DEFAULT_HISTORY}
  ref="origin/$(default_branch)"
  git rev-parse --verify --quiet "$ref" >/dev/null || ref=HEAD
  if ! git rev-parse --verify --quiet "$ref" >/dev/null; then
    printf 'SKIP\tTST-04\tNo commits yet\n'; exit 0
  fi
  checked=0; bad=0; bad_list=""
  for sha in $(git log --no-merges --format=%h -n "$n" "$ref"); do
    classify "$(git diff-tree --no-commit-id --name-only -r --root "$sha")"
    [ "$n_src" -eq 0 ] && continue
    checked=$((checked + 1))
    if [ "$n_test" -eq 0 ]; then
      bad=$((bad + 1)); [ "$bad" -le "$MAX_LISTED_COMMITS" ] && bad_list="$bad_list $sha"
    fi
  done
  if [ "$checked" -eq 0 ]; then
    printf 'SKIP\tTST-04\tNo source-changing commits in the last %s\n' "$n"
  elif [ "$bad" -eq 0 ]; then
    printf 'PASS\tTST-04\tAll %s source-changing commits in the last %s came with tests\n' "$checked" "$n"
  else
    printf 'WARN\tTST-04\t%s of %s source-changing commits had no test changes (e.g.%s)\n' "$bad" "$checked" "$bad_list"
  fi
  exit 0
fi

base=${1:-origin/$(default_branch)}
if ! git rev-parse --verify --quiet "$base" >/dev/null; then
  printf 'SKIP\tTST-05\tBase %s not found\n' "$base"; exit 0
fi
classify "$(git diff --name-only "$base...HEAD")"

if [ "$n_src" -eq 0 ]; then
  printf 'PASS\tTST-05\tNo source changes vs %s (docs/skills/tests only)\n' "$base"; exit 0
elif [ "$n_test" -gt 0 ]; then
  printf 'PASS\tTST-05\t%s source and %s test file(s) changed vs %s\n' "$n_src" "$n_test" "$base"; exit 0
fi
printf 'FAIL\tTST-05\t%s source file(s) changed vs %s with no test changes (e.g.%s). Write the failing test first\n' "$n_src" "$base" "$src_list"
exit 1
