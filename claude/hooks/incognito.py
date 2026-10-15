#!/usr/bin/env python3
"""Per-session incognito for the Obsidian session mirror.

Scoped to THIS session only, via $CLAUDE_CODE_SESSION_ID: turning incognito on
in one session never affects any other concurrent session. While on, this
session's turns are not mirrored to _claude-live; the on→off span is sealed as an
excluded window so those turns stay out even after the session file regenerates
from the full transcript. Only the mirror is affected: the raw transcript, the
reconciler, and /handoff are untouched.

`purge` is the retroactive scrub: it seals a window covering the whole session
so far ([0, now]) AND goes active, so every turn (past and future) stays out
even across a full --backfill, then deletes this session's live mirror artifacts
(sessions/<id>.md, its .sessions.json entry, its latest.md row). It is the
reusable form of "wipe this session from the mirror as if it never recorded";
plain `on` stays forward-only.

State: ~/.claude/state/incognito.json
  { "sessions": { "<session_id>": {"active": bool, "since_ms": int|null,
                                   "windows": [[start_ms, end_ms], ...]} } }

Usage: incognito.py [on|off|toggle|purge|status]   (default: status)
"""
import sys, os, json, time, fcntl, glob

STATE = os.path.expanduser("~/.claude/state/incognito.json")
SID = os.environ.get("CLAUDE_CODE_SESSION_ID") or ""


def _load():
    try:
        with open(STATE, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        d = {}
    d.setdefault("sessions", {})
    return d


def _save(d):
    # prune no-op records (inactive + no sealed windows) so the file stays small;
    # a record with sealed windows is kept, those windows still gate that
    # session's future regenerations.
    d["sessions"] = {k: v for k, v in d.get("sessions", {}).items()
                     if v.get("active") or v.get("windows")}
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=1)
    os.replace(tmp, STATE)


def _now_ms():
    return int(time.time() * 1000)


def _hhmm(ms):
    return time.strftime("%H:%M", time.localtime((ms or 0) / 1000))


def _rec(d):
    return d["sessions"].setdefault(SID, {"active": False, "since_ms": None, "windows": []})


def _on(d):
    r = _rec(d)
    if not r.get("active"):
        r["active"] = True
        r["since_ms"] = _now_ms()
    _save(d)
    return ("🕶️  incognito ON (this session only): turns from here on are NOT mirrored to "
            "_claude-live; the raw transcript still exists. Run `/incognito off` to resume.")


def _off(d):
    r = _rec(d)
    if r.get("active"):
        r.setdefault("windows", []).append([r.get("since_ms") or _now_ms(), _now_ms()])
        r["active"] = False
        r["since_ms"] = None
        _save(d)
        return f"✅ incognito OFF (this session): mirroring resumed; those turns stay excluded (window #{len(r['windows'])} sealed)."
    return "already recording (incognito was off for this session): nothing to do."


def _status(d):
    r = d["sessions"].get(SID, {})
    others = sum(1 for k, v in d["sessions"].items() if k != SID and v.get("active"))
    tail = f" · ⚠️ {others} OTHER session(s) currently incognito" if others else ""
    nwin = len(r.get("windows") or [])
    if r.get("active"):
        return f"🕶️  incognito ON (this session) since {_hhmm(r.get('since_ms'))} · {nwin} sealed window(s){tail}."
    return f"● recording (incognito off, this session) · {nwin} sealed window(s){tail}."


# ── purge: retroactively scrub THIS session from the mirror ──────────────────
# The durable part lives in the incognito state (the [0, now] window, sealed by
# _purge); the live cleanup below removes what was already written and is fully
# self-healing, the obsidian-mirror.py renderer is the single source of truth,
# imported and reused rather than reimplemented.

def _mirror_mod():
    """Import obsidian-mirror.py as a module (its name has a hyphen). The script
    has a clean `if __name__ == '__main__'` guard, so import has no side effects.
    We reuse its VAULT/SESS/INDEX/LOCK paths and its renderer."""
    import importlib.util
    # the sibling file (deployed, ~/.claude/hooks is a link to this same directory),
    # so a test of a checkout exercises the checkout's renderer, not the live one
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "obsidian-mirror.py")
    spec = importlib.util.spec_from_file_location("obsidian_mirror", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _regen_latest(mod, index):
    """Rewrite latest.md from `index` (SID already removed), so the purged session
    can never linger in the top 'Latest reply' block.

    Features the newest REMAINING session this machine can re-render from its own
    transcript. When there is none (the purged session was the only one here, or the
    rest came from other machines and have no transcript on this one), latest.md
    keeps just the sessions table, with no featured reply. If even that fails, the
    file is removed: the next reply in any session writes it again. (The old fallback
    stripped only lines holding the full session id, and the featured block shows the
    short id, so the purged prompt and reply stayed on screen; the re-render itself
    always failed, unpacking four values from a five-value tuple.)"""
    latest_p = os.path.join(mod.VAULT, "latest.md")
    try:
        mod.merge_session_files(index)  # keep other machines' rows, as the hook does
    except Exception:
        pass
    ledger = mod._load_incognito()
    for row in sorted(index.values(), key=lambda v: v.get("epoch", 0), reverse=True):
        if ledger is None:
            break  # fail-CLOSED: a corrupt ledger must not re-render
        try:
            tpath = glob.glob(os.path.join(mod.TRANSCRIPTS, "*", f"{row['id']}.jsonl"))[0]
        except (IndexError, KeyError):
            continue  # no transcript on this machine (another machine's session)
        try:
            entries = mod.load_entries(tpath)
            # an opted-out project is never featured, whatever its row still says
            if mod.mirror.notes_excluded(mod.start_dir(entries) or row.get("cwd", "")):
                continue
            incog = (ledger.get("sessions") or {}).get(row["id"], {})
            _body, _n, last = mod.render_session_file(
                entries, row.get("short", row["id"][:8]),
                row.get("project", "-"), incog)
            if not last:
                continue  # every turn of it is excluded too
            ts, human, reply, _gist, model = last
            mod.atomic_write(latest_p, mod.render_dashboard(index, {
                **row, "ts": ts, "human": human, "reply": reply, "model": model}))
            return
        except Exception:
            continue
    try:
        board = mod.render_dashboard(index, {"project": "", "short": "", "ts": "",
                                             "human": "", "reply": ""})
        table = board[board.index("## 🗂 All sessions"):]
        mod.atomic_write(latest_p, table)
    except Exception:
        try:
            os.remove(latest_p)
        except FileNotFoundError:
            pass


def _scrub_mirror():
    """Remove THIS session's live mirror artifacts. Returns (file_removed,
    index_removed). The shared index + latest.md are touched under the same
    file lock obsidian-mirror.py uses, so concurrent sessions can't corrupt it."""
    mod = _mirror_mod()
    if not mod.VAULT:
        return False, False  # no vault configured: nothing was ever saved
    file_removed = False
    try:
        os.remove(os.path.join(mod.SESS, f"{SID}.md"))
        file_removed = True
    except FileNotFoundError:
        pass
    index_removed = False
    with open(mod.LOCK, "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            index = mod.load_index()
            index_removed = index.pop(SID, None) is not None
            # atomic: same tmp+replace convention as the mirror's own writes
            mod.atomic_write(mod.INDEX, json.dumps(index))
            _regen_latest(mod, index)
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)
    return file_removed, index_removed


def _purge(d):
    r = _rec(d)
    now = _now_ms()
    # Durable retroactive exclusion: a window over everything up to now (survives
    # --backfill), plus go active so turns from here on aren't mirrored either.
    r.setdefault("windows", []).append([0, now])
    r["active"] = True
    r["since_ms"] = now
    _save(d)
    try:
        file_removed, index_removed = _scrub_mirror()
    except Exception as e:
        return ("🧹 incognito PURGE (this session): all turns sealed up to now and incognito is "
                f"ON, but live cleanup hit an error ({e!r}); it self-heals on the next mirror write.")
    bits = ("session file removed" if file_removed else "no session file",
            "index entry removed" if index_removed else "not in index")
    return ("🧹 incognito PURGE (this session): wiped from the mirror ("
            + ", ".join(bits) + "; latest.md regenerated). Every turn is sealed up to now and "
            "incognito is ON, so it stays gone even across a backfill. Run `/incognito off` to resume.")


def main():
    arg = (sys.argv[1] if len(sys.argv) > 1 else "status").strip().lower()
    d = _load()
    if arg in ("", "status"):
        print(_status(d)); return
    if not SID:
        print("incognito: no session id ($CLAUDE_CODE_SESSION_ID unset): cannot scope on/off here.")
        return
    # Hold a lock from read to write: two sessions switching at once would
    # otherwise each save their own copy and one record would be lost.
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    with open(STATE + ".lock", "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            _change(arg, _load())
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


def _change(arg, d):
    if arg in ("on", "start", "enable"):
        print(_on(d))
    elif arg in ("off", "stop", "disable", "resume"):
        print(_off(d))
    elif arg == "toggle":
        print(_off(d) if _rec(d).get("active") else _on(d))
    elif arg in ("purge", "scrub", "wipe"):
        print(_purge(d))
    else:
        print(_status(d))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"incognito: error {e!r}")
    sys.exit(0)
