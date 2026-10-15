#!/usr/bin/env python3
"""hooks/host-role: manage THIS device's role. Writes ~/.claude/state/host.json.

  host-role status              show this device's id, uuid, and effective roles
  host-role init                create host.json (per_host only) if absent, the safe default
  host-role claim owner         grant BOTH singletons + per_host (this device runs everything)
  host-role claim bookkeeping   grant singleton_bookkeeping (reconcile and other vault writers)
  host-role claim sideeffect    grant singleton_sideeffect (jobs with outside effects, e.g. a bot)
  host-role release             drop all singleton roles (back to per_host only)

Promotion is a deliberate ritual, never automatic, to move ownership: `release` on the
OLD owner, then `claim owner` on the NEW one. Claiming (re)pins THIS hardware's
platform_uuid, so copying host.json to a clone cannot inherit ownership.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))
import hostid
import role as _role
import hostjson as _hj

PATH = _role.host_json_path()
ROLE_ORDER = ("per_host", "singleton_bookkeeping", "singleton_sideeffect")

# host.json read-modify-write now lives in hooks/lib/hostjson.py, shared with
# organism-setup.py (schedules/short/peers). Same atomic whole-dict semantics.
_load = _hj.load
_save = _hj.save
_ensure = _hj.ensure


def _c():
    """Conventional gate: terminal or CLICOLOR_FORCE=1, never NO_COLOR; off = empty."""
    on = (os.environ.get("CLICOLOR_FORCE") == "1"
          or (sys.stdout.isatty() and not os.environ.get("NO_COLOR")))
    k = ("B", "D", "G", "R", "C", "X")
    v = ("\033[1m", "\033[2m", "\033[32m", "\033[31m", "\033[36m", "\033[0m")
    return dict(zip(k, v)) if on else dict.fromkeys(k, "")


def status():
    C = _c()
    cfg = _ensure(_load())
    pinned, live = cfg.get("platform_uuid", ""), hostid.platform_uuid()
    match = (C["G"] + "✓ matches this hardware" + C["X"] if pinned == live
             else C["R"] + "✗ MISMATCH (clone?) singletons auto-demoted" + C["X"])
    print(f"{C['D']}device :{C['X']} {C['B']}{hostid.host_label()}{C['X']}")
    print(f"{C['D']}host_id:{C['X']} {C['C']}{cfg.get('host_id')}{C['X']}")
    print(f"{C['D']}uuid   :{C['X']} {pinned[:13]}…  {match}")
    print(f"{C['D']}roles  :{C['X']}")
    for r in ROLE_ORDER:
        run = _role.should_run(r)
        runtxt = (C["G"] + "runs here" + C["X"]) if run else (C["D"] + "not here" + C["X"])
        decl = "on " if cfg["roles"].get(r) else "off"
        print(f"  {r:24} {C['D']}declared={decl}{C['X']}  →  {runtxt}")
    print(f"\n{C['D']}host.json:{C['X']} {C['C']}{PATH}{C['X']}"
          f"{'' if os.path.exists(PATH) else '  (does not exist yet: per_host defaults apply)'}")


def init():
    if os.path.exists(PATH):
        print(f"host.json already exists at {PATH}; use status / claim / release.")
        return
    _save(_ensure({}))
    print(f"created {PATH}: per_host only (safe default; this device runs no singletons).\n")
    status()


def claim(what):
    cfg = _ensure(_load())
    cfg["platform_uuid"] = hostid.platform_uuid()   # (re)pin THIS hardware
    cfg["host_id"] = hostid.host_id()
    grant = {"owner": ("singleton_bookkeeping", "singleton_sideeffect"),
             "bookkeeping": ("singleton_bookkeeping",), "singleton_bookkeeping": ("singleton_bookkeeping",),
             "sideeffect": ("singleton_sideeffect",), "singleton_sideeffect": ("singleton_sideeffect",)}.get(what)
    if grant is None:
        print(f"unknown role '{what}'; use: owner | bookkeeping | sideeffect"); sys.exit(2)
    for r in grant:
        cfg["roles"][r] = True
    cfg["roles"]["per_host"] = True
    _save(cfg)
    print(f"claimed '{what}' on {hostid.host_label()}.")
    print("⚠️  Ensure NO OTHER device holds the same singleton role (release it there first), else they")
    print("    will double-run. Then reload this device's launchd agents to apply.\n")
    status()


def release():
    cfg = _ensure(_load())
    cfg["roles"]["singleton_bookkeeping"] = False
    cfg["roles"]["singleton_sideeffect"] = False
    cfg["roles"]["per_host"] = True
    _save(cfg)
    print(f"released all singleton roles on {hostid.host_label()}: per_host only now.")
    print("Another device can now `claim owner` safely. Reload launchd agents to stop the singletons here.\n")
    status()


def main():
    argv = sys.argv[1:]
    cmd = argv[0] if argv else "status"
    if cmd == "status":
        status()
    elif cmd == "init":
        init()
    elif cmd == "claim":
        claim(argv[1] if len(argv) > 1 else "owner")
    elif cmd == "release":
        release()
    else:
        print(__doc__); sys.exit(2)


if __name__ == "__main__":
    main()
