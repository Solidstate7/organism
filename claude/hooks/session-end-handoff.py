#!/usr/bin/env python3
"""SessionEnd hook, fast half: hand the payload to a detached worker and return.

Claude Code gives a SessionEnd hook about 1.5 s, and a plugin's own timeout can't
raise that, so the slow work (a reconcile trigger and a final mirror pass) runs in
session-end-worker.py, started in its own session so neither the hook's cancellation
nor a closing terminal stops it. The payload goes to the worker on stdin.

It never writes HANDOFF.md. The "where we are" note has one writer, /handoff. (The
file name predates that rule and stays, so existing settings entries keep working.)

Reads SessionEnd JSON on stdin (transcript_path, session_id, cwd, reason).
Always exits 0; never raises.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
HOME = os.environ.get("HOME") or os.path.expanduser("~")


def log_file():
    """~/.claude/logs/session-end.log, opened for append; None when it can't be."""
    try:
        logdir = os.path.join(HOME, ".claude", "logs")
        os.makedirs(logdir, exist_ok=True)
        return open(os.path.join(logdir, "session-end.log"), "ab")
    except Exception:
        return None


def main():
    raw = sys.stdin.buffer.read()
    log = log_file()
    out = log if log is not None else subprocess.DEVNULL
    p = subprocess.Popen(
        [sys.executable, os.path.join(HERE, "session-end-worker.py")],
        stdin=subprocess.PIPE, stdout=out, stderr=out,
        start_new_session=True, close_fds=True,
    )
    try:
        p.stdin.write(raw)   # a SessionEnd payload is far smaller than a pipe buffer
        p.stdin.close()
    except Exception:
        pass


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
