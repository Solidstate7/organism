#!/usr/bin/env python3
"""SessionEnd worker, slow half (started detached by session-end-handoff.py).

1. Takes a per-session lock (~/.claude/state/locks/session-end-<session>.lock), so a
   session wired twice for a moment (settings and plugin) gets one final pass. Lock
   files older than a day are deleted; a payload with no session id runs unlocked.
2. Waits up to 5 s for the transcript to stop growing: the last assistant message can
   still be flushing when SessionEnd fires.
3. If the session ran inside a paired repo, starts the doc check for that pair
   (detached; its expensive probes only re-run when HEAD changed).
4. Runs the session mirror once more, so the session note records the real ending:
   the Stop hook's run can race the final message, and no later Stop heals it.

A 60 s alarm caps the whole run. Reads the SessionEnd JSON on stdin. Exits 0.
"""
import fcntl
import json
import os
import re
import signal
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(HERE, "lib"))
import where  # noqa: E402

CAP_S = 60
SETTLE_MAX_S = float(os.environ.get("ORGANISM_SETTLE_MAX_S", "5"))
SETTLE_POLL_S = 0.5


def log(msg):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), msg, flush=True)


def git_toplevel(cwd):
    try:
        out = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=cwd,
                             capture_output=True, text=True, timeout=5)
        return out.stdout.strip() if out.returncode == 0 else None
    except Exception:
        return None


LOCK_MAX_AGE_S = 86400


def prune_locks(d):
    """Delete session-end lock files older than a day; their workers are long gone."""
    cutoff = time.time() - LOCK_MAX_AGE_S
    try:
        names = os.listdir(d)
    except OSError:
        return
    for n in names:
        if not (n.startswith("session-end-") and n.endswith(".lock")):
            continue
        p = os.path.join(d, n)
        try:
            if os.path.getmtime(p) < cutoff:
                os.unlink(p)
        except OSError:
            pass


def lock(session_id):
    """An exclusive lock for this session, or None when another worker holds it.

    Without a session id there is nothing to tell sessions apart by, so the pass
    runs unlocked rather than letting unrelated sessions skip each other.
    """
    if not session_id:
        return -1
    name = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id)[:120]
    d = os.path.join(where.state_dir(), "locks")
    try:
        os.makedirs(d, exist_ok=True)
        prune_locks(d)
        fd = os.open(os.path.join(d, f"session-end-{name}.lock"), os.O_RDWR | os.O_CREAT, 0o600)
    except Exception:
        return -1   # can't lock: run anyway, a doubled pass is harmless
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except OSError:
        os.close(fd)
        return None


def settle(path):
    """Wait until the transcript's size holds still for one poll, at most SETTLE_MAX_S."""
    if not path:
        return
    deadline = time.monotonic() + SETTLE_MAX_S
    last = -1
    while time.monotonic() < deadline:
        try:
            size = os.path.getsize(path)
        except OSError:
            return
        if size == last:
            return
        last = size
        time.sleep(SETTLE_POLL_S)


def maybe_reconcile(cwd):
    cfg_path = where.pairings()
    if not cfg_path:
        return
    with open(cfg_path, encoding="utf-8") as f:
        cfg = json.load(f)
    root = os.path.realpath(git_toplevel(cwd) or cwd)
    for pair in cfg.get("pairs", []):
        if os.path.realpath(pair["repo"]) == root:
            os.makedirs(where.logs_dir(), exist_ok=True)
            with open(os.path.join(where.logs_dir(), "reconcile.log"), "a") as logf:
                subprocess.Popen(
                    [sys.executable, os.path.join(HERE, "reconcile.py"), "--pair", pair["id"]],
                    stdout=logf, stderr=subprocess.STDOUT, start_new_session=True,
                )
            log(f"reconcile started for pair {pair['id']}")
            return


def mirror(data):
    subprocess.run(
        [sys.executable, os.path.join(HERE, "obsidian-mirror.py")],
        input=json.dumps(data), text=True, timeout=CAP_S - 10,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def main():
    data = json.loads(sys.stdin.read() or "{}")
    sid = data.get("session_id") or ""
    fd = lock(sid)
    if fd is None:
        log(f"session {sid[:8]}: another final pass holds the lock; skipped")
        return
    signal.alarm(CAP_S)
    settle(data.get("transcript_path"))
    try:
        maybe_reconcile(data.get("cwd") or os.getcwd())
    except Exception as e:
        log(f"reconcile trigger failed: {e!r}")
    try:
        mirror(data)
        log(f"session {sid[:8]}: final mirror pass done")
    except Exception as e:
        log(f"final mirror pass failed: {e!r}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"worker failed: {e!r}")
    sys.exit(0)
