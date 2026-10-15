#!/bin/bash
# tests for the launch-safety guards. Nothing here may reach a real launchd: a PATH shim
# stands in for launchctl and only logs, and every case uses a temp HOME.
#   install-agents.sh / uninstall-agents.sh with a HOME that is not the account's home
#     and no ORGANISM_DRY: forced dry, the shim is never called
#   install-agents.sh from a copy inside the plugin cache: refuses
#   self-converge.sh fired in a repo that is not the deployed clone (a worktree, a
#     second clone): does not converge; in the deployed clone: converges
# Run:  bash claude/hooks/tests/test_launch_safety.sh
set -u
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
FAILS=0
ok()  { echo "  ✓ $1"; }
bad() { echo "  ✗ $1"; FAILS=$((FAILS+1)); }
t()   { if eval "$2"; then ok "$1"; else bad "$1"; fi; }

TMP="$(cd "$(mktemp -d)" && pwd -P)"; trap 'rm -rf "$TMP"' EXIT
SHIM="$TMP/shim"; mkdir -p "$SHIM"
LOG="$TMP/launchctl.log"; : > "$LOG"
for tool in launchctl osascript; do
  printf '#!/bin/sh\necho "%s $*" >> "%s"\n' "$tool" "$LOG" > "$SHIM/$tool"; chmod +x "$SHIM/$tool"
done
clean_env() { env -u ORGANISM_DRY -u ORGANISM_LA -u ORGANISM_LAUNCHD_NS -u ORGANISM_REPO "$@"; }
copy_tree() {  # copy_tree DEST: tracked + new files, symlinks kept, no .git
  mkdir -p "$1"
  (cd "$ROOT" && git ls-files -z --cached --others --exclude-standard \
     | while IFS= read -r -d '' f; do [ -e "$f" ] || [ -L "$f" ] && printf '%s\0' "$f"; done \
     | tar --null -T - -cf -) | tar -xf - -C "$1"
}

echo "launch safety: a sandbox HOME never reaches launchd"
H="$TMP/home"; mkdir -p "$H/.claude/state"
echo '{"roles":{}}' > "$TMP/host.json"
out=$(clean_env HOME="$H" PATH="$SHIM:$PATH" ORGANISM_HOOKS="$ROOT/claude/hooks" \
      ORGANISM_HOST_JSON="$TMP/host.json" CLAUDE_HOST_JSON="$TMP/host.json" \
      ORGANISM_LOCAL_AGENTS="$TMP/none" bash "$ROOT/launchd/install-agents.sh" 2>&1)
t "install-agents says it is forcing dry" 'printf "%s" "$out" | grep -q "not this account'"'"'s home"'
t "install-agents renders (dry)" 'printf "%s" "$out" | grep -q "\[dry\]"'
t "install-agents never calls launchctl" '[ ! -s "$LOG" ]'
mkdir -p "$H/Library/LaunchAgents"; touch "$H/Library/LaunchAgents/com.organism.fake.plist"
out=$(clean_env HOME="$H" PATH="$SHIM:$PATH" bash "$ROOT/launchd/uninstall-agents.sh" 2>&1)
t "uninstall-agents says it is forcing dry" 'printf "%s" "$out" | grep -q "not this account'"'"'s home"'
t "uninstall-agents never calls launchctl" '[ ! -s "$LOG" ]'
t "uninstall-agents leaves the plist" '[ -f "$H/Library/LaunchAgents/com.organism.fake.plist" ]'

echo "launch safety: no launchd jobs from the plugin cache"
C="$TMP/plugins/cache/organism/organism/0.1.0"; copy_tree "$C"
out=$(clean_env HOME="$H" PATH="$SHIM:$PATH" ORGANISM_DRY=1 CLAUDE_CODE_PLUGIN_CACHE_DIR="$TMP/plugins" \
      ORGANISM_HOOKS="$C/claude/hooks" bash "$C/launchd/install-agents.sh" 2>&1); rc=$?
t "install-agents refuses (exit 1)" '[ $rc -ne 0 ]'
t "...and says why" 'printf "%s" "$out" | grep -q "plugin cache"'
out=$(clean_env HOME="$H" CLAUDE_CODE_PLUGIN_CACHE_DIR="$TMP/plugins" bash "$C/launchd/install-agents.sh" --list 2>&1); rc=$?
t "--list still works there (read-only)" '[ $rc -eq 0 ]'

echo "launch safety: only the deployed clone self-converges"
mkrepo() {  # mkrepo DIR: a minimal repo whose converge.sh only records that it ran
  mkdir -p "$1/claude/hooks/lib" "$1/git-hooks"
  cp "$ROOT/claude/hooks/lib/where.py" "$1/claude/hooks/lib/"
  cp "$ROOT/git-hooks/self-converge.sh" "$1/git-hooks/"
  ln -s self-converge.sh "$1/git-hooks/post-merge"
  printf '#!/bin/sh\necho converged >> "%s/converged"\n' "$1" > "$1/converge.sh"; chmod +x "$1/converge.sh"
  git -c init.defaultBranch=main init -q "$1"
}
A="$TMP/deployed"; B="$TMP/other"; mkrepo "$A"; mkrepo "$B"
H2="$TMP/home2"; mkdir -p "$H2/.claude"; ln -s "$A/claude/hooks" "$H2/.claude/hooks"
(cd "$B" && HOME="$H2" bash "$B/git-hooks/post-merge" >/dev/null 2>&1)
t "a second clone does not converge" '[ ! -e "$B/converged" ]'
(cd "$A" && HOME="$H2" bash "$A/git-hooks/post-merge" >/dev/null 2>&1)
t "the deployed clone converges" '[ -e "$A/converged" ]'
git -C "$A" -c user.email=t@t -c user.name=t commit -q --allow-empty -m init
git -C "$A" worktree add -q "$TMP/wt" 2>/dev/null
cp "$A/converge.sh" "$TMP/wt/converge.sh" 2>/dev/null; mkdir -p "$TMP/wt/claude/hooks/lib" "$TMP/wt/git-hooks"
cp "$A/claude/hooks/lib/where.py" "$TMP/wt/claude/hooks/lib/"; cp "$A/git-hooks/self-converge.sh" "$TMP/wt/git-hooks/post-merge"
(cd "$TMP/wt" && HOME="$H2" bash "$TMP/wt/git-hooks/post-merge" >/dev/null 2>&1)
t "a worktree of the deployed clone does not converge" '[ "$(wc -l < "$A/converged" | tr -d " ")" = 1 ]'
printf '{"mode":"plugin-inplace","root":"%s"}\n' "$B" > "$TMP/marker.json"
mkdir -p "$H2/.claude/state"; cp "$TMP/marker.json" "$H2/.claude/state/organism-install.json"
(cd "$B" && HOME="$H2" bash "$B/git-hooks/post-merge" >/dev/null 2>&1)
t "a clone the install marker names converges" '[ -e "$B/converged" ]'
t "no launchctl call in any case" '[ ! -s "$LOG" ]'

[ "$FAILS" -eq 0 ] && { echo "ALL PASS"; exit 0; }
echo "FAIL: $FAILS check(s)"; exit 1
