#!/bin/bash
# tests for `organism uninstall`, one case per install mode, in a temp HOME with a fake
# `claude` (fixtures/fake_claude.py) and a launchctl shim that only logs:
#   symlink   the plan changes nothing; --yes removes the hooks/commands/git-hooks links
#             (moving deploy.sh's backup back), copies skills and keybindings as real files,
#             removes the repo's own git hooks, unsets core.hooksPath only if it is ours,
#             removes the CLI link; state kept, the clone untouched
#   in-place  the plugin and marketplace are removed, and a later deploy does not bring
#             the plugin back (no organism@organism, no organism marketplace)
#   copied    only the plugin goes; the marketplace stays without --remove-marketplace
#   purge     --purge-state deletes Organism's own state files by name, nothing else
# Run:  bash claude/hooks/tests/test_uninstall.sh
set -u
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
FAILS=0
ok()  { echo "  ✓ $1"; }
bad() { echo "  ✗ $1"; FAILS=$((FAILS+1)); }
t()   { if eval "$2"; then ok "$1"; else bad "$1"; fi; }

TMP="$(cd "$(mktemp -d)" && pwd -P)"; trap 'rm -rf "$TMP"' EXIT
copy_tree() {  # copy_tree DEST: tracked + new files, symlinks kept, no .git
  mkdir -p "$1"
  (cd "$ROOT" && git ls-files -z --cached --others --exclude-standard \
     | while IFS= read -r -d '' f; do [ -e "$f" ] || [ -L "$f" ] && printf '%s\0' "$f"; done \
     | tar --null -T - -cf -) | tar -xf - -C "$1"
}
SHIM="$TMP/shim"; mkdir -p "$SHIM"
printf '#!/bin/sh\nexec python3 "%s" "$@"\n' "$ROOT/claude/hooks/tests/fixtures/fake_claude.py" > "$SHIM/claude"
printf '#!/bin/sh\necho "launchctl $*" >> "%s/launchctl.log"\nexit 0\n' "$TMP" > "$SHIM/launchctl"
printf '#!/bin/sh\necho "osascript $*" >> "%s/launchctl.log"\n' "$TMP" > "$SHIM/osascript"
printf '#!/bin/sh\nexit 0\n' > "$SHIM/ps"
chmod +x "$SHIM"/*
run() { env -u RECONCILE_CONFIG -u ORGANISM_STATE_DIR -u GIT_CONFIG_GLOBAL HOME="$H" PATH="$SHIM:$PATH" \
          ORGANISM_DRY=1 CLAUDE_CODE_PLUGIN_CACHE_DIR="$H/.claude/plugins" "$@"; }
tree_hash() {  # tree_hash DIR: every file and link with its size, mtime and target (dirs left out)
  python3 - "$1" <<'PY'
import hashlib, os, sys
h = hashlib.sha256()
for d, dirs, files in os.walk(sys.argv[1]):
    dirs[:] = sorted(x for x in dirs if x not in (".git", ".fake-claude", "__pycache__") or os.path.islink(os.path.join(d, x)))
    for n in sorted(files) + sorted(x for x in os.listdir(d) if os.path.islink(os.path.join(d, x)) and x not in files):
        p = os.path.join(d, n); st = os.lstat(p)
        h.update(f"{p}|{st.st_size}|{st.st_mtime_ns}|{os.readlink(p) if os.path.islink(p) else ''}\n".encode())
print(h.hexdigest())
PY
}
plugin_keys() { python3 -c '
import json, sys
s = json.load(open(sys.argv[1]))
print("organism@organism" in (s.get("enabledPlugins") or {}), "organism" in (s.get("extraKnownMarketplaces") or {}))' "$1"; }
has_ours() { grep -qE "claude/hooks/(guard-publish|inject-handoff|obsidian-mirror|session-end-handoff)" "$1"; }

echo "uninstall: symlink mode"
R="$TMP/clone"; copy_tree "$R"; git -C "$R" init -q
mkdir -p "$R/claude/skills/my-skill" && echo "mine" > "$R/claude/skills/my-skill/SKILL.md"
H="$TMP/home1"; mkdir -p "$H/.claude/hooks" "$H/.local/bin" "$H/Library/LaunchAgents"
echo "old hook" > "$H/.claude/hooks/my-old-hook.sh"
touch "$H/Library/LaunchAgents/com.organism.converge.plist"
run bash "$R/deploy.sh" >/dev/null 2>&1
ln -sf "$R/bin/organism" "$H/.local/bin/organism"
run git config --global core.hooksPath "$H/.claude/git-hooks"
t "deploy linked the repo's own git hooks" '[ -L "$R/.git/hooks/post-merge" ]'
hh=$(tree_hash "$H"); rh=$(tree_hash "$R")
out=$(run python3 "$R/claude/cli/uninstall.py" 2>&1); rc=$?
t "the plan exits 0" '[ $rc -eq 0 ]'
t "the plan changes nothing" '[ "$(tree_hash "$H")" = "$hh" ] && [ -L "$R/.git/hooks/post-merge" ]'
t "the plan names the job" 'printf "%s" "$out" | grep -q "com.organism.converge"'
t "the plan names the backup it will move back" 'printf "%s" "$out" | grep -q "move back hooks.pre-organism.bak"'
t "the plan warns about importers of hooks/lib" 'printf "%s" "$out" | grep -q "imports ~/.claude/hooks/lib"'
out=$(run python3 "$R/claude/cli/uninstall.py" --yes 2>&1); rc=$?
t "--yes exits 0" '[ $rc -eq 0 ]' || printf '%s\n' "$out"
t "the real hooks dir is back from its backup" '[ -d "$H/.claude/hooks" ] && [ ! -L "$H/.claude/hooks" ] && [ -f "$H/.claude/hooks/my-old-hook.sh" ]'
t "the commands and git-hooks links are gone" '[ ! -e "$H/.claude/commands" ] && [ ! -L "$H/.claude/commands" ] && [ ! -L "$H/.claude/git-hooks" ]'
t "skills is now a real dir with the personal skill" '[ -d "$H/.claude/skills" ] && [ ! -L "$H/.claude/skills" ] && [ -f "$H/.claude/skills/my-skill/SKILL.md" ]'
t "keybindings.json is now a real file" '[ -f "$H/.claude/keybindings.json" ] && [ ! -L "$H/.claude/keybindings.json" ]'
t "the repo's own git hooks are gone" '[ ! -e "$R/.git/hooks/post-merge" ] && [ ! -e "$R/.git/hooks/post-checkout" ]'
t "core.hooksPath is unset" '! run git config --global --get core.hooksPath >/dev/null'
t "the CLI link is gone" '[ ! -e "$H/.local/bin/organism" ]'
t "settings.json runs no Organism hooks" '! has_ours "$H/.claude/settings.json"'
t "state is kept" '[ -f "$H/.claude/state/paths.env" ] || [ -f "$H/.claude/state/settings.generated.json" ]'
t "the clone is untouched (bytecode caches aside)" '[ "$(tree_hash "$R")" = "$rh" ]'
t "launchd was never called (sandbox HOME forces a dry run)" '! grep -qE "launchctl (load|unload)" "$TMP/launchctl.log" 2>/dev/null'

echo "uninstall: core.hooksPath that is not ours"
H="$TMP/home2"; mkdir -p "$H"
run bash "$R/deploy.sh" >/dev/null 2>&1
run git config --global core.hooksPath "$TMP/elsewhere"
out=$(run python3 "$R/claude/cli/uninstall.py" --yes 2>&1)
t "is left alone" '[ "$(run git config --global --get core.hooksPath)" = "$TMP/elsewhere" ]'
t "...and says so" 'printf "%s" "$out" | grep -q "not Organism"'

echo "uninstall: plugin in place"
H="$TMP/home3"; mkdir -p "$H/.claude/state"
run bash "$R/deploy.sh" >/dev/null 2>&1
out=$(run python3 "$R/claude/cli/migrate.py" --to-plugin --skip-tests --keep-pairings 2>&1)
t "migrated to plugin-inplace" 'grep -q plugin-inplace "$H/.claude/state/organism-install.json"' || printf '%s\n' "$out"
out=$(run python3 "$R/claude/cli/uninstall.py" --yes 2>&1); rc=$?
t "--yes exits 0" '[ $rc -eq 0 ]' || printf '%s\n' "$out"
t "the plugin and marketplace were removed" 'grep -q "plugin uninstall organism@organism" "$H/.fake-claude/calls.log" && grep -q "plugin marketplace remove organism" "$H/.fake-claude/calls.log"'
t "settings.json keeps no plugin keys" '[ "$(plugin_keys "$H/.claude/settings.json")" = "False False" ]'
t "...nor does the overlay" '[ ! -f "$H/.claude/state/settings.local.json" ] || [ "$(plugin_keys "$H/.claude/state/settings.local.json")" = "False False" ]'
t "the install record is gone" '[ ! -e "$H/.claude/state/organism-install.json" ]'
run bash "$R/deploy.sh" >/dev/null 2>&1
t "a later deploy does not bring the plugin back" '[ "$(plugin_keys "$H/.claude/settings.json")" = "False False" ]'

echo "uninstall: copied plugin"
H="$TMP/home4"; mkdir -p "$H/.fake-claude"
C="$H/.claude/plugins/cache/organism/organism/0.1.0"; copy_tree "$C"
printf '{"marketplaces":{"organism":"%s"},"plugins":{"organism@organism":{"enabled":true,"folder":"%s"}}}\n' "$TMP/gh" "$C" > "$H/.fake-claude/state.json"
out=$(run python3 "$C/claude/cli/uninstall.py" 2>&1)
t "the mode is plugin-copied" 'printf "%s" "$out" | grep -q "install mode plugin-copied"'
t "the plan has no launchd or link steps" '! printf "%s" "$out" | grep -qE "background jobs|links in"'
t "the marketplace stays without --remove-marketplace" 'printf "%s" "$out" | grep -q "marketplace stays"'
out=$(run python3 "$C/claude/cli/uninstall.py" --yes 2>&1); rc=$?
t "--yes exits 0" '[ $rc -eq 0 ]' || printf '%s\n' "$out"
t "the plugin was uninstalled, the marketplace kept" 'grep -q "plugin uninstall organism@organism" "$H/.fake-claude/calls.log" && ! grep -q "marketplace remove" "$H/.fake-claude/calls.log"'
t "the cache copy is left to Claude Code" '[ -f "$C/bin/organism" ]'
out=$(run sh "$C/bin/organism" start 2>&1); rc=$?
out2=$(run sh "$C/bin/organism" status 2>&1)
t "organism status says a copied install has no jobs" 'printf "%s" "$out2" | grep -q "no background jobs"'
t "organism start refuses in a copied install" '[ $rc -ne 0 ] && printf "%s" "$out" | grep -q "not part of the plugin"'
out=$(run sh "$C/bin/organism" help 2>&1)
t "organism help in a copied install names no clone-only verb" '! printf "%s" "$out" | grep -qE "organism (start|stop|update|migrate|addon|status)"'

echo "uninstall: --purge-state"
H="$TMP/home5"; mkdir -p "$H/.claude/state"
run bash "$R/deploy.sh" >/dev/null 2>&1
S="$H/.claude/state"
for f in paths.env host.json incognito.json reconcile-state.json converge-last-success entrypoint-census.json; do echo x > "$S/$f"; done
echo keep > "$S/my-notes.txt"; echo '{}' > "$S/telegram-alerts.json"
mkdir -p "$S/locks"; : > "$S/locks/session-end-abc.lock"
out=$(run python3 "$R/claude/cli/uninstall.py" --yes --purge-state 2>&1); rc=$?
t "--purge-state exits 0" '[ $rc -eq 0 ]' || printf '%s\n' "$out"
t "Organism's state files are gone" '[ ! -e "$S/paths.env" ] && [ ! -e "$S/host.json" ] && [ ! -e "$S/reconcile-state.json" ] && [ ! -e "$S/converge-last-success" ] && [ ! -e "$S/settings.generated.json" ]'
t "other files stay" '[ -f "$S/my-notes.txt" ] && [ -f "$S/telegram-alerts.json" ]'
t "SessionEnd's lock files and their folder are gone" '[ ! -e "$S/locks" ]'

echo "uninstall: from a clone that is not the deployed one"
H="$TMP/home6"; mkdir -p "$H/.claude/state"
run bash "$R/deploy.sh" >/dev/null 2>&1
R2="$TMP/clone2"; copy_tree "$R2"
cp "$H/.claude/settings.json" "$TMP/s6.json"
out=$(run python3 "$R2/claude/cli/uninstall.py" --yes --purge-state 2>&1); rc=$?
t "refuses (exit 1) and names the deployed clone" '[ $rc -ne 0 ] && printf "%s" "$out" | grep -q "runs Organism from $R"'
t "...and changes nothing" 'cmp -s "$TMP/s6.json" "$H/.claude/settings.json" && [ -L "$H/.claude/hooks" ]'
out=$(run python3 "$R2/claude/cli/migrate.py" --to-plugin --skip-tests --yes 2>&1); rc=$?
t "migrate from that clone refuses too" '[ $rc -ne 0 ] && [ "$(readlink "$H/.claude/hooks")" = "$R/claude/hooks" ]'

echo "uninstall: after the deployed clone was moved"
H="$TMP/home8"; mkdir -p "$H/.claude/state"
R3="$TMP/clone3"; copy_tree "$R3"
run bash "$R3/deploy.sh" >/dev/null 2>&1
R4="$TMP/clone4"; mv "$R3" "$R4"
out=$(run python3 "$R4/claude/cli/uninstall.py" --yes 2>&1); rc=$?
t "the moved clone does not send you to the old path" '! printf "%s" "$out" | grep -q "runs Organism from"'
t "...and the dangling links are removed" '[ $rc -eq 0 ] && [ ! -L "$H/.claude/hooks" ] && [ ! -L "$H/.claude/commands" ] && [ ! -L "$H/.claude/keybindings.json" ] && [ ! -L "$H/.local/bin/organism" ]' || printf '%s\n' "$out"

echo "uninstall: a failed plugin uninstall can be retried"
H="$TMP/home7"; mkdir -p "$H/.claude/state"
run bash "$R/deploy.sh" >/dev/null 2>&1
run python3 "$R/claude/cli/migrate.py" --to-plugin --skip-tests --keep-pairings >/dev/null 2>&1
out=$(FAKE_CLAUDE_FAIL="plugin uninstall" run python3 "$R/claude/cli/uninstall.py" --yes 2>&1); rc=$?
t "the failed run exits 1" '[ $rc -ne 0 ]'
t "...and keeps the install record" '[ -f "$H/.claude/state/organism-install.json" ]'
out=$(run python3 "$R/claude/cli/uninstall.py" --yes 2>&1); rc=$?
t "the retry removes the plugin" '[ $rc -eq 0 ] && grep -q "\"plugins\": {}" "$H/.fake-claude/state.json"' || printf '%s\n' "$out"
H="$TMP/home7b"; mkdir -p "$H/.claude/state"
run bash "$R/deploy.sh" >/dev/null 2>&1
run python3 "$R/claude/cli/migrate.py" --to-plugin --skip-tests --keep-pairings >/dev/null 2>&1
out=$(FAKE_CLAUDE_FAIL="plugin uninstall" run python3 "$R/claude/cli/uninstall.py" --yes --purge-state 2>&1); rc=$?
t "with --purge-state, a failed run still keeps the install record" '[ $rc -ne 0 ] && [ -f "$H/.claude/state/organism-install.json" ]'

echo "plugin install: an organism marketplace from somewhere else"
H="$TMP/home8"; mkdir -p "$H/.fake-claude"
printf '{"marketplaces":{"organism":"%s"},"plugins":{}}\n' "$TMP/elsewhere" > "$H/.fake-claude/state.json"
out=$(run python3 "$R/claude/cli/migrate.py" --install-plugin 2>&1); rc=$?
t "refuses to install from it" '[ $rc -ne 0 ] && printf "%s" "$out" | grep -q "different marketplace" && ! grep -q "plugin install" "$H/.fake-claude/calls.log"'

echo "small things"
out=$(run sh "$R/bin/organism" bogus 2>&1); rc=$?
t "an unknown organism verb exits 2" '[ $rc -eq 2 ] && printf "%s" "$out" | grep -q "unknown command"'
H="$TMP/home9"; mkdir -p "$H/.claude"
run python3 "$R/claude/hooks/lib/settingsmerge.py" "$R/claude/settings.json" >/dev/null 2>&1; rc=$?
t "settingsmerge with no state folder writes both files" '[ $rc -eq 0 ] && [ -f "$H/.claude/state/settings.generated.json" ]'

[ "$FAILS" -eq 0 ] && { echo "ALL PASS"; exit 0; }
echo "FAIL: $FAILS check(s)"; exit 1
