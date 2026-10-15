#!/usr/bin/env python3
"""tests for hooks/claude-md-check.py: slash commands, including the plugin form
/organism:handoff, are never taken for paths, while a real missing path is reported.
Run:  python3 claude/hooks/tests/test_claude_md_check.py"""
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile

HOOKS = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
SCRIPT = os.path.join(HOOKS, "claude-md-check.py")
spec = importlib.util.spec_from_file_location("claude_md_check", SCRIPT)
cmc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cmc)

_fails = []


def check(name, cond):
    print(("  ✓ " if cond else "  ✗ ") + name)
    if not cond:
        _fails.append(name)


print("claude-md-check: slash commands are not paths")
for cmd in ("/handoff", "/incognito", "/organism:handoff", "/organism:organism-setup"):
    check(f"{cmd} is a command", not cmc.looks_like_path(cmd))
for path in ("src/app.py", "docs/ADVANCED.md"):
    check(f"{path} is a path", cmc.looks_like_path(path))

print("claude-md-check: end to end")
d = tempfile.mkdtemp(prefix="cmc-")
try:
    os.makedirs(os.path.join(d, "src"))
    open(os.path.join(d, "src", "here.py"), "w").close()
    with open(os.path.join(d, "CLAUDE.md"), "w") as f:
        f.write("Run `/organism:handoff` or `/handoff` at the end. Code: `src/here.py`, `src/gone.py`.\n")
    out = subprocess.run([sys.executable, SCRIPT, d], capture_output=True, text=True).stdout
    check("reports the one missing path", "1 missing" in out and "src/gone.py" in out)
    check("never names the command", "organism:handoff" not in out)
finally:
    shutil.rmtree(d, ignore_errors=True)

if _fails:
    print(f"FAIL: {len(_fails)} check(s)")
    sys.exit(1)
print("ALL PASS")
