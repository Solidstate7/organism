#!/usr/bin/env python3
"""tests for hooks/lib/jobstatus.py: the success-marker round-trip and the
BYTE format's stability (bare ISO date, no trailing newline: the --fallback agents and
any user tooling compare it against `date.today().isoformat()` verbatim, and
a marker written by `date +%F`, with its trailing newline, must stay read-compatible).
HOME is redirected to a temp dir before import so the real ~/.claude/state markers
(live fallback gates for scheduled jobs) are never touched.
Run:  python3 claude/hooks/tests/test_jobstatus.py"""
import datetime
import os
import shutil
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix="jobstatus-test-")
os.environ["HOME"] = TMP    # before import; jobstatus reads $HOME per call anyway, belt and braces

HOOKS = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, os.path.join(HOOKS, "lib"))
import jobstatus

_fails = []


def check(name, cond):
    print(("  ✓ " if cond else "  ✗ ") + name)
    if not cond:
        _fails.append(name)


def marker(job):
    return os.path.join(TMP, ".claude", "state", f"{job}-last-success")


def test_roundtrip_and_format():
    print("mark_success / last_success round-trip + marker byte format")
    today = datetime.date.today().isoformat()
    check("mark_success returns True", jobstatus.mark_success("t-job") is True)
    check("marker lands at state/<job>-last-success", os.path.isfile(marker("t-job")))
    with open(marker("t-job"), "rb") as f:
        raw = f.read()
    check("content is the bare ISO date, NO trailing newline (byte-stable format)",
          raw == today.encode("utf-8"))
    check("last_success round-trips", jobstatus.last_success("t-job") == today)
    check("fallback-skip comparison holds",
          jobstatus.last_success("t-job") == datetime.date.today().isoformat())


def test_newline_variant_and_absent():
    print("legacy newline variant + absent/unreadable markers")
    os.makedirs(os.path.dirname(marker("nl-job")), exist_ok=True)
    with open(marker("nl-job"), "w", encoding="utf-8") as f:
        f.write("2026-06-30\n")      # `date +%F` writes a trailing newline
    check("newline variant read-compatible (stripped)",
          jobstatus.last_success("nl-job") == "2026-06-30")
    check("absent marker → None", jobstatus.last_success("never-ran") is None)
    if os.geteuid() != 0:            # root ignores mode bits, skip there (CI containers)
        with open(marker("locked"), "w") as f:
            f.write("2026-01-01")
        os.chmod(marker("locked"), 0)
        check("unreadable marker → None, never raises", jobstatus.last_success("locked") is None)
        os.chmod(marker("locked"), 0o644)


def test_never_raises():
    print("mark_success never raises (contract: dependency-free, best-effort)")
    old = os.environ["HOME"]
    os.environ["HOME"] = os.path.join(TMP, "no", "such", "parent")
    try:
        # os.makedirs will succeed even here; force failure with a FILE where the dir goes
        os.environ["HOME"] = marker("t-job")   # HOME points at a plain file → makedirs fails
        check("returns False on failure instead of raising",
              jobstatus.mark_success("x") is False)
    finally:
        os.environ["HOME"] = old


if __name__ == "__main__":
    try:
        test_roundtrip_and_format()
        test_newline_variant_and_absent()
        test_never_raises()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    print(f"\n{'ALL PASS' if not _fails else 'FAILED: ' + ', '.join(_fails)}")
    sys.exit(1 if _fails else 0)
