#!/usr/bin/env python3
"""tests for hooks/lib/settingsmerge.py: the first deploy keeps a settings.json that
Organism never generated (a backup, then a fold into the overlay); hook lists are
unioned, so a user's hooks run beside the base's and a hook added to the base later
still arrives; re-runs are idempotent. Every case runs the script in a subprocess with
HOME pointed at its own temp dir, so the live ~/.claude is never touched.
Run:  python3 claude/hooks/tests/test_settingsmerge.py"""
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile

HOOKS = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
SCRIPT = os.path.join(HOOKS, "lib", "settingsmerge.py")

_fails = []
_homes = []


def check(name, cond):
    print(("  ✓ " if cond else "  ✗ ") + name)
    if not cond:
        _fails.append(name)


GUARD = {"matcher": "Bash", "hooks": [{"type": "command", "command": "sh guard.sh"}]}
MIRROR = {"hooks": [{"type": "command", "command": "python3 mirror.py"}]}
BASE = {"hooks": {"PreToolUse": [GUARD], "Stop": [MIRROR]}}

LINT = {"matcher": "Edit", "hooks": [{"type": "command", "command": "my-lint.sh"}]}
SAY = {"hooks": [{"type": "command", "command": "say done"}]}
USER = {"model": "opus", "permissions": {"allow": ["Bash(ls:*)"]},
        "hooks": {"PreToolUse": [LINT], "Notification": [SAY]}}


def home():
    h = tempfile.mkdtemp(prefix="settingsmerge-test-")
    os.makedirs(os.path.join(h, ".claude", "state"))
    _homes.append(h)
    return h


def run(h, base, mode="plugin", *extra):
    """Plugin mode renders nothing, so the cases below test the plain merge."""
    bp = os.path.join(h, "base.json")
    with open(bp, "w") as f:
        json.dump(base, f)
    env = dict(os.environ, HOME=h)
    return subprocess.run([sys.executable, SCRIPT, bp, "--mode", mode, *extra],
                          env=env, capture_output=True, text=True)


def overlay(h):
    with open(os.path.join(h, ".claude", "state", "settings.local.json")) as f:
        return json.load(f)


def write_overlay(h, obj):
    with open(os.path.join(h, ".claude", "state", "settings.local.json"), "w") as f:
        json.dump(obj, f)


def commands(settings_obj):
    """Every hook command line in a settings object, args joined in."""
    out = []
    for groups in (settings_obj.get("hooks") or {}).values():
        for g in groups:
            for hk in g.get("hooks", []):
                out.append(" ".join([hk.get("command", "")] + hk.get("args", [])))
    return out


def organism_commands(settings_obj):
    names = ("guard-publish.sh", "inject-handoff.sh", "obsidian-mirror.py", "session-end-handoff.py")
    return [c for c in commands(settings_obj) if any("claude/hooks/" + n in c for n in names)]


def settings(h):
    with open(os.path.join(h, ".claude", "settings.json")) as f:
        return json.load(f)


def write_settings(h, text):
    with open(os.path.join(h, ".claude", "settings.json"), "w") as f:
        f.write(text)


def backups(h):
    return glob.glob(os.path.join(h, ".claude", "settings.json.pre-organism*.bak"))


print("settingsmerge: fresh machine")
h = home()
run(h, BASE)
check("writes the base", settings(h) == BASE)
check("makes no backup", backups(h) == [])
check("writes no overlay", not os.path.exists(os.path.join(h, ".claude", "state", "settings.local.json")))

print("settingsmerge: existing settings.json")
h = home()
write_settings(h, json.dumps(USER))
r = run(h, BASE)
s = settings(h)
baks = backups(h)
check("backs up the original", len(baks) == 1 and json.load(open(baks[0])) == USER)
check("says it kept them", "kept your existing settings" in r.stdout)
check("keeps the user's model", s.get("model") == "opus")
check("keeps the user's permissions", s.get("permissions") == USER["permissions"])
check("keeps the base hook and adds the user's beside it",
      s["hooks"]["PreToolUse"] == [GUARD, LINT])
check("keeps a hook event only the user had", s["hooks"].get("Notification") == [SAY])
check("keeps a hook event only the base had", s["hooks"].get("Stop") == [MIRROR])

print("settingsmerge: re-run")
before = settings(h)
run(h, BASE)
check("leaves settings unchanged", settings(h) == before)
check("makes no second backup", len(backups(h)) == 1)

print("settingsmerge: a hook added to the base later")
base2 = json.loads(json.dumps(BASE))
MCP = {"matcher": "mcp__.*", "hooks": [{"type": "command", "command": "sh guard.sh"}]}
base2["hooks"]["PreToolUse"].append(MCP)
run(h, base2)
check("arrives, and the user's hook stays", settings(h)["hooks"]["PreToolUse"] == [GUARD, MCP, LINT])

print("settingsmerge: unreadable settings.json")
h = home()
write_settings(h, "{not json")
r = run(h, BASE)
baks = backups(h)
check("backs it up untouched", len(baks) == 1 and open(baks[0]).read() == "{not json")
check("writes the base", settings(h) == BASE)
check("says it was not merged", "not merged" in r.stderr)

print("settingsmerge: a hand edit breaks settings.json after a deploy")
h = home()
run(h, BASE)
write_settings(h, '{"permissions": {"allow": ["Bash(ls)"]},}')
r = run(h, BASE)
baks = backups(h)
check("the broken file is backed up before the rewrite",
      len(baks) == 1 and open(baks[0]).read() == '{"permissions": {"allow": ["Bash(ls)"]},}')
check("says it was not merged", "not merged" in r.stderr)
check("writes the base", settings(h) == BASE)

print("settingsmerge: settings.json left as a symlink by the old deploy")
h = home()
clone = os.path.join(h, "clone", "claude")
os.makedirs(os.path.join(clone, "hooks", "lib"))
open(os.path.join(clone, "hooks", "lib", "settingsmerge.py"), "w").close()
target = os.path.join(clone, "settings.json")
with open(target, "w") as f:
    json.dump(BASE, f)
os.symlink(target, os.path.join(h, ".claude", "settings.json"))
run(h, BASE)
p = os.path.join(h, ".claude", "settings.json")
check("replaces the link with a real file", os.path.isfile(p) and not os.path.islink(p) and settings(h) == BASE)
check("makes no backup", backups(h) == [])

print("settingsmerge: settings.json is the user's own link (a dotfiles repo)")
h = home()
target = os.path.join(h, "dotfiles", "settings.json")
os.makedirs(os.path.dirname(target))
with open(target, "w") as f:
    json.dump(USER, f)
os.symlink(target, os.path.join(h, ".claude", "settings.json"))
run(h, BASE)
s = settings(h)
check("keeps the user's settings", s.get("model") == "opus" and s.get("permissions") == USER["permissions"])
check("backs up the linked file's content", len(backups(h)) == 1 and json.load(open(backups(h)[0])) == USER)
check("leaves the dotfiles file itself alone", json.load(open(target)) == USER)

print("settingsmerge: a broken overlay stops the merge")
h = home()
write_settings(h, json.dumps(USER))
ov = os.path.join(h, ".claude", "state", "settings.local.json")
with open(ov, "w") as f:
    f.write('{"permissions": {"defaultMode": "acceptEdits"')
r = run(h, BASE)
check("exits 1 and says why", r.returncode == 1 and "not a JSON object" in r.stderr)
check("leaves the overlay and settings.json untouched",
      open(ov).read().endswith('"acceptEdits"') and settings(h) == USER)

print("settingsmerge: eject with an unreadable base fails")
h = home()
env = dict(os.environ, HOME=h)
r = subprocess.run([sys.executable, SCRIPT, os.path.join(h, "missing.json"), "--mode", "eject"],
                   env=env, capture_output=True, text=True)
check("exits 1", r.returncode == 1 and "base unreadable" in r.stderr)

# --- Organism's own hooks: one source (hooks/hooks.json), stripped everywhere else ---
REPO = os.path.dirname(os.path.dirname(HOOKS))
with open(os.path.join(REPO, "hooks", "hooks.json")) as f:
    HOOKS_JSON = json.load(f)

print("settingsmerge: the repo base")
with open(os.path.join(REPO, "claude", "settings.json")) as f:
    repo_base = json.load(f)
check("claude/settings.json holds no hooks (hooks/hooks.json is the one source)",
      "hooks" not in repo_base)

print("settingsmerge: legacy mode renders hooks/hooks.json")
h = home()
run(h, {}, "legacy", "--root", "/opt/org root")
want = json.loads(json.dumps(HOOKS_JSON["hooks"]).replace("${CLAUDE_PLUGIN_ROOT}", "/opt/org root"))
for groups in want.values():
    for g in groups:
        for hk in g["hooks"]:
            hk["command"] = hk["command"] + " " + " ".join("'%s'" % a for a in hk.pop("args"))
check("settings hooks equal hooks.json with the root filled in, in shell form",
      settings(h).get("hooks") == want)
check("no exec-form args in settings.json (older CLIs would ignore them)",
      "\"args\"" not in json.dumps(settings(h)))
check("no placeholder is left", "${CLAUDE_PLUGIN_ROOT}" not in json.dumps(settings(h)))
check("the guard keeps onFailure block", all(
    hk.get("onFailure") == "block" for g in settings(h)["hooks"]["PreToolUse"] for hk in g["hooks"]))
h = home()
run(h, {}, "legacy")
check("the default root is this repo", os.path.realpath(REPO) + "/claude/hooks/" in json.dumps(settings(h)))

# an overlay shaped like a real one: the guard twice and the mirror once (old shell
# form, through the ~/.claude/hooks link), next to another tool's entries
OTHER_PRE = {"matcher": "Bash", "hooks": [{"type": "command", "command": "other-tool pre"}]}
OTHER_STOP = {"hooks": [{"type": "command", "command": "other-tool stop"}]}
OTHER_NOTE = {"hooks": [{"type": "command", "command": "other-tool notify"}]}
OLD_GUARD = {"matcher": "Bash", "hooks": [{"type": "command",
             "command": "sh $HOME/.claude/hooks/guard-publish.sh", "timeout": 15}]}
OLD_GUARD_MCP = {"matcher": "mcp__.*", "hooks": [{"type": "command",
                 "command": "sh $HOME/.claude/hooks/guard-publish.sh", "timeout": 15}]}
MIXED_STOP = {"hooks": [{"type": "command", "command": "python3 $HOME/.claude/hooks/obsidian-mirror.py"},
                        {"type": "command", "command": "other-tool also-stop"}]}
REAL_OVERLAY = {"model": "opus", "permissions": {"defaultMode": "bypassPermissions"},
                "hooks": {"PreToolUse": [OLD_GUARD, OLD_GUARD_MCP, OTHER_PRE],
                          "Stop": [MIXED_STOP, OTHER_STOP], "Notification": [OTHER_NOTE]}}


def overlay_backups(h):
    return glob.glob(os.path.join(h, ".claude", "state", "settings.local.json.*.bak"))


print("settingsmerge: plugin mode strips Organism's entries from a real-shaped overlay")
h = home()
write_overlay(h, REAL_OVERLAY)
r = run(h, {})
s = settings(h)
check("no Organism hook is left in settings.json", organism_commands(s) == [])
check("no Organism hook is left in the overlay", organism_commands(overlay(h)) == [])
check("the other tool's entries all stay", sorted(commands(s)) == sorted(
    ["other-tool pre", "other-tool stop", "other-tool notify", "other-tool also-stop"]))
check("a shared matcher group keeps its other hook",
      {"type": "command", "command": "other-tool also-stop"} in s["hooks"]["Stop"][0]["hooks"])
check("non-hook keys stay", s.get("model") == "opus" and s.get("permissions") == REAL_OVERLAY["permissions"])
baks = overlay_backups(h)
check("the overlay was backed up first", len(baks) == 1 and json.load(open(baks[0])) == REAL_OVERLAY)
run(h, {})
check("a re-run makes no second overlay backup", len(overlay_backups(h)) == 1)

print("settingsmerge: legacy mode with the same overlay runs each hook once")
h = home()
write_overlay(h, REAL_OVERLAY)
run(h, {}, "legacy", "--root", "/r")
s = settings(h)
got = sorted(organism_commands(s))
check("exactly the hooks.json entries, once each", got == sorted(
    c for c in organism_commands(
        {"hooks": json.loads(json.dumps(HOOKS_JSON["hooks"]).replace("${CLAUDE_PLUGIN_ROOT}", "/r"))})))
check("the guard is not doubled", sum("guard-publish.sh" in c for c in got) == 3)
check("the other tool's entries stay", "other-tool pre" in commands(s) and "other-tool notify" in commands(s))

print("settingsmerge: a later drift fold can't bring Organism's entries back")
h = home()
run(h, {}, "legacy", "--root", "/r")
live = settings(h)
live["hooks"]["PreToolUse"].append(OTHER_PRE)          # an in-session edit next to ours
write_settings(h, json.dumps(live))
run(h, {})                                             # now in plugin mode
s = settings(h)
check("the in-session hook survives", "other-tool pre" in commands(s))
check("Organism's hooks are gone from settings and overlay",
      organism_commands(s) == [] and organism_commands(overlay(h)) == [])

print("settingsmerge: eject drops the plugin so a later deploy can't revive it")
h = home()
write_overlay(h, {"enabledPlugins": {"organism@organism": True, "other@market": True},
                  "extraKnownMarketplaces": {"organism": {"source": {"source": "directory", "path": "/r"}}},
                  "hooks": {"PreToolUse": [OLD_GUARD, OTHER_PRE]}})
run(h, {})
check("plugin mode keeps the plugin enabled", settings(h)["enabledPlugins"].get("organism@organism") is True)
live = settings(h)
del live["enabledPlugins"]["organism@organism"]        # what `claude plugin uninstall` does
del live["extraKnownMarketplaces"]
write_settings(h, json.dumps(live))
run(h, {}, "eject")
s, o = settings(h), overlay(h)
check("eject: plugin gone from settings.json", "organism@organism" not in s.get("enabledPlugins", {}))
check("eject: plugin gone from the overlay", "organism@organism" not in o.get("enabledPlugins", {}))
check("eject: marketplace gone from both", "organism" not in s.get("extraKnownMarketplaces", {})
      and "organism" not in o.get("extraKnownMarketplaces", {}))
check("eject: other plugins stay", s.get("enabledPlugins", {}).get("other@market") is True)
check("eject: no Organism hooks", organism_commands(s) == [])
check("eject: other hooks stay", "other-tool pre" in commands(s))
run(h, {}, "legacy", "--root", "/r")                   # e.g. a later deploy of an old checkout
check("a later deploy does not resurrect the plugin",
      "organism@organism" not in settings(h).get("enabledPlugins", {})
      and "organism" not in settings(h).get("extraKnownMarketplaces", {}))

print("settingsmerge: a bad mode leaves settings.json untouched")
h = home()
write_settings(h, json.dumps(USER))
r = run(h, {}, "bogus")
check("untouched", settings(h) == USER and "unknown mode" in r.stderr)

for d in _homes:
    shutil.rmtree(d, ignore_errors=True)
if _fails:
    print(f"FAIL: {len(_fails)} check(s)")
    sys.exit(1)
print("ALL PASS")
