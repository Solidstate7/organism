#!/bin/bash
# tests for `organism migrate`, on a copy of the tree in a temp HOME with a fake `claude`
# (fixtures/fake_claude.py), a fake `ps` and a launchctl shim that only logs:
#   --dry-run changes nothing; the preflight refuses while an interactive claude runs
#   stop after step N and resume; the pairings step stops for --yes and shows the diff
#   forward: the plugin is on before the settings hooks go; another tool's hooks survive;
#     the rewritten harness probes print what the old ones printed
#   forward twice is a no-op; a later deploy brings no Organism hooks back
#   --to-symlink: settings.json key-for-key equal to the start, pairings moved back
# Run:  bash claude/hooks/tests/test_migrate.sh
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
R="$TMP/clone"; copy_tree "$R"
H="$TMP/home"; mkdir -p "$H/.claude/state"
SHIM="$TMP/shim"; mkdir -p "$SHIM"
printf '#!/bin/sh\nexec python3 "%s" "$@"\n' "$ROOT/claude/hooks/tests/fixtures/fake_claude.py" > "$SHIM/claude"
printf '#!/bin/sh\necho "launchctl $*" >> "%s/launchctl.log"\n[ "$1" = list ] && printf "123\\t0\\tcom.organism.converge\\n"\nexit 0\n' "$TMP" > "$SHIM/launchctl"
printf '#!/bin/sh\necho "osascript $*" >> "%s/launchctl.log"\n' "$TMP" > "$SHIM/osascript"
printf '#!/bin/sh\n[ -n "${FAKE_PS:-}" ] && printf "%%s\\n" "$FAKE_PS"\nexit 0\n' > "$SHIM/ps"
chmod +x "$SHIM"/*
run() { env -u RECONCILE_CONFIG -u ORGANISM_STATE_DIR HOME="$H" PATH="$SHIM:$PATH" ORGANISM_DRY=1 \
          CLAUDE_CODE_PLUGIN_CACHE_DIR="$H/.claude/plugins" "$@"; }
migrate() { run python3 "$R/claude/cli/migrate.py" "$@"; }
organism_cmds() { python3 -c '
import json, re, sys
s = json.load(open(sys.argv[1]))
for groups in (s.get("hooks") or {}).values():
    for g in groups:
        for h in g.get("hooks", []):
            line = " ".join([h.get("command", "")] + h.get("args", []))
            if re.search(r"claude/hooks/(guard-publish\.sh|inject-handoff\.sh|obsidian-mirror\.py|session-end-handoff\.py)", line):
                print(line)' "$1"; }
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
harness_value() {  # harness_value PROBE|GUARD NAME: run it from ~/.claude as the doc check does
  python3 - "$H/.claude/state/reconcile-pairings.json" "$1" "$2" <<'PY' | (cd "$H/.claude" && HOME="$H" sh)
import json, sys
p = [x for x in json.load(open(sys.argv[1]))["pairs"] if x["id"] == "harness"][0]
print(p["probes"][sys.argv[3]]["cmd"] if sys.argv[2] == "probe" else p["guards"][sys.argv[3]] + " && echo yes || echo no")
PY
}

# a symlink install whose overlay looks like a real one: Organism's guard twice and its
# mirror once (an old drift fold copied them there), beside another tool's hooks
cat > "$H/.claude/state/settings.local.json" <<EOF
{
  "model": "opus",
  "hooks": {
    "PreToolUse": [
      {"matcher": "Bash", "hooks": [{"type": "command", "command": "sh $H/.claude/hooks/guard-publish.sh"}]},
      {"matcher": "mcp__.*", "hooks": [{"type": "command", "command": "sh $H/.claude/hooks/guard-publish.sh"}]},
      {"matcher": "Bash", "hooks": [{"type": "command", "command": "other-tool pre-bash"}]}
    ],
    "Stop": [
      {"hooks": [{"type": "command", "command": "python3 $H/.claude/hooks/obsidian-mirror.py"},
                 {"type": "command", "command": "other-tool on-stop"}]}
    ]
  }
}
EOF
sed -e "s#__HOME__#$H#g" -e "s#__VAULT_OPS__#$TMP/vault#g" -e "s#__NAMESPACE__#organism#g" \
    -e "s#__CODE_ROOT__#$TMP#g" "$ROOT/config/reconcile-pairings.example.json" > "$H/.claude/state/reconcile-pairings.json"
run bash "$R/deploy.sh" >/dev/null 2>&1
cp "$H/.claude/settings.json" "$TMP/start.json"
cp "$H/.claude/state/reconcile-pairings.json" "$TMP/pairings-start.json"
old_cmds=$(harness_value probe commands); old_keys=$(harness_value probe settings_hooks)
t "the symlink install runs Organism's hooks from settings.json" '[ "$(organism_cmds "$H/.claude/settings.json" | wc -l | tr -d " ")" = 6 ]'

echo "migrate: dry run and preflight"
mkdir -p "$H/.fake-claude"; h0=$(tree_hash "$H")
out=$(migrate --to-plugin --dry-run --skip-tests 2>&1); rc=$?
t "dry run exits 0" '[ $rc -eq 0 ]'
t "dry run changes nothing in HOME" '[ "$(tree_hash "$H")" = "$h0" ]'
t "dry run shows the pairings diff" 'printf "%s" "$out" | grep -q "plugin mode"'
t "dry run says what deploy would drop" 'printf "%s" "$out" | grep -q "drop 6 Organism hook entries"'
out=$(FAKE_PS="4242 /usr/local/bin/claude --resume" migrate --to-plugin --skip-tests 2>&1); rc=$?
t "refuses while an interactive claude runs" '[ $rc -ne 0 ] && printf "%s" "$out" | grep -q "interactive claude sessions"'
t "...before snapshotting anything" '[ -z "$(ls -d "$H"/.claude/state/pre-plugin-* 2>/dev/null)" ]'
out=$(FAKE_PS="4243 claude -p hello" migrate --to-plugin --dry-run --skip-tests 2>&1); rc=$?
t "a claude -p process does not count" '[ $rc -eq 0 ]'
out=$(FAKE_CLAUDE_VERSION=2.1.100 migrate --to-plugin --skip-tests 2>&1); rc=$?
t "refuses an old Claude Code" '[ $rc -ne 0 ] && printf "%s" "$out" | grep -q "or newer is needed"'

echo "migrate: stop after a step, then resume"
out=$(ORGANISM_MIGRATE_STOP_AFTER=2 migrate --to-plugin --skip-tests 2>&1); rc=$?
t "stops after step 2" '[ $rc -eq 0 ] && printf "%s" "$out" | grep -q "stopped after step 2"'
snap=$(ls -d "$H"/.claude/state/pre-plugin-* 2>/dev/null | head -1)
t "the snapshot holds the three settings files" '[ -f "$snap/settings.json" ] && [ -f "$snap/settings.local.json" ] && [ -f "$snap/settings.generated.json" ]'
t "...and the pairings and the facts" '[ -f "$snap/reconcile-pairings.json" ] && grep -q "\"links\"" "$snap/snapshot.json"'
t "the checkpoint lists both steps" 'grep -q "\"snapshot\"" "$H/.claude/state/organism-install.json"'
t "settings unchanged so far" 'cmp -s "$TMP/start.json" "$H/.claude/settings.json"'
out=$(migrate --to-plugin 2>&1); rc=$?
t "resumes at step 3 (preflight not rerun)" 'printf "%s" "$out" | grep -q "1 preflight: done earlier"'
t "the pairings step stops without --yes (exit 3)" '[ $rc -eq 3 ]'
t "...shows the diff" 'printf "%s" "$out" | grep -q "^ *+.*skills"'
t "...after the plugin is already on" 'grep -q "plugin install organism@organism" "$H/.fake-claude/calls.log"'
t "...and leaves the file alone" 'cmp -s "$TMP/pairings-start.json" "$H/.claude/state/reconcile-pairings.json"'
out=$(ORGANISM_MIGRATE_STOP_AFTER=4 migrate --to-plugin --yes 2>&1); rc=$?
t "stops after step 4" '[ $rc -eq 0 ] && printf "%s" "$out" | grep -q "stopped after step 4"'
t "the plugin was installed from the clone" 'grep -q "plugin marketplace add $R" "$H/.fake-claude/calls.log" && grep -q "plugin install organism@organism" "$H/.fake-claude/calls.log"'
t "the overlay pins the plugin" 'python3 -c "import json,sys;o=json.load(open(sys.argv[1]));sys.exit(0 if o[\"enabledPlugins\"][\"organism@organism\"] is True and o[\"extraKnownMarketplaces\"][\"organism\"][\"source\"][\"path\"] else 1)" "$H/.claude/state/settings.local.json"'
t "the settings hooks are still on (plugin first, settings off later)" '[ "$(organism_cmds "$H/.claude/settings.json" | wc -l | tr -d " ")" = 6 ]'
t "the pairings were rewritten, with a backup" '[ -f "$H/.claude/state/reconcile-pairings.json.pre-plugin.bak" ] && grep -q "hooks.json" "$H/.claude/state/reconcile-pairings.json"'
out=$(migrate --to-plugin 2>&1); rc=$?
t "the rest runs to the end" '[ $rc -eq 0 ] && printf "%s" "$out" | grep -q "done. Start new"'
t "settings.json has no Organism hooks" '[ -z "$(organism_cmds "$H/.claude/settings.json")" ]'
t "...nor does the overlay" '[ -z "$(organism_cmds "$H/.claude/state/settings.local.json")" ]'
t "the other tool's hooks survive" 'grep -q "other-tool pre-bash" "$H/.claude/settings.json" && grep -q "other-tool on-stop" "$H/.claude/settings.json"'
t "the overlay's own prefs survive" 'grep -q "\"model\": \"opus\"" "$H/.claude/settings.json"'
t "the commands link is gone, the hooks link stays" '[ ! -e "$H/.claude/commands" ] && [ -L "$H/.claude/hooks" ]'
t "the install record says plugin-inplace, migration finished" \
  'python3 -c "import json,sys;m=json.load(open(sys.argv[1]));sys.exit(0 if m[\"mode\"]==\"plugin-inplace\" and \"migration\" not in m else 1)" "$H/.claude/state/organism-install.json"'
t "the harness commands probe prints what it printed before ($old_cmds)" '[ "$(harness_value probe commands)" = "$old_cmds" ]'
t "the harness hook-events probe prints what it printed before" '[ "$(harness_value probe settings_hooks)" = "$old_keys" ]'
t "the harness guard check still holds" '[ "$(harness_value guard g_guardpublish)" = yes ]'
t "launchd was only listed, never loaded" '! grep -qE "launchctl (load|unload|bootstrap|bootout)" "$TMP/launchctl.log" 2>/dev/null'
mkdir -p "$TMP/bin"; ln -sf "$R/bin/organism" "$TMP/bin/organism"
out=$(PATH="$TMP/bin:$PATH" run python3 "$R/claude/cli/doctor.py" 2>&1); rc=$?
t "doctor is happy in plugin mode" '[ $rc -eq 0 ]' || printf '%s\n' "$out"

echo "migrate: idempotent"
out=$(migrate --to-plugin 2>&1); rc=$?
t "forward twice is a no-op" '[ $rc -eq 0 ] && printf "%s" "$out" | grep -q "nothing to do"'
cp "$H/.claude/settings.json" "$TMP/plugin.json"
run bash "$R/deploy.sh" >/dev/null 2>&1
t "a later deploy brings no Organism hooks back" '[ -z "$(organism_cmds "$H/.claude/settings.json")" ]'
t "...and keeps the plugin enabled" 'grep -q "\"organism@organism\": true" "$H/.claude/settings.json"'

echo "migrate: back to symlink, after a failed first try"
cp "$R/deploy.sh" "$TMP/deploy.sh.ok"
printf '#!/bin/bash\nexit 3\n' > "$R/deploy.sh"
out=$(migrate --to-symlink 2>&1); rc=$?
cp "$TMP/deploy.sh.ok" "$R/deploy.sh"
t "a failed deploy stops the rollback and says how to retry" '[ $rc -ne 0 ] && printf "%s" "$out" | grep -q "migrate --to-symlink again"'
out=$(FAKE_CLAUDE_FAIL="plugin uninstall" migrate --to-symlink 2>&1); rc=$?
t "a failed plugin uninstall exits non-zero and says how to retry" \
  '[ $rc -ne 0 ] && printf "%s" "$out" | grep -q "still installed" && ! printf "%s" "$out" | grep -q "back in symlink mode"'
echo "migrate: back to symlink"
out=$(migrate --to-symlink 2>&1); rc=$?
t "rollback exits 0" '[ $rc -eq 0 ]' || printf '%s\n' "$out"
t "settings.json is key-for-key equal to the start" \
  'python3 -c "import json,sys;sys.exit(0 if json.load(open(sys.argv[1]))==json.load(open(sys.argv[2])) else 1)" "$TMP/start.json" "$H/.claude/settings.json"'
t "the commands link is back" '[ -L "$H/.claude/commands" ]'
t "the plugin and marketplace are gone" 'grep -q "plugin uninstall organism@organism" "$H/.fake-claude/calls.log" && grep -q "marketplace remove organism" "$H/.fake-claude/calls.log"'
t "the pairings file was moved back" 'cmp -s "$TMP/pairings-start.json" "$H/.claude/state/reconcile-pairings.json"'
out=$(PATH="$TMP/bin:$PATH" run python3 "$R/claude/cli/doctor.py" 2>&1); rc=$?
t "doctor is happy in symlink mode" '[ $rc -eq 0 ]' || printf '%s\n' "$out"

echo "doctor: a double wiring"
printf '{"mode":"plugin-inplace","root":"%s"}\n' "$R" > "$H/.claude/state/organism-install.json"
out=$(PATH="$TMP/bin:$PATH" run python3 "$R/claude/cli/doctor.py" 2>&1); rc=$?
t "flags Organism hooks in settings.json in plugin mode" '[ $rc -ne 0 ] && printf "%s" "$out" | grep -q "run twice"'
rm -f "$H/.claude/state/organism-install.json"

echo "bootstrap.sh --plugin"
out=$(run bash "$R/bootstrap.sh" --plugin 2>&1); rc=$?
t "refuses over an existing symlink install" '[ $rc -ne 0 ] && printf "%s" "$out" | grep -q "migrate --to-plugin"'
H="$TMP/home-new"; mkdir -p "$H"
out=$(run bash "$R/bootstrap.sh" --plugin 2>&1); rc=$?
t "a new install exits 0" '[ $rc -eq 0 ]' || printf '%s\n' "$out" | tail -15
t "the plugin is installed from the clone" 'grep -q "plugin install organism@organism" "$H/.fake-claude/calls.log"'
t "the install record says plugin-inplace" 'grep -q plugin-inplace "$H/.claude/state/organism-install.json"'
t "settings.json has no Organism hooks, the plugin is pinned" \
  '[ -z "$(organism_cmds "$H/.claude/settings.json")" ] && grep -q "\"organism@organism\": true" "$H/.claude/settings.json"'
t "no commands link; the hooks link and the CLI are there" \
  '[ ! -e "$H/.claude/commands" ] && [ -L "$H/.claude/hooks" ] && [ -L "$H/.local/bin/organism" ]'
out=$(env -u RECONCILE_CONFIG HOME="$H" PATH="/usr/bin:/bin" bash "$R/bootstrap.sh" --plugin 2>&1); rc=$?
t "without claude on PATH it refuses" '[ $rc -ne 0 ] && printf "%s" "$out" | grep -q "needs the claude CLI"'

[ "$FAILS" -eq 0 ] && { echo "ALL PASS"; exit 0; }
echo "FAIL: $FAILS check(s)"; exit 1
