#!/usr/bin/env python3
"""hooks/lib/lockrun.py: flock-with-deadline exec wrapper for shell callers.
macOS ships no flock(1) binary; this is the substitute the scheduled
shell jobs use to serialise heavy `claude -p` runs (the Monday
morning chain of reviews contends on one subscription).

    python3 lockrun.py <lockfile> <wait_seconds> -- <cmd> [args...]

Semantics (mirrors lib/llm.llm_lock): poll an exclusive flock up to <wait_seconds>;
on timeout (or any lock-plumbing error) print a warning to stderr and run the
command ANYWAY (a scheduled job must degrade to unserialised, never deadlock). The
command is exec'd, so its exit code IS this process's exit code, and the held flock
fd is made inheritable, Python opens fds close-on-exec by default (PEP 446), which
would silently release the lock at exec and defeat the whole serialisation.
"""
import fcntl
import os
import sys
import time

POLL_S = 5
USAGE = "usage: lockrun.py <lockfile> <wait_seconds> -- <cmd> [args...]\n"


def main(argv):
    try:
        i = argv.index("--")
    except ValueError:
        sys.stderr.write(USAGE)
        return 2
    head, cmd = argv[:i], argv[i + 1:]
    if len(head) != 2 or not cmd:
        sys.stderr.write(USAGE)
        return 2
    lockfile = head[0]
    try:
        wait_s = float(head[1])
    except ValueError:
        sys.stderr.write(USAGE)
        return 2

    fd, got = None, False
    try:
        parent = os.path.dirname(os.path.abspath(lockfile))
        os.makedirs(parent, exist_ok=True)
        fd = os.open(lockfile, os.O_CREAT | os.O_RDWR, 0o644)
        deadline = time.monotonic() + wait_s
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                got = True
                break
            except OSError:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    sys.stderr.write(
                        f"lockrun: {lockfile} still held after {wait_s:g}s: "
                        "running unserialised\n")
                    break
                time.sleep(min(POLL_S, max(remaining, 0.05)))
    except Exception as e:
        # Lock plumbing must never block the job it protects.
        sys.stderr.write(f"lockrun: lock unavailable ({e}): running unserialised\n")

    if fd is not None:
        if got:
            os.set_inheritable(fd, True)   # hold the flock across exec (PEP 446)
        else:
            try:
                os.close(fd)               # not held, don't leak the fd to the child
            except Exception:
                pass
    try:
        os.execvp(cmd[0], cmd)             # exit code propagates by replacement
    except OSError as e:
        sys.stderr.write(f"lockrun: exec {cmd[0]}: {e}\n")
        return 127


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
