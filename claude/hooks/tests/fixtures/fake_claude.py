#!/usr/bin/env python3
"""A stand-in for the `claude` CLI in tests: the plugin and marketplace verbs only.

It keeps its state in $HOME/.fake-claude/state.json, logs every call to
$HOME/.fake-claude/calls.log, and edits ~/.claude/settings.json the way the real
CLI does (marketplace add writes extraKnownMarketplaces, install writes
enabledPlugins, uninstall and marketplace remove drop those keys again).
FAKE_CLAUDE_VERSION sets the reported version; FAKE_CLAUDE_FAIL=<verb> makes that verb fail.
"""
import json
import os
import sys

HOME = os.environ["HOME"]
D = os.path.join(HOME, ".fake-claude")
STATE = os.path.join(D, "state.json")
SETTINGS = os.path.join(HOME, ".claude", "settings.json")


def load(p, default):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def dump(p, obj):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2)
        f.write("\n")


def edit_settings(fn):
    s = load(SETTINGS, {})
    fn(s)
    dump(SETTINGS, s)


def main(argv):
    os.makedirs(D, exist_ok=True)
    with open(os.path.join(D, "calls.log"), "a", encoding="utf-8") as f:
        f.write(" ".join(argv) + "\n")
    st = load(STATE, {"marketplaces": {}, "plugins": {}})
    verb = " ".join(a for a in argv[:3] if not a.startswith("-"))
    if os.environ.get("FAKE_CLAUDE_FAIL") and verb.startswith(os.environ["FAKE_CLAUDE_FAIL"]):
        print(f"fake failure: {verb}", file=sys.stderr)
        return 1
    if argv[:1] == ["--version"]:
        print(f"{os.environ.get('FAKE_CLAUDE_VERSION', '2.1.300')} (Claude Code)")
        return 0
    if argv[:2] == ["plugin", "validate"]:
        print("Validation passed")
        return 0
    if argv[:3] == ["plugin", "marketplace", "add"]:
        path = os.path.realpath(argv[3])
        st["marketplaces"]["organism"] = path
        edit_settings(lambda s: s.setdefault("extraKnownMarketplaces", {}).update(
            {"organism": {"source": {"source": "directory", "path": path}}}))
    elif argv[:3] == ["plugin", "marketplace", "list"]:
        print(json.dumps([{"name": n, "source": "directory", "path": p, "installLocation": p}
                          for n, p in st["marketplaces"].items()], indent=2))
        return 0
    elif argv[:3] == ["plugin", "marketplace", "remove"]:
        if argv[3] not in st["marketplaces"]:
            print("not found", file=sys.stderr)
            return 1
        st["marketplaces"].pop(argv[3])
        edit_settings(lambda s: s.setdefault("extraKnownMarketplaces", {}).pop(argv[3], None))
    elif argv[:2] == ["plugin", "install"]:
        pid = argv[2]
        name, _, mk = pid.partition("@")
        if mk not in st["marketplaces"]:
            print(f"marketplace {mk} not found", file=sys.stderr)
            return 1
        st["plugins"][pid] = {"enabled": True, "folder": st["marketplaces"][mk]}
        edit_settings(lambda s: s.setdefault("enabledPlugins", {}).update({pid: True}))
    elif argv[:2] == ["plugin", "uninstall"]:
        if argv[2] not in st["plugins"]:
            print("not installed", file=sys.stderr)
            return 1
        st["plugins"].pop(argv[2])
        edit_settings(lambda s: (s.get("enabledPlugins") or {}).pop(argv[2], None))
    elif argv[:2] == ["plugin", "disable"]:
        st["plugins"][argv[2]]["enabled"] = False
        edit_settings(lambda s: s.setdefault("enabledPlugins", {}).update({argv[2]: False}))
    elif argv[:2] == ["plugin", "list"]:
        print(json.dumps([{"id": pid, "version": "0.1.0", "scope": "user", "enabled": p["enabled"],
                           "installPath": os.path.join(HOME, ".claude", "plugins", "cache", pid),
                           "readFromFolder": p["folder"]} for pid, p in st["plugins"].items()], indent=2))
        return 0
    else:
        print(f"fake claude: unsupported {argv}", file=sys.stderr)
        return 2
    dump(STATE, st)
    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
