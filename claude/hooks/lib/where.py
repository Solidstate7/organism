#!/usr/bin/env python3
"""Where Organism's code, state and config live, in every install mode.

The code root is found from this file's real path, so a hook reached through
~/.claude/hooks/x.py and the same hook reached through ${CLAUDE_PLUGIN_ROOT}
resolve to the same ROOT. State is always ~/.claude/state (or
$ORGANISM_STATE_DIR), whatever the mode.

Install modes:
  symlink         ~/.claude/{hooks,commands,...} link into a clone; hooks wired in settings.json
  plugin-inplace  the clone is also loaded as a plugin (marketplace add <clone>); hooks come from hooks/hooks.json
  plugin-copied   a plugin installed into Claude Code's plugin cache, with no clone

Only installers read the mode. Hooks behave the same in every mode.

CLI (used by where.sh): where.py root|hooks|state|logs|mode|pairings|pairings-write|cache|in-cache
"""
import json
import os
import sys

LIB = os.path.dirname(os.path.realpath(__file__))
HOOKS = os.path.dirname(LIB)
ROOT = os.path.dirname(os.path.dirname(HOOKS))

PAIRINGS = "reconcile-pairings.json"
MARKER = "organism-install.json"
MODES = ("symlink", "plugin-inplace", "plugin-copied")


def home():
    return os.environ.get("HOME") or os.path.expanduser("~")


def state_dir():
    return os.environ.get("ORGANISM_STATE_DIR") or os.path.join(home(), ".claude", "state")


def logs_dir():
    return os.path.join(home(), ".claude", "logs")


def plugins_root():
    return os.environ.get("CLAUDE_CODE_PLUGIN_CACHE_DIR") or os.path.join(home(), ".claude", "plugins")


def cache_dir():
    return os.path.join(plugins_root(), "cache")


def _under(path, parent):
    path, parent = os.path.realpath(path), os.path.realpath(parent)
    return path == parent or path.startswith(parent.rstrip(os.sep) + os.sep)


def in_cache(root=None):
    """True when the code runs from a copy inside Claude Code's plugin cache."""
    return _under(root or ROOT, cache_dir())


def marker_path():
    return os.path.join(state_dir(), MARKER)


def read_marker():
    try:
        with open(marker_path(), encoding="utf-8") as f:
            m = json.load(f)
        return m if isinstance(m, dict) else {}
    except Exception:
        return {}


def mode(root=None):
    root = root or ROOT
    m = read_marker()
    if m.get("mode") in MODES and m.get("root") and \
            os.path.realpath(m["root"]) == os.path.realpath(root):
        return m["mode"]
    if in_cache(root):
        return "plugin-copied"
    return "symlink"


def pairings_candidates():
    out = []
    env = os.environ.get("RECONCILE_CONFIG")
    if env:
        out.append(env)
    out.append(os.path.join(state_dir(), PAIRINGS))
    out.append(os.path.join(HOOKS, PAIRINGS))
    out.append(os.path.join(home(), ".claude", "hooks", PAIRINGS))
    return out


def pairings():
    """The pairings file to read: the first that exists, or None."""
    for p in pairings_candidates():
        if os.path.isfile(p):
            return p
    return None


def pairings_write_path():
    """New pairings files are always written to the state folder."""
    return os.environ.get("RECONCILE_CONFIG") or os.path.join(state_dir(), PAIRINGS)


def main(argv):
    what = argv[1] if len(argv) > 1 else "root"
    value = {
        "root": lambda: ROOT,
        "hooks": lambda: HOOKS,
        "state": state_dir,
        "logs": logs_dir,
        "mode": mode,
        "pairings": lambda: pairings() or "",
        "pairings-write": pairings_write_path,
        "cache": cache_dir,
        "in-cache": lambda: "yes" if in_cache() else "no",
    }.get(what)
    if value is None:
        print(f"where.py: unknown key {what!r}", file=sys.stderr)
        return 2
    print(value())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
