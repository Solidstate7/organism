#!/usr/bin/env python3
"""hooks/lib/jobstatus.py: the per-job success marker, ONE implementation.

~/.claude/state/<job>-last-success holds the ISO date (YYYY-MM-DD) of the job's last
successful run; each post-noon --fallback agent skips iff it reads today. The protocol
was implemented three times and two copies inverted the gate, marking success on a
failed/degraded run permanently disarms the fallback.

Contract for consumers: call mark_success ONLY when the run genuinely succeeded (LLM
stage produced output AND delivery was confirmed), a pushed deterministic fallback
digest is NOT success, it must leave the marker unarmed so the fallback agent retries
on post-reset tokens.

Dependency-free; never raises.
"""
import datetime
import os


def _marker(job):
    home = os.environ.get("HOME") or os.path.expanduser("~")
    return os.path.join(home, ".claude", "state", f"{job}-last-success")


def mark_success(job):
    """Write today's ISO date to the marker, byte-compatible with what the reviews
    wrote before extraction (same path, same content), so existing --fallback gates
    and any user tooling keep working. Returns True iff written."""
    try:
        p = _marker(job)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(datetime.date.today().isoformat())
        return True
    except Exception:
        return False


def last_success(job):
    """Marker content, stripped (a marker written by `date +%F` has a trailing newline), or
    None when the marker is absent/unreadable. Compare against
    datetime.date.today().isoformat() for the fallback skip."""
    try:
        with open(_marker(job), encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return None
