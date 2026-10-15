#!/bin/bash
# tests/test_converge.sh, asserting tests for the converge loop:
# converge.sh, the git-hooks/ self-hooks, and install-agents.sh's new --list / --freshness
# modes, the `marker` manifest field, the self-label reload skip, and the stale-marker report.
#
# Every test sandboxes HOME into a mktemp dir and runs with ORGANISM_DRY=1, so nothing here can
# touch the real ~/.claude or the real launchd. Linux/CI-safe: no launchctl is ever reached.
# Run:  bash claude/hooks/tests/test_converge.sh
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"

FAILS=0
ok()  { printf '  ✓ %s\n' "$1"; }
bad() { printf '  ✗ %s\n' "$1"; FAILS=$((FAILS+1)); }
has() { # name haystack needle
  case "$2" in *"$3"*) ok "$1" ;; *) bad "$1 (missing: $3)" ;; esac
}
hasnt() { case "$2" in *"$3"*) bad "$1 (unexpected: $3)" ;; *) ok "$1" ;; esac; }

newhome() { # -> prints a fresh sandboxed HOME with a per_host-only host.json
  local t; t=$(mktemp -d)
  mkdir -p "$t/.claude/state" "$t/la"
  printf '{"roles":{"per_host":true,"singleton_bookkeeping":false,"singleton_sideeffect":false}}' \
    > "$t/.claude/state/host.json"
  printf '%s' "$t"
}
run_ia() { # run install-agents.sh in a sandbox; args passed through
  HOME="$H" ORGANISM_DRY=1 ORGANISM_LA="$H/la" ORGANISM_HOOKS="$REPO/claude/hooks" \
  ORGANISM_LOCAL_AGENTS="${LOCAL:-$H/none}" ORGANISM_HOST_JSON="$H/.claude/state/host.json" \
  CLAUDE_HOST_JSON="$H/.claude/state/host.json" \
    bash "$REPO/launchd/install-agents.sh" "$@" 2>&1
}

echo "== install-agents --list =="
H=$(newhome); trap 'rm -rf "$H"' EXIT
LOCAL="$H/agents.local"
printf 'localsingleton|singleton_bookkeeping|python3 $HOOKS/local/x.py|cal:9:0|localsingleton\n' > "$LOCAL"
OUT=$(run_ia --list)
has "converge is a per_host job"            "$OUT" "converge|per_host|yes|cal:6:30|converge"
has "unassigned singleton still lists its marker" "$OUT" "localsingleton|singleton_bookkeeping|no|cal:9:0|localsingleton"
has "calw job lists its weekly schedule"    "$OUT" "logrotate|per_host|yes|calw:0:4:30|logrotate"
has "markerless job has an empty 5th field" "$OUT" "reconcile|singleton_bookkeeping|no|cal:7:45|"
hasnt "--list never touches launchd"          "$OUT" "loaded"

echo "== install-agents --freshness =="
printf '2026-07-05' > "$H/.claude/state/logrotate-last-success"
printf '1999-01-01' > "$H/.claude/state/localsingleton-last-success"  # job NOT assigned here
OUT=$(run_ia --freshness)
has   "assigned job with a marker reports its date" "$OUT" "logrotate 2026-07-05"
has   "assigned job with no marker file: (never)"   "$OUT" "converge (never)"
hasnt "unassigned job is not watched here"          "$OUT" "localsingleton"
# shared markers must appear exactly once, not once per job
n=$(run_ia --freshness | tr ' ' '\n' | grep -cx 'converge')
[ "$n" = 1 ] && ok "shared/duplicate markers are de-duplicated" || bad "marker repeated $n times"

echo "== agents.local back-compat + marker field =="
LOCAL="$H/agents.local"
printf 'four-field|per_host|python3 $HOOKS/x.py|cal:1:0\n' > "$LOCAL"          # no trailing marker
printf 'five-field|per_host|python3 $HOOKS/y.py|cal:2:0|shared\n' >> "$LOCAL"
printf 'five-again|per_host|python3 $HOOKS/z.py|cal:3:0|shared\n' >> "$LOCAL"  # same marker, 2 jobs
OUT=$(run_ia --list)
has "4-field local row still parses"      "$OUT" "four-field|per_host|yes|cal:1:0|"
has "marker may differ from the label"    "$OUT" "five-field|per_host|yes|cal:2:0|shared"
OUT=$(run_ia --freshness)
n=$(printf '%s' "$OUT" | tr ' ' '\n' | grep -cx 'shared')
[ "$n" = 1 ] && ok "two jobs sharing one marker report it once" || bad "shared marker appeared $n times"

echo "== stale-marker report (reports, never deletes) =="
unset LOCAL
printf '2020-01-01' > "$H/.claude/state/retired-job-last-success"
OUT=$(run_ia)
has "stale marker is named"                 "$OUT" "stale marker  state/retired-job-last-success"
[ -f "$H/.claude/state/retired-job-last-success" ] \
  && ok "stale marker is NOT deleted (a wrong delete re-arms a --fallback gate)" \
  || bad "stale marker was deleted (unsafe: marker to job is not 1:1)"
hasnt "a declared marker is not reported stale" "$OUT" "state/logrotate-last-success"

echo "== self-label: the converge agent never reloads itself =="
OUT=$(ORGANISM_SELF_LABEL="com.organism.converge" run_ia)
has "converge renders with the self note" "$OUT" "self: reload skipped"

echo "== ORGANISM_LA without ORGANISM_DRY forces dry (real-launchd guard) =="
OUT=$(HOME="$H" ORGANISM_LA="$H/la" ORGANISM_HOOKS="$REPO/claude/hooks" \
      ORGANISM_LOCAL_AGENTS="$H/none" ORGANISM_HOST_JSON="$H/.claude/state/host.json" \
      CLAUDE_HOST_JSON="$H/.claude/state/host.json" \
      bash "$REPO/launchd/install-agents.sh" 2>&1)
has   "forcing-dry warning is printed" "$OUT" "forcing dry"
hasnt "launchctl was never reached"    "$OUT" "loaded   com.organism"

echo "== converge.sh failure paths leave the marker UNARMED =="
C=$(newhome)
OUT=$(HOME="$C" ORGANISM_DRY=1 bash "$REPO/converge.sh" --scheduled 2>&1); rc=$?
[ "$rc" -ne 0 ] && ok "--scheduled refuses an unlinked ~/.claude/hooks" || bad "--scheduled succeeded on an undeployed HOME"
has "refusal explains itself" "$OUT" "is not a symlink"
[ -f "$C/.claude/state/converge-last-success" ] \
  && bad "marker armed on failure: the dead-man's-switch is defeated" \
  || ok "marker unarmed on failure"

ln -s "$C/elsewhere" "$C/.claude/hooks"
OUT=$(HOME="$C" ORGANISM_DRY=1 bash "$REPO/converge.sh" --scheduled 2>&1); rc=$?
[ "$rc" -ne 0 ] && ok "--scheduled refuses hooks linked elsewhere" || bad "accepted a foreign hooks symlink"

OUT=$(HOME="$C" ORGANISM_DRY=1 ORGANISM_CONVERGE_RUNNING=1 bash "$REPO/converge.sh" --scheduled 2>&1); rc=$?
[ "$rc" -eq 0 ] && ok "recursion guard exits 0 without re-running" || bad "recursion guard exit=$rc"
has "recursion guard says so" "$OUT" "re-entered"
rm -rf "$C"

echo "== git self-hooks =="
for h in post-merge post-rewrite post-checkout; do
  [ -L "$REPO/git-hooks/$h" ] && ok "git-hooks/$h is a symlink (tracked, mode 120000)" || bad "git-hooks/$h not a symlink"
done
[ -x "$REPO/git-hooks/self-converge.sh" ] && ok "self-converge.sh is executable" || bad "self-converge.sh not executable"
[ -x "$REPO/converge.sh" ] && ok "converge.sh is executable" || bad "converge.sh not executable"
# the deny-by-default .gitignore must not swallow the extensionless hook symlinks
if git -C "$REPO" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  for h in post-merge post-rewrite post-checkout; do
    git -C "$REPO" check-ignore -q "git-hooks/$h" \
      && bad "git-hooks/$h is gitignored: a fresh clone would have NO hooks" \
      || ok "git-hooks/$h is tracked by git"
  done
fi
# post-checkout must ignore a FILE checkout ($3=0) and act only on a branch checkout ($3=1)
OUT=$(cd "$REPO" && bash git-hooks/post-checkout aaa bbb 0 2>&1); rc=$?
[ "$rc" -eq 0 ] && [ -z "$OUT" ] && ok "post-checkout ignores a file checkout" || bad "post-checkout ran on a file checkout: $OUT"
# post-rewrite must ignore `commit --amend` and act only on `rebase`
OUT=$(cd "$REPO" && printf '' | bash git-hooks/post-rewrite amend 2>&1); rc=$?
[ "$rc" -eq 0 ] && [ -z "$OUT" ] && ok "post-rewrite ignores commit --amend" || bad "post-rewrite ran on an amend: $OUT"

echo
if [ "$FAILS" -eq 0 ]; then echo "test_converge: ALL PASS"; exit 0; fi
echo "test_converge: $FAILS FAILED"; exit 1
