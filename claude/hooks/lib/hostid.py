#!/usr/bin/env python3
"""hooks/lib/hostid.py: canonical machine identity for the harness.

host_id()    : stable, hardware-derived short id (from IOPlatformUUID). Migration
               Assistant copies ComputerName + files but NOT the hardware UUID, so a
               CLONE gets a different id, ownership can't be inherited by copying a file.
host_label() : "<ComputerName> (<hw.model>)" for display.
host_short() : friendly short name ('mini' for a Mac mini, 'book' for a MacBook, …).

Import it (`import hostid`) or run it: hostid.py id|label|short
"""
import subprocess
import sys
import os
import json


def _run(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        return ""


def host_json_path():
    """Canonical host.json location, honors the documented $CLAUDE_HOST_JSON override
    (tests / setup redirection), same resolution as role.host_json_path() and
    hostjson.path(), so a redirected host.json gives one identity (short/peers)
    and one role gate within a process. (role.py imports this module, so the resolver lives HERE to stay
    circular-import-free; role.host_json_path() remains for its existing callers.)"""
    return os.environ.get("CLAUDE_HOST_JSON") or os.path.join(
        os.environ.get("HOME") or os.path.expanduser("~"), ".claude", "state", "host.json")


def platform_uuid():
    """Full IOPlatformUUID (uppercase, as ioreg reports it), pins device ownership."""
    out = _run(["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"])
    for line in out.splitlines():
        if "IOPlatformUUID" in line:
            parts = line.split('"')
            if len(parts) >= 4:
                return parts[3]
    return ""


def host_id():
    uuid = platform_uuid() or _run(["hostname"])
    hexed = "".join(c for c in uuid.lower() if c in "0123456789abcdef")
    return hexed[:12] or "unknown"


def host_label():
    cn = _run(["scutil", "--get", "ComputerName"]) or _run(["hostname", "-s"]) or "Mac"
    model = _run(["sysctl", "-n", "hw.model"]) or "Mac"
    return f"{cn} ({model})"


def host_short():
    """Friendly short routing name for this machine (e.g. 'mini', 'book'), for
    per-host commands and labels. Set by the user in ~/.claude/state/host.json
    {"short": "..."} (authoritative); else derived from the ComputerName; else the
    hardware-id prefix so it's always unique."""
    try:
        with open(host_json_path(), encoding="utf-8") as f:
            s = (json.load(f).get("short") or "").strip().lower()
        if s:
            return s
    except Exception:
        pass
    cn = (_run(["scutil", "--get", "ComputerName"]) or _run(["hostname", "-s"]) or "").lower()
    if "mini" in cn:
        return "mini"
    if "book" in cn or "air" in cn:
        return "book"
    return host_id()[:6]


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else "label"
    print({"id": host_id, "uuid": platform_uuid, "label": host_label,
           "short": host_short}.get(arg, host_label)())
