"""once.first(*key): True the first time a key is seen within TTL seconds, else False.

For a hook that may be wired twice for a moment (settings.json and the plugin) and must
not act twice, such as the SessionStart brief. Stamps are files created with O_EXCL in
$TMPDIR/organism-once-<uid>/ and expire after the TTL. When a stamp can't be written the
answer is True: a doubled brief beats a missing one.
"""
import hashlib
import os
import tempfile
import time

TTL_S = 60


def _dir():
    uid = os.getuid() if hasattr(os, "getuid") else 0
    return os.path.join(tempfile.gettempdir(), f"organism-once-{uid}")


def first(*key, ttl=TTL_S):
    try:
        d = _dir()
        os.makedirs(d, mode=0o700, exist_ok=True)
        name = hashlib.sha256("\0".join(str(k) for k in key).encode()).hexdigest()[:32]
        path = os.path.join(d, name)
        try:
            if time.time() - os.path.getmtime(path) >= ttl:
                os.remove(path)
        except FileNotFoundError:
            pass
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        return True
    except FileExistsError:
        return False
    except Exception:
        return True
