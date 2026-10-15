#!/usr/bin/env python3
"""tests for hooks/lib/where.py: the root found through links, the pairings read order
and write target, and install-mode detection (a fake plugin cache, an install marker
whose root does not match). where.sh must give the same answers. Each case runs where.py
in a subprocess with HOME and the cache dir pointed at temp dirs.
Run:  python3 claude/hooks/tests/test_where.py"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HOOKS = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
ROOT = os.path.dirname(os.path.dirname(HOOKS))
WHERE = os.path.join(HOOKS, "lib", "where.py")

_fails = []
_tmp = []


def check(name, cond):
    print(("  ✓ " if cond else "  ✗ ") + name)
    if not cond:
        _fails.append(name)


def tmp():
    d = tempfile.mkdtemp(prefix="where-test-")
    _tmp.append(d)
    return d


def env_for(h, **extra):
    env = {k: v for k, v in os.environ.items()
           if k not in ("RECONCILE_CONFIG", "ORGANISM_STATE_DIR", "CLAUDE_CODE_PLUGIN_CACHE_DIR")}
    env.update(HOME=h, CLAUDE_CODE_PLUGIN_CACHE_DIR=os.path.join(h, "plugins"))
    env.update(extra)
    return env


def where(h, key, script=WHERE, **extra):
    return subprocess.run([sys.executable, script, key], env=env_for(h, **extra),
                          capture_output=True, text=True).stdout.strip()


def where_sh(h, key, **extra):
    lib = os.path.dirname(WHERE)
    return subprocess.run(["bash", "-c", f'. "{lib}/where.sh"; organism_where {key}'],
                          env=env_for(h, **extra), capture_output=True, text=True).stdout.strip()


def write(path, text="{}"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


def copy_tree(dest):
    """A gitless copy of the tracked tree (symlinks kept)."""
    os.makedirs(dest, exist_ok=True)
    listed = subprocess.run(["git", "-C", ROOT, "ls-files", "-z", "--cached", "--others",
                             "--exclude-standard"], capture_output=True).stdout.split(b"\0")
    files = b"\0".join(f for f in listed if f and os.path.lexists(os.path.join(ROOT, f.decode())))
    tar = subprocess.Popen(["tar", "--null", "-T", "-", "-cf", "-"], cwd=ROOT,
                           stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    out, _ = tar.communicate(files)
    subprocess.run(["tar", "-xf", "-", "-C", dest], input=out, check=True)
    return dest


print("where: root")
h = tmp()
check("root is this repo", where(h, "root") == os.path.realpath(ROOT))
check("hooks is claude/hooks", where(h, "hooks") == os.path.join(os.path.realpath(ROOT), "claude", "hooks"))
link = os.path.join(h, ".claude", "hooks")
os.makedirs(os.path.dirname(link))
os.symlink(HOOKS, link)
check("root is the same through ~/.claude/hooks",
      where(h, "root", script=os.path.join(link, "lib", "where.py")) == os.path.realpath(ROOT))
check("state is ~/.claude/state", where(h, "state") == os.path.join(h, ".claude", "state"))
check("ORGANISM_STATE_DIR overrides state", where(h, "state", ORGANISM_STATE_DIR="/x/s") == "/x/s")

print("where: pairings read order")
h = tmp()
state_p = os.path.join(h, ".claude", "state", "reconcile-pairings.json")
legacy_p = os.path.join(h, ".claude", "hooks", "reconcile-pairings.json")
env_p = os.path.join(h, "env.json")
root_p = os.path.join(HOOKS, "reconcile-pairings.json")
# a clone in daily use may keep its own (gitignored) pairings file in claude/hooks, which
# where.py rightly finds; only a clone without one can show the empty case
if not os.path.exists(root_p):
    check("nothing found: empty", where(h, "pairings") == "")
write(legacy_p)
if os.path.exists(root_p):   # a clone with its own pairings file: it is found first
    print("  - this clone has its own pairings file: legacy-order checks skipped")
else:
    check("~/.claude/hooks is the last fallback", where(h, "pairings") == legacy_p)
    write(root_p)
    try:
        check("the repo's copy beats ~/.claude/hooks", where(h, "pairings") == root_p)
    finally:
        os.remove(root_p)
write(state_p)
check("the state copy beats the legacy ones", where(h, "pairings") == state_p)
write(env_p)
check("$RECONCILE_CONFIG beats everything", where(h, "pairings", RECONCILE_CONFIG=env_p) == env_p)
check("where.sh agrees", where_sh(h, "pairings") == state_p)
check("writes go to the state folder", where(tmp(), "pairings-write")
      .endswith(os.path.join(".claude", "state", "reconcile-pairings.json")))

print("where: install mode")
h = tmp()
check("a clone with no marker is symlink mode", where(h, "mode") == "symlink")
marker = os.path.join(h, ".claude", "state", "organism-install.json")
write(marker, json.dumps({"mode": "plugin-inplace", "root": ROOT}))
check("the marker's mode applies when its root is this one", where(h, "mode") == "plugin-inplace")
check("where.sh agrees", where_sh(h, "mode") == "plugin-inplace")
write(marker, json.dumps({"mode": "plugin-inplace", "root": "/somewhere/else"}))
check("a marker for another root is ignored", where(h, "mode") == "symlink")
write(marker, json.dumps({"mode": "bogus", "root": ROOT}))
check("an unknown mode in the marker is ignored", where(h, "mode") == "symlink")
write(marker, "{not json")
check("an unreadable marker is ignored", where(h, "mode") == "symlink")

h = tmp()
cached = copy_tree(os.path.join(h, "plugins", "cache", "organism", "organism", "0.1.0"))
cw = os.path.join(cached, "claude", "hooks", "lib", "where.py")
check("a copy under the plugin cache is plugin-copied", where(h, "mode", script=cw) == "plugin-copied")
check("...and finds the cached root", where(h, "root", script=cw) == os.path.realpath(cached))
check("...still reads the shared state folder", where(h, "state", script=cw) == os.path.join(h, ".claude", "state"))
other = tmp()
check("CLAUDE_CODE_PLUGIN_CACHE_DIR decides where the cache is",
      where(h, "mode", script=cw, CLAUDE_CODE_PLUGIN_CACHE_DIR=os.path.join(other, "p")) == "symlink")

for d in _tmp:
    shutil.rmtree(d, ignore_errors=True)
if _fails:
    print(f"FAIL: {len(_fails)} check(s)")
    sys.exit(1)
print("ALL PASS")
