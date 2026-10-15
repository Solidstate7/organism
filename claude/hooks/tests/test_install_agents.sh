#!/bin/bash
# tests/test_install_agents.sh, asserting tests for launchd/install-agents.sh's render
# path driven entirely through the existing seams: ORGANISM_DRY=1
# (render-only, no launchctl, also what makes this Linux/CI-safe), ORGANISM_LA (mktemp
# LaunchAgents dir), ORGANISM_LOCAL_AGENTS, ORGANISM_HOST_JSON + CLAUDE_HOST_JSON (the
# role gate reads the latter), and a sandboxed HOME so the script's `mkdir -p
# $HOME/.claude/logs` never touches the real machine. Pins: schedule
# grammar gate, XML escaping, trailing-newline-less agents.local,
# indented comments, and the orphan sweep's dry report.
# plutil lint runs only where available (macOS), self-skips on Linux.
# Run:  bash claude/hooks/tests/test_install_agents.sh
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"

T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT
FAILS=0
ok()   { printf '  ✓ %s\n' "$1"; }
bad()  { printf '  ✗ %s\n' "$1"; FAILS=$((FAILS+1)); }
assert() { # name condition-command...
  local name="$1"; shift
  if "$@" >/dev/null 2>&1; then ok "$name"; else bad "$name"; fi
}

export HOME="$T/home"; mkdir -p "$HOME"
export ORGANISM_HOOKS="$REPO/claude/hooks"     # RUNWRAP must exist → repo copy, not the deploy
export ORGANISM_LA="$T/la"; mkdir -p "$ORGANISM_LA"
export ORGANISM_DRY=1
export ORGANISM_HOST_JSON="$T/host.json"
export CLAUDE_HOST_JSON="$T/host.json"         # role.py's resolver (the env-name split is known)
NS=com.organism

# Grant every role, NO platform_uuid pin → deterministic "all assigned" on any machine
# (a pin would demote singletons on foreign hardware and make the test host-dependent).
cat > "$T/host.json" <<'EOF'
{"roles": {"per_host": true, "singleton_bookkeeping": true, "singleton_sideeffect": true},
 "schedules": {"localjob": "cal:9:30", "logrotate": "bogus:1"}}
EOF

# agents.local fixture: indented comment, '&' in an arg token,
# unknown schedule token, and a final line with NO trailing newline.
export ORGANISM_LOCAL_AGENTS="$T/agents.local"
printf '  # indented comment: must be ignored, not parsed as an agent\nlocaljob|per_host|python3 $HOOKS/local/example-instance.py --flag a&b|cal:7:45\nkeepjob|per_host|python3 $HOOKS/local/example-daemon.py|keepalive\nbadsched|per_host|python3 $HOOKS/x.py|daily:7:45\nweeklyjob|per_host|bash $HOOKS/rotate-logs.sh|calw:1:11:0\nlastline|per_host|bash $HOOKS/rotate-logs.sh|interval:600' > "$ORGANISM_LOCAL_AGENTS"

# pre-existing plists: one whose job now has a bad schedule (must survive untouched),
# one orphan in the namespace (must be reported, not removed, under DRY).
printf 'SENTINEL-DO-NOT-TOUCH\n' > "$ORGANISM_LA/$NS.badsched.plist"
printf 'stray\n' > "$ORGANISM_LA/$NS.stray.plist"

echo "read-only modes"
ORGANISM_LA="$T/la-unmade" bash "$REPO/launchd/install-agents.sh" --list >/dev/null 2>&1
ORGANISM_LA="$T/la-unmade" bash "$REPO/launchd/install-agents.sh" --freshness >/dev/null 2>&1
assert "--list and --freshness create no folders" test ! -e "$T/la-unmade" -a ! -e "$HOME/.claude/logs"

OUT=$(bash "$REPO/launchd/install-agents.sh" 2>&1); RC=$?

echo "render pass"
assert "exits 0" test "$RC" -eq 0
assert "manifest agents rendered (reconcile)"       test -f "$ORGANISM_LA/$NS.reconcile.plist"
assert "keepalive grammar (keepjob)"                grep -q '<key>KeepAlive</key><true/>' "$ORGANISM_LA/$NS.keepjob.plist"
assert "interval grammar (lastline: 600)"           grep -q '<key>StartInterval</key><integer>600</integer>' "$ORGANISM_LA/$NS.lastline.plist"
assert "calw grammar (weeklyjob: Mon 11:00)"        grep -q 'Weekday</key><integer>1</integer><key>Hour</key><integer>11</integer><key>Minute</key><integer>0</integer>' "$ORGANISM_LA/$NS.weeklyjob.plist"
assert "every argv token is its own <string>"       grep -q '<string>--all</string>' "$ORGANISM_LA/$NS.reconcile.plist"
assert "run.sh is argv[0] in every plist"           grep -q "<string>$REPO/claude/hooks/lib/run.sh</string>" "$ORGANISM_LA/$NS.reconcile.plist"

echo "host.json schedules override"
assert "override applied (localjob → 9:30)"      grep -q 'Hour</key><integer>9</integer><key>Minute</key><integer>30</integer>' "$ORGANISM_LA/$NS.localjob.plist"
assert "invalid override token skipped with warning" grep -q "unknown schedule 'bogus:1'" <<<"$OUT"
assert "invalid override renders NO plist"          test ! -f "$ORGANISM_LA/$NS.logrotate.plist"

echo "agents.local (host-local instances)"
assert "local agent rendered"                       test -f "$ORGANISM_LA/$NS.localjob.plist"
assert "'&' in an arg token XML-escaped" grep -q '<string>a&amp;b</string>' "$ORGANISM_LA/$NS.localjob.plist"
assert "\$HOOKS expanded in local args"             grep -q "<string>$REPO/claude/hooks/local/example-instance.py</string>" "$ORGANISM_LA/$NS.localjob.plist"
assert "unknown schedule skipped"        grep -q "unknown schedule 'daily:7:45'" <<<"$OUT"
assert "…and the existing plist left untouched"     grep -q 'SENTINEL-DO-NOT-TOUCH' "$ORGANISM_LA/$NS.badsched.plist"
assert "newline-less final line still installed" test -f "$ORGANISM_LA/$NS.lastline.plist"
assert "indented comment ignored"        test -z "$(find "$ORGANISM_LA" -name '*#*' -print 2>/dev/null)"

echo "orphan sweep"
assert "orphan reported under DRY"                  grep -q "orphan   $NS.stray" <<<"$OUT"
assert "…report-only: file still present"           test -f "$ORGANISM_LA/$NS.stray.plist"

echo "plist XML validity"
if command -v plutil >/dev/null 2>&1; then
  LINTFAIL=0
  for p in "$ORGANISM_LA/$NS."*.plist; do
    case "$p" in *badsched*|*stray*) continue ;; esac   # fixtures, not rendered output
    plutil -lint "$p" >/dev/null 2>&1 || { bad "plutil -lint $(basename "$p")"; LINTFAIL=1; }
  done
  [ "$LINTFAIL" -eq 0 ] && ok "plutil -lint clean on every rendered plist"
else
  echo "  - plutil not available (non-macOS): XML lint skipped"
fi

if [ "$FAILS" -eq 0 ]; then echo "ALL PASS"; exit 0; else echo "FAILED: $FAILS case(s)"; exit 1; fi
