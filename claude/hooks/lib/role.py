#!/usr/bin/env python3
"""hooks/lib/role.py: per-device role gate.

should_run(role) decides whether THIS machine should run a job of a given class,
WITHOUT any runtime coordination with other devices: each device reads its own
gitignored ~/.claude/state/host.json (or $CLAUDE_HOST_JSON) and decides locally.

  per_host             : local jobs (health, logrotate, session hooks). Default ON.
  singleton_bookkeeping: vault writers (reconcile, the doc check). One device only.
  singleton_sideeffect : jobs with outside effects (e.g. a bot). One device only.

Safe-by-default: a device with NO host.json (a freshly linked machine) runs per_host
ONLY, it can never double-run a singleton. A singleton role also requires the pinned
platform_uuid to still match this hardware, so copying host.json to a CLONE does not
transfer ownership (the clone's UUID differs → its singletons auto-demote to off).

CLI:  role.py <role>     exit 0 = should run, 1 = skip.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hostid

SINGLETONS = ("singleton_bookkeeping", "singleton_sideeffect")


def host_json_path():
    return os.environ.get("CLAUDE_HOST_JSON") or os.path.join(
        os.environ.get("HOME") or os.path.expanduser("~"), ".claude", "state", "host.json")


def load():
    try:
        with open(host_json_path()) as f:
            return json.load(f)
    except Exception:
        return {}


def should_run(role):
    cfg = load()
    roles = cfg.get("roles", {})
    if role not in SINGLETONS:
        return bool(roles.get("per_host", True))      # per-host defaults ON
    if not roles.get(role, False):
        return False                                  # singleton not granted on this host
    pinned = cfg.get("platform_uuid")
    if pinned and pinned != hostid.platform_uuid():
        return False                                  # host.json copied to other hardware
    return True


if __name__ == "__main__":
    role = sys.argv[1] if len(sys.argv) > 1 else "per_host"
    try:
        ok = should_run(role)
    except Exception:
        ok = True   # fail-safe: a broken gate must never silently disable a job
    sys.exit(0 if ok else 1)
