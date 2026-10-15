#!/bin/bash
# tests/run-all.sh, THE one test entrypoint.
# Static pass first, python3 -m py_compile on every .py, bash -n on every .sh,
# node --check on every .js (skipped without node), then every asserting test file in
# this directory. Nonzero exit on ANY failure, so CI (.github/workflows/ci.yml) and an
# user's pre-push run get the same one-command verdict.
#
# File inventory comes from git: tracked PLUS untracked-but-not-ignored, so a brand-new
# hook is compile-checked BEFORE its first commit (the repo's deny-by-default .gitignore
# keeps state/logs/local instances out automatically). Falls back to find(1) on a
# gitless export. Linux/CI-safe: the macOS-only bits (plutil; launchctl is never reached
# under ORGANISM_DRY) self-skip inside the individual tests, and every test sandboxes
# HOME and its seams into mktemp dirs, nothing here can touch a live ~/.claude.
# Run:  bash claude/hooks/tests/run-all.sh
set -u
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
cd "$ROOT" || exit 1
. "$ROOT/claude/hooks/lib/color.sh"
# colorize a child test's streamed output: leading ✓ green, ✗/FAILED red (children are
# piped, so they print plain; this repaints for the terminal only, CI stays plain)
paint() { if [ -n "$CG" ]; then sed -e "s/^\(  *\)✓/\1${CG}✓${CX}/" -e "s/^\(  *\)✗/\1${CR}✗${CX}/" -e "s/FAILED/${CR}FAILED${CX}/g"; else cat; fi; }

PASS=0; FAIL=0; FAILED=""
note_fail() { FAIL=$((FAIL+1)); FAILED="$FAILED
  ✗ $1"; }

files() {
  # tracked + untracked-but-not-ignored; the [ -f ] filter drops files deleted from
  # the working tree but still in the index (a pending `git rm` must not fail CI).
  if git -C "$ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    git -C "$ROOT" ls-files --cached --others --exclude-standard
  else
    find . -name .git -prune -o -type f -print | sed 's|^\./||'
  fi | while IFS= read -r f; do [ -f "$f" ] && printf '%s\n' "$f"; done
}

h1 "static: py_compile"
while IFS= read -r f; do
  if out=$(python3 -m py_compile "$f" 2>&1); then PASS=$((PASS+1)); else
    echo "✗ py_compile $f"; printf '%s\n' "$out" | head -8; note_fail "py_compile $f"
  fi
done < <(files | grep '\.py$')

h1 "static: bash -n"
while IFS= read -r f; do
  if out=$(bash -n "$f" 2>&1); then PASS=$((PASS+1)); else
    echo "✗ bash -n $f"; printf '%s\n' "$out" | head -8; note_fail "bash -n $f"
  fi
done < <(files | grep '\.sh$')

h1 "static: node --check"
if command -v node >/dev/null 2>&1; then
  while IFS= read -r f; do
    if out=$(node --check "$f" 2>&1); then PASS=$((PASS+1)); else
      echo "✗ node --check $f"; printf '%s\n' "$out" | head -8; note_fail "node --check $f"
    fi
  done < <(files | grep '\.js$')
else
  echo "  - node not available: .js syntax check skipped"
fi

h1 "tests"
for t in "$ROOT"/claude/hooks/tests/test_*.py "$ROOT"/claude/hooks/tests/test_*.sh; do
  [ -f "$t" ] || continue
  name=$(basename "$t")
  echo "${CB}-- $name${CX}"
  case "$t" in
    *.py) python3 "$t" 2>&1 | paint; rc=${PIPESTATUS[0]} ;;
    *.sh) bash "$t" 2>&1 | paint; rc=${PIPESTATUS[0]} ;;
  esac
  if [ "$rc" -eq 0 ]; then PASS=$((PASS+1)); else note_fail "$name"; fi
done

echo
if [ "$FAIL" -eq 0 ]; then
  echo "run-all: ${CG}${CB}ALL PASS${CX} ($PASS checks)"
  exit 0
else
  echo "run-all: ${CR}${CB}$FAIL FAILED${CX} / $PASS passed$FAILED"
  exit 1
fi
