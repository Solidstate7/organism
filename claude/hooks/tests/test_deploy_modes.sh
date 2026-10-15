#!/bin/bash
# tests for deploy.sh's install modes, run on a gitless copy of the tree in a temp HOME
# (a copy without .git, so deploy.sh can never link git hooks into this checkout):
#   symlink mode   links all five trees, renders hooks/hooks.json into settings.json with
#                  the copy's absolute paths, carries a real hooks dir's pairings into state
#   plugin mode    (install marker says plugin-inplace) drops the commands link, renders no
#                  Organism hooks, keeps the other links
#   cache copy     refuses to deploy from inside the plugin cache and links nothing
# Run:  bash claude/hooks/tests/test_deploy_modes.sh
set -u
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
FAILS=0
ok()  { echo "  ✓ $1"; }
bad() { echo "  ✗ $1"; FAILS=$((FAILS+1)); }
t()   { if eval "$2"; then ok "$1"; else bad "$1"; fi; }

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
copy_tree() {  # copy_tree DEST: tracked + new files, symlinks kept, no .git
  mkdir -p "$1"
  (cd "$ROOT" && git ls-files -z --cached --others --exclude-standard \
     | while IFS= read -r -d '' f; do [ -e "$f" ] || [ -L "$f" ] && printf '%s\0' "$f"; done \
     | tar --null -T - -cf -) | tar -xf - -C "$1"
}
organism_cmds() {  # organism_cmds FILE: Organism hook commands in a settings file
  python3 - "$1" <<'PY'
import json, re, sys
s = json.load(open(sys.argv[1]))
for groups in (s.get("hooks") or {}).values():
    for g in groups:
        for h in g.get("hooks", []):
            line = " ".join([h.get("command", "")] + h.get("args", []))
            if re.search(r"claude/hooks/(guard-publish\.sh|inject-handoff\.sh|obsidian-mirror\.py|session-end-handoff\.py)", line):
                print(line)
PY
}

echo "deploy: symlink mode"
R="$TMP/clone"; copy_tree "$R"
H="$TMP/home1"; mkdir -p "$H/.claude/hooks"
echo '{"pairs":[]}' > "$H/.claude/hooks/reconcile-pairings.json"
out=$(HOME="$H" CLAUDE_CODE_PLUGIN_CACHE_DIR="$H/plugins" bash "$R/deploy.sh" 2>&1); rc=$?
t "deploy exits 0" '[ $rc -eq 0 ]'
for item in hooks commands skills keybindings.json git-hooks; do
  [ -e "$R/claude/$item" ] || continue
  t "~/.claude/$item links into the clone" '[ "$(readlink "$H/.claude/$item")" = "$R/claude/$item" ]'
done
t "the /handoff command resolves through the links" 'grep -q "^name: handoff" "$H/.claude/commands/handoff.md"'
t "pairings were carried into state" '[ -f "$H/.claude/state/reconcile-pairings.json" ]'
t "the real hooks dir was backed up, not deleted" '[ -f "$H/.claude/hooks.pre-organism.bak/reconcile-pairings.json" ]'
n=$(organism_cmds "$H/.claude/settings.json" | wc -l | tr -d ' ')
t "settings.json runs the six hooks from hooks.json ($n)" '[ "$n" = 6 ]'
t "...by absolute path into the clone" '[ -z "$(organism_cmds "$H/.claude/settings.json" | grep -v "$(cd "$R" && pwd -P)/claude/hooks/")" ]'
t "...and every script exists" 'organism_cmds "$H/.claude/settings.json" | awk "{print \$2}" | while read -r p; do [ -f "$p" ] || exit 1; done'
t "it says the self-hooks were skipped (no .git)" 'printf "%s" "$out" | grep -q "not a git work tree"'
guard_cmd=$(organism_cmds "$H/.claude/settings.json" | grep guard-publish | head -1)
t "the rendered guard runs through sh and allows a harmless command" \
  'echo "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"ls\"}}" | HOME="$H" sh -c "$guard_cmd"'
cp "$H/.claude/settings.json" "$TMP/s1.json"
HOME="$H" CLAUDE_CODE_PLUGIN_CACHE_DIR="$H/plugins" bash "$R/deploy.sh" >/dev/null 2>&1
t "a second deploy changes nothing" 'cmp -s "$TMP/s1.json" "$H/.claude/settings.json"'

echo "deploy: links that are already there"
H="$TMP/home-links"; mkdir -p "$H/.claude" "$H/dotfiles" "$TMP/old-clone/claude/hooks"
echo '[]' > "$H/dotfiles/kb.json"
ln -s "$H/dotfiles/kb.json" "$H/.claude/keybindings.json"
ln -s "$TMP/old-clone/claude/hooks" "$H/.claude/hooks"
HOME="$H" CLAUDE_CODE_PLUGIN_CACHE_DIR="$H/plugins" bash "$R/deploy.sh" >/dev/null 2>&1
t "the user's own keybindings link is backed up, not deleted" \
  '[ "$(readlink "$H/.claude/keybindings.json.pre-organism.bak")" = "$H/dotfiles/kb.json" ]'
t "...and replaced by the clone's file" '[ "$(readlink "$H/.claude/keybindings.json")" = "$R/claude/keybindings.json" ]'
t "a link into another Organism clone is replaced with no backup" \
  '[ "$(readlink "$H/.claude/hooks")" = "$R/claude/hooks" ] && [ ! -e "$H/.claude/hooks.pre-organism.bak" ] && [ ! -L "$H/.claude/hooks.pre-organism.bak" ]'

echo "deploy: plugin mode"
printf '{"mode":"plugin-inplace","root":"%s"}\n' "$R" > "$H/.claude/state/organism-install.json"
out=$(HOME="$H" CLAUDE_CODE_PLUGIN_CACHE_DIR="$H/plugins" bash "$R/deploy.sh" 2>&1); rc=$?
t "deploy exits 0" '[ $rc -eq 0 ]'
t "the commands link is gone" '[ ! -e "$H/.claude/commands" ] && [ ! -L "$H/.claude/commands" ]'
t "the hooks link stays" '[ "$(readlink "$H/.claude/hooks")" = "$R/claude/hooks" ]'
t "the skills link stays" '[ "$(readlink "$H/.claude/skills")" = "$R/claude/skills" ] || [ ! -e "$R/claude/skills" ]'
t "settings.json has no Organism hooks" '[ -z "$(organism_cmds "$H/.claude/settings.json")" ]'
rm -f "$H/.claude/state/organism-install.json"
HOME="$H" CLAUDE_CODE_PLUGIN_CACHE_DIR="$H/plugins" bash "$R/deploy.sh" >/dev/null 2>&1
t "back in symlink mode the commands link and hooks return" \
  '[ -L "$H/.claude/commands" ] && [ "$(organism_cmds "$H/.claude/settings.json" | wc -l | tr -d " ")" = 6 ]'

echo "deploy: from the plugin cache"
H="$TMP/home2"; mkdir -p "$H"
C="$H/plugins/cache/organism/organism/0.1.0"; copy_tree "$C"
out=$(HOME="$H" CLAUDE_CODE_PLUGIN_CACHE_DIR="$H/plugins" bash "$C/deploy.sh" 2>&1); rc=$?
t "refuses (exit 1)" '[ $rc -ne 0 ]'
t "links nothing" '[ ! -e "$H/.claude/hooks" ] && [ ! -e "$H/.claude/settings.json" ]'
t "says why" 'printf "%s" "$out" | grep -q "plugin cache"'

[ "$FAILS" -eq 0 ] && { echo "ALL PASS"; exit 0; }
echo "FAIL: $FAILS check(s)"; exit 1
