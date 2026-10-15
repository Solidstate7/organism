#!/usr/bin/env python3
"""tests for the plugin layout: .claude-plugin/{plugin,marketplace}.json, hooks/hooks.json,
skills/*/SKILL.md and the legacy command links. Also the root rules a plugin needs (no
settings.json, CLAUDE.md or commands/ at the root, only `organism` in bin/) and that git
can never track the folders deploy.sh links personal files into. When a real `claude` CLI
is on PATH it also runs `claude plugin validate --strict` and `plugin details` in a temp
HOME; otherwise those checks are skipped.
Run:  python3 claude/hooks/tests/test_plugin_manifest.py"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HOOKS = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
ROOT = os.path.dirname(os.path.dirname(HOOKS))

_fails = []


def check(name, cond):
    print(("  ✓ " if cond else "  ✗ ") + name)
    if not cond:
        _fails.append(name)


def load(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return json.load(f)


def frontmatter(path):
    with open(path, encoding="utf-8") as f:
        text = f.read()
    m = re.match(r"---\n(.*?)\n---\n", text, re.S)
    out = {}
    for ln in (m.group(1).splitlines() if m else []):
        k, _, v = ln.partition(":")
        out[k.strip()] = v.strip()
    return out, text


print("plugin: manifests")
plugin = load(".claude-plugin/plugin.json")
market = load(".claude-plugin/marketplace.json")
check("plugin name is organism", plugin.get("name") == "organism")
check("plugin version is a semver string", re.fullmatch(r"\d+\.\d+\.\d+", plugin.get("version", "")) is not None)
check("no hooks key in the manifest (hooks/hooks.json is loaded by default)", "hooks" not in plugin)
check("marketplace is named organism", market.get("name") == "organism")
entries = market.get("plugins", [])
check("marketplace lists the plugin at its own root",
      len(entries) == 1 and entries[0].get("name") == "organism" and entries[0].get("source") == "./")

print("plugin: hooks/hooks.json")
hj = load("hooks/hooks.json")["hooks"]
check("events are the four Organism uses", set(hj) == {"PreToolUse", "SessionStart", "Stop", "SessionEnd"})
check("PreToolUse matchers: Bash, MCP tools, web tools",
      [g.get("matcher") for g in hj["PreToolUse"]] == ["Bash", "mcp__.*", "WebFetch|WebSearch"])
all_hooks = [(ev, g, hk) for ev, groups in hj.items() for g in groups for hk in g["hooks"]]
check("every hook is exec form (args set)", all(isinstance(hk.get("args"), list) and hk["args"] for _, _, hk in all_hooks))
scripts = []
for ev, g, hk in all_hooks:
    for a in hk["args"]:
        if "${CLAUDE_PLUGIN_ROOT}" in a:
            scripts.append(a.replace("${CLAUDE_PLUGIN_ROOT}", ROOT))
check("every hook script exists under the plugin root", scripts and all(os.path.isfile(p) for p in scripts))
check("every guard entry blocks when the hook fails",
      all(hk.get("onFailure") == "block" for g in hj["PreToolUse"] for hk in g["hooks"]))
check("SessionStart has no matcher (startup, resume, clear and compact)",
      all("matcher" not in g for g in hj["SessionStart"]))
check("SessionEnd has no timeout (a plugin's can't raise the budget)",
      all("timeout" not in hk for g in hj["SessionEnd"] for hk in g["hooks"]))
check("no hook names ~/.claude/hooks", "/.claude/hooks" not in json.dumps(hj))

print("plugin: skills")
want = {"handoff": "handoff", "incognito": "incognito", "setup": "organism-setup"}
skill_dirs = sorted(d for d in os.listdir(os.path.join(ROOT, "skills"))
                    if os.path.isdir(os.path.join(ROOT, "skills", d)))
check("skills are handoff, incognito and setup", skill_dirs == sorted(want))
for d, name in want.items():
    p = os.path.join(ROOT, "skills", d, "SKILL.md")
    fm, text = frontmatter(p) if os.path.isfile(p) else ({}, "")
    check(f"{d}: name is {name}", fm.get("name") == name)
    check(f"{d}: has a description", bool(fm.get("description")))
    check(f"{d}: disable-model-invocation", fm.get("disable-model-invocation") == "true")
    check(f"{d}: never names ~/.claude/hooks", "~/.claude/hooks" not in text)
    tools = fm.get("allowed-tools", "")
    check(f"{d}: Bash grants go through `organism <verb>`",
          all(t.startswith(("organism ", "~/.local/bin/organism ")) or not t.startswith(("bash ", "sh ", "python3 ~"))
              for t in re.findall(r"Bash\(([^)]*)\)", tools)))
    link = os.path.join(ROOT, "claude", "commands", d + ".md")
    check(f"{d}: claude/commands/{d}.md links to the skill (one source for legacy mode)",
          os.path.islink(link) and os.readlink(link) == f"../../skills/{d}/SKILL.md"
          and os.path.realpath(link) == os.path.realpath(p))
_, handoff = frontmatter(os.path.join(ROOT, "skills", "handoff", "SKILL.md"))
check("handoff calls organism adr-link and organism guard-scan",
      "organism adr-link state" in handoff and "organism guard-scan" in handoff)
check("handoff names the ~/.local/bin/organism fallback", "~/.local/bin/organism" in handoff)

print("plugin: root rules")
for name in ("settings.json", "CLAUDE.md", "commands", "agents"):
    check(f"no {name} at the plugin root", not os.path.lexists(os.path.join(ROOT, name)))
check("bin/ holds only organism (it lands on the Bash tool's PATH)",
      sorted(f for f in os.listdir(os.path.join(ROOT, "bin")) if f != "__pycache__") == ["organism"])
check("bin/organism is executable", os.access(os.path.join(ROOT, "bin", "organism"), os.X_OK))

print("plugin: git never tracks personal folders")
tracked = subprocess.run(["git", "-C", ROOT, "ls-files"], capture_output=True, text=True)
if tracked.returncode != 0:
    print("  - not a git work tree: skipped")
else:
    files = tracked.stdout.splitlines()
    for prefix in ("claude/hooks/local/", "claude/skills/"):
        check(f"nothing tracked under {prefix}", not any(f.startswith(prefix) for f in files))
    check("no reconcile-pairings.json tracked", not any(f.endswith("/reconcile-pairings.json")
                                                       or f == "reconcile-pairings.json" for f in files))
    for probe in ("claude/hooks/local/job.py", "claude/skills/mine/SKILL.md",
                  "claude/hooks/reconcile-pairings.json", "claude/hooks", "claude/hooks/local"):
        r = subprocess.run(["git", "-C", ROOT, "check-ignore", "-q", "--no-index", probe])
        if probe in ("claude/hooks",):
            check(f"{probe} itself is not ignored (it holds the code)", r.returncode != 0)
        else:
            check(f"{probe} is ignored", r.returncode == 0)

print("plugin: claude CLI checks")
claude = shutil.which("claude")
if not claude or os.environ.get("ORGANISM_SKIP_CLI"):
    print("  - no claude CLI on PATH: skipped")
else:
    h = tempfile.mkdtemp(prefix="plugin-cli-")
    env = dict(os.environ, HOME=h, CMUX_CLAUDE_HOOKS_DISABLED="1")
    try:
        r = subprocess.run([claude, "plugin", "validate", ROOT, "--strict"], env=env,
                           capture_output=True, text=True, timeout=120)
        check("claude plugin validate --strict passes", r.returncode == 0)
        if r.returncode != 0:
            print((r.stdout + r.stderr)[-1500:])
        r = subprocess.run([claude, "--plugin-dir", ROOT, "plugin", "details", "organism"], env=env,
                           capture_output=True, text=True, timeout=120)
        out = r.stdout
        check("plugin details: 3 skills", re.search(r"Skills \(3\)", out) is not None)
        check("plugin details: 4 hook events", re.search(r"Hooks \(4\)", out) is not None)
    except subprocess.TimeoutExpired:
        check("claude CLI answered in time", False)
    finally:
        shutil.rmtree(h, ignore_errors=True)

if _fails:
    print(f"FAIL: {len(_fails)} check(s)")
    sys.exit(1)
print("ALL PASS")
