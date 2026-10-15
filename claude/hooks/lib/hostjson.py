#!/usr/bin/env python3
"""hooks/lib/hostjson.py: atomic read-modify-write of the per-device host.json.

Extracted so both host-role.py (roles) and organism-setup.py (schedules / short /
peers) share ONE writer. Whole-dict RMW with setdefault, so any key not being
touched (roles, platform_uuid, schedules, short, peers) survives every save.
Path via role.host_json_path() (honors $CLAUDE_HOST_JSON), so tests redirect freely.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hostid
import role as _role


def path():
    return _role.host_json_path()


def load():
    try:
        with open(path()) as f:
            return json.load(f)
    except Exception:
        return {}


def save(cfg):
    p = path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, p)


def ensure(cfg):
    """Fill only the minimal scaffold (id, uuid, roles) with setdefault, never
    clobbers an existing value, so roles/platform_uuid/schedules/short/peers all survive."""
    cfg.setdefault("host_id", hostid.host_id())
    cfg.setdefault("platform_uuid", hostid.platform_uuid())
    roles = cfg.setdefault("roles", {})
    roles.setdefault("per_host", True)
    roles.setdefault("singleton_bookkeeping", False)
    roles.setdefault("singleton_sideeffect", False)
    return cfg


def merge(updates):
    """Load → ensure → shallow-merge top-level `updates` → save. Returns the saved dict.
    Used for schedules/short/peers; roles are managed by host-role.py, untouched here."""
    cfg = ensure(load())
    for k, v in updates.items():
        cfg[k] = v
    save(cfg)
    return cfg
