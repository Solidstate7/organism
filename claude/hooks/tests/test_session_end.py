#!/usr/bin/env python3
"""tests for the SessionEnd split: session-end-handoff.py (launcher) returns fast and
hands the payload to a detached worker; session-end-worker.py takes a per-session lock
and runs the final mirror pass.
  - the launcher returns in well under a second while a stub worker sleeps for 5 s
  - the stub worker gets the payload on stdin and outlives the launcher's process group
  - with no writable ~/.claude/logs the launcher still starts the worker
  - the real worker runs one final pass, logs it, and skips when the lock is held
Every case uses a temp HOME. Run:  python3 claude/hooks/tests/test_session_end.py"""
import fcntl
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time

HOOKS = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
LAUNCHER = os.path.join(HOOKS, "session-end-handoff.py")
WORKER = os.path.join(HOOKS, "session-end-worker.py")

_fails = []
_tmp = []


def check(name, cond):
    print(("  ✓ " if cond else "  ✗ ") + name)
    if not cond:
        _fails.append(name)


def tmp():
    d = os.path.realpath(tempfile.mkdtemp(prefix="sessend-"))
    _tmp.append(d)
    return d


def wait_for(path, secs=10.0, text=None):
    end = time.monotonic() + secs
    while time.monotonic() < end:
        if os.path.exists(path) and (text is None or text in open(path).read()):
            return True
        time.sleep(0.05)
    return False


STUB = '''import sys, time, os
data = sys.stdin.read()
time.sleep(float(os.environ.get("STUB_SLEEP", "5")))
out = os.environ["STUB_OUT"]
open(out + ".tmp", "w").write(data)
os.replace(out + ".tmp", out)   # atomic: the file never exists half-written
'''


def stub_dir():
    """A hooks dir holding the real launcher next to a slow stub worker."""
    d = tmp()
    shutil.copy(LAUNCHER, os.path.join(d, "session-end-handoff.py"))
    with open(os.path.join(d, "session-end-worker.py"), "w") as f:
        f.write(STUB)
    return d


PAYLOAD = json.dumps({"session_id": "abc12345-0000", "transcript_path": "", "cwd": "/", "reason": "exit"})

print("session-end: launcher")
d = stub_dir()
home = tmp()
out = os.path.join(home, "stub-out.json")
env = dict(os.environ, HOME=home, STUB_OUT=out, STUB_SLEEP="5")
t0 = time.monotonic()
r = subprocess.run([sys.executable, os.path.join(d, "session-end-handoff.py")], input=PAYLOAD,
                   text=True, env=env, capture_output=True)
took = time.monotonic() - t0
# Claude Code gives a SessionEnd hook about 1.5 s; the point is that the launcher does not
# wait for the 5 s worker. A slow CI runner needs a few hundred ms just to start python.
check(f"returns in under a second while the worker sleeps 5 s ({took * 1000:.0f} ms)", took < 1.0)
check("exits 0", r.returncode == 0)
check("creates ~/.claude/logs", os.path.isdir(os.path.join(home, ".claude", "logs")))
check("the worker gets the payload on stdin", wait_for(out, 15) and open(out).read() == PAYLOAD)

print("session-end: the worker outlives the hook's process group")
home = tmp()
out = os.path.join(home, "stub-out.json")
env = dict(os.environ, HOME=home, STUB_OUT=out, STUB_SLEEP="1")
p = subprocess.Popen([sys.executable, os.path.join(d, "session-end-handoff.py")], stdin=subprocess.PIPE,
                     env=env, start_new_session=True)
pgid = os.getpgid(p.pid)
p.stdin.write(PAYLOAD.encode())
p.stdin.close()
p.wait()
try:
    os.killpg(pgid, signal.SIGKILL)   # what a cancelled hook or a closed terminal does
except ProcessLookupError:
    pass
check("the worker still finishes", wait_for(out, 10))

print("session-end: no writable logs folder")
home = tmp()
with open(os.path.join(home, ".claude"), "w") as f:   # ~/.claude is a file: makedirs fails
    f.write("x")
out = os.path.join(home, "stub-out.json")
env = dict(os.environ, HOME=home, STUB_OUT=out, STUB_SLEEP="0")
r = subprocess.run([sys.executable, os.path.join(d, "session-end-handoff.py")], input=PAYLOAD,
                   text=True, env=env, capture_output=True)
check("exits 0", r.returncode == 0)
check("the worker still runs", wait_for(out, 10))

print("session-end: the real worker")
home = tmp()
transcript = os.path.join(home, "t.jsonl")
with open(transcript, "w") as f:
    f.write(json.dumps({"type": "user", "timestamp": "2026-09-30T00:00:00Z",
                        "message": {"role": "user", "content": "hello"}}) + "\n")
payload = json.dumps({"session_id": "feedbeef-1111", "transcript_path": transcript,
                      "cwd": home, "reason": "exit"})
env = dict(os.environ, HOME=home)
log = os.path.join(home, ".claude", "logs", "session-end.log")
subprocess.run([sys.executable, LAUNCHER], input=payload, text=True, env=env, capture_output=True)
check("logs one final mirror pass", wait_for(log, 15, "final mirror pass done"))
lockdir = os.path.join(home, ".claude", "state", "locks")
check("the lock file is per session", os.path.exists(os.path.join(lockdir, "session-end-feedbeef-1111.lock")))
fd = os.open(os.path.join(lockdir, "session-end-feedbeef-1111.lock"), os.O_RDWR)
fcntl.flock(fd, fcntl.LOCK_EX)
r = subprocess.run([sys.executable, WORKER], input=payload, text=True, env=env, capture_output=True,
                   timeout=30)
os.close(fd)
check("a second worker for the same session skips while the lock is held", "skipped" in r.stdout
      and "final mirror pass done" not in r.stdout)
odd = json.dumps({"session_id": "../../etc/x", "transcript_path": "", "cwd": home})
subprocess.run([sys.executable, WORKER], input=odd, text=True, env=env, capture_output=True, timeout=30)
check("a session id can't steer the lock path", not os.path.exists(os.path.join(home, ".claude", "etc"))
      and any(n.startswith("session-end-.._.._etc_x") for n in os.listdir(lockdir)))
stale = os.path.join(lockdir, "session-end-old-session.lock")
open(stale, "w").close()
os.utime(stale, (time.time() - 2 * 86400,) * 2)
fd = os.open(os.path.join(lockdir, "session-end-unknown.lock"), os.O_RDWR | os.O_CREAT)
fcntl.flock(fd, fcntl.LOCK_EX)
blank = json.dumps({"session_id": "", "transcript_path": transcript, "cwd": home})
r = subprocess.run([sys.executable, WORKER], input=blank, text=True, env=env, capture_output=True,
                   timeout=30)
os.close(fd)
check("a payload with no session id is not skipped by another session's lock",
      "final mirror pass done" in r.stdout)
subprocess.run([sys.executable, WORKER], input=payload, text=True, env=env, capture_output=True,
               timeout=30)
check("lock files older than a day are deleted", not os.path.exists(stale)
      and os.path.exists(os.path.join(lockdir, "session-end-feedbeef-1111.lock")))

for p in _tmp:
    shutil.rmtree(p, ignore_errors=True)
if _fails:
    print(f"FAIL: {len(_fails)} check(s)")
    sys.exit(1)
print("ALL PASS")
