#!/usr/bin/env python3
"""tests for where session notes land and what /incognito purge leaves behind. Every
case runs the real hooks (obsidian-mirror.py, incognito.py) in a subprocess with HOME
pointed at a temp dir, so the live ~/.claude and vault are never touched.

  - notes follow the vault (CLAUDE_VAULT_ROOT alone is enough); no vault, no notes
  - purge clears the featured reply in latest.md, whether or not another session can
    take its place, and keeps the sessions table
  - a note deleted on the machine that ran it drops out of every other machine's table
  - secrets in a prompt or reply reach neither the note, latest.md nor the index
  - a project opts out with a .organism-no-notes file or paths.env's CLAUDE_NOTES_EXCLUDE
  - an opted-out session is never featured by a purge, and its row goes with its note
  - a purge that lands during a mirror run is not undone by that run's write
Run:  python3 claude/hooks/tests/test_session_notes.py"""
import datetime
import fcntl
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HOOKS = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
MIRROR = os.path.join(HOOKS, "obsidian-mirror.py")
INCOG = os.path.join(HOOKS, "incognito.py")
_fails, _dirs, _pending = [], [], []
_heldlock = None


def check(name, cond):
    print(("  ✓ " if cond else "  ✗ ") + name)
    if not cond:
        _fails.append(name)


def home(paths_env):
    h = tempfile.mkdtemp(prefix="session-notes-test-")
    _dirs.append(h)
    os.makedirs(os.path.join(h, ".claude", "state"))
    with open(os.path.join(h, ".claude", "state", "paths.env"), "w") as f:
        f.write(paths_env.replace("$TMPHOME", h))
    return h


def env_for(h, sid=None):
    e = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE_", "GIT_"))}
    e["HOME"] = h
    if sid:
        e["CLAUDE_CODE_SESSION_ID"] = sid
    return e


def reply(h, sid, prompt, answer, cwd="/tmp/proj", hook_cwd=None, wait=True):
    """Write a one-turn transcript and run the Stop hook on it. `cwd` is where the
    transcript says the session runs; `hook_cwd` (default: the same) is the hook's."""
    t = os.path.join(h, ".claude", "projects", "-tmp-proj", f"{sid}.jsonl")
    os.makedirs(os.path.dirname(t), exist_ok=True)
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    rows = [{"type": "user", "uuid": "u-" + sid[:4], "timestamp": now, "sessionId": sid, "cwd": cwd,
             "entrypoint": "cli", "message": {"role": "user", "content": prompt}},
            {"type": "assistant", "uuid": "a-" + sid[:4], "timestamp": now, "sessionId": sid, "cwd": cwd,
             "entrypoint": "cli", "message": {"role": "assistant", "model": "claude-opus-5-5",
                                              "content": [{"type": "text", "text": answer}]}}]
    with open(t, "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    payload = json.dumps({"session_id": sid, "transcript_path": t, "cwd": hook_cwd or cwd,
                          "hook_event_name": "Stop"})
    if wait:
        subprocess.run([sys.executable, MIRROR], input=payload, text=True, env=env_for(h), timeout=60)
        return
    # Not waiting: hold the mirror lock first, so the run stops just before its write.
    global _heldlock
    _heldlock = open(os.path.join(h, "vault", "_claude-live", ".mirror.lock"), "w")
    fcntl.flock(_heldlock, fcntl.LOCK_EX)
    p = subprocess.Popen([sys.executable, MIRROR], stdin=subprocess.PIPE, text=True, env=env_for(h))
    p.stdin.write(payload)
    p.stdin.close()
    _pending.append(p)


def purge(h, sid):
    return subprocess.run([sys.executable, INCOG, "purge"], capture_output=True, text=True,
                          env=env_for(h, sid), timeout=60).stdout


def read(p):
    try:
        with open(p, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return ""


A = "aaaaaaaa-1111-4111-8111-111111111111"
B = "bbbbbbbb-2222-4222-8222-222222222222"
C = "cccccccc-3333-4333-8333-333333333333"
NO_NOTES = ".organism-no-notes"

print("notes follow the vault")
h = home('CLAUDE_VAULT_ROOT="$TMPHOME/Documents/My Vault"\n')
reply(h, A, "first prompt alpha", "first reply alpha")
live = os.path.join(h, "Documents", "My Vault", "_claude-live")
check("a vault outside ~/Projects gets the note", os.path.isfile(os.path.join(live, "sessions", f"{A}.md")))
check("…and latest.md", "first reply alpha" in read(os.path.join(live, "latest.md")))
check("no stray ~/Projects folder", not os.path.exists(os.path.join(h, "Projects")))

print("no vault, no notes")
h0 = home("# nothing configured\n")
reply(h0, A, "prompt with nowhere to go", "reply")
written = [os.path.join(r, f) for r, _, fs in os.walk(h0) for f in fs
           if "/.claude/" not in os.path.join(r, f)]
check("nothing is written outside ~/.claude", written == [])

print("purge, when this is the only session on the machine")
purge(h, A)
latest = read(os.path.join(live, "latest.md"))
check("the purged prompt is gone from latest.md", "first prompt alpha" not in latest)
check("the purged reply is gone from latest.md", "first reply alpha" not in latest)
check("the sessions table stays", "All sessions" in latest)
check("the session note is deleted", not os.path.exists(os.path.join(live, "sessions", f"{A}.md")))

print("purge, when another session on this machine can take its place")
h2 = home('CLAUDE_VAULT_ROOT="$TMPHOME/vault"\n')
live2 = os.path.join(h2, "vault", "_claude-live")
reply(h2, B, "older prompt bravo", "older reply bravo")
reply(h2, A, "newer prompt alpha", "newer reply alpha")
check("before: latest.md features the newer session", "newer reply alpha" in read(os.path.join(live2, "latest.md")))
out = purge(h2, A)
latest = read(os.path.join(live2, "latest.md"))
check("after: the purged reply is gone", "newer reply alpha" not in latest and "newer prompt alpha" not in latest)
check("after: the other session's reply is featured", "older reply bravo" in latest)
check("purge reports what it removed", "session file removed" in out)

print("a note deleted on one machine leaves the others' tables")
m1 = home('CLAUDE_VAULT_ROOT="$TMPHOME/vault"\n')   # this machine
m2 = home('CLAUDE_VAULT_ROOT="$TMPHOME/vault"\n')   # the machine that ran session B
reply(m2, B, "prompt run on the other machine", "its reply")
s1 = os.path.join(m1, "vault", "_claude-live", "sessions")
os.makedirs(s1, exist_ok=True)
synced = os.path.join(s1, f"{B}.md")
shutil.copy(os.path.join(m2, "vault", "_claude-live", "sessions", f"{B}.md"), synced)   # "sync"
reply(m1, A, "local prompt one", "local reply one")
board = os.path.join(m1, "vault", "_claude-live", "latest.md")
check("the synced session shows in this machine's table", B in read(board))
os.remove(synced)                                                                       # purged there
reply(m1, A, "local prompt two", "local reply two")
check("once its note is gone, its row goes too", B not in read(board))
check("…and its last prompt with it", "prompt run on the other machine" not in read(board))

print("secrets never reach the notes")
hs = home('CLAUDE_VAULT_ROOT="$TMPHOME/vault"\n')
lives = os.path.join(hs, "vault", "_claude-live")
token = "gh" + "p_" + "A1b2" * 9                 # assembled, so no scanner flags this file
password = "hunter2" + "pass"
reply(hs, A, f"deploy with GITHUB_TOKEN={token} please", f"Done. The URL is postgres://app:{password}@db/x")
note = read(os.path.join(lives, "sessions", f"{A}.md"))
latest = read(os.path.join(lives, "latest.md"))
check("the token in the prompt is not in the session note", token not in note and "[redacted:" in note)
check("the password in the reply is not in it either", password not in note)
check("latest.md shows neither, in the featured turn or the table",
      token not in latest and password not in latest and "[redacted:" in latest)
check("the index the table is built from has no token", token not in read(os.path.join(lives, ".sessions.json")))

print("a project opts out with a .organism-no-notes file")
ho = home('CLAUDE_VAULT_ROOT="$TMPHOME/vault"\n')
liveo = os.path.join(ho, "vault", "_claude-live")
private, public = os.path.join(ho, "dev", "private"), os.path.join(ho, "dev", "public")
os.makedirs(os.path.join(private, "src"))
os.makedirs(public)
open(os.path.join(private, NO_NOTES), "w").close()
reply(ho, B, "public prompt bravo", "public reply bravo", cwd=public)
reply(ho, A, "private prompt alpha", "private reply alpha", cwd=os.path.join(private, "src"))
latest, index = read(os.path.join(liveo, "latest.md")), read(os.path.join(liveo, ".sessions.json"))
check("a session started below the opted-out folder gets no note",
      not os.path.exists(os.path.join(liveo, "sessions", f"{A}.md")))
check("…and no table row", A[:8] not in latest and "private prompt alpha" not in latest)
check("…and no index entry", A not in index)
check("another project is still copied", B[:8] in latest
      and "public reply bravo" in read(os.path.join(liveo, "sessions", f"{B}.md")))
reply(ho, C, "started private, then cd'd out", "reply charlie", cwd=private, hook_cwd=public)
check("the start directory decides, not where the shell is now",
      not os.path.exists(os.path.join(liveo, "sessions", f"{C}.md")))
open(os.path.join(ho, NO_NOTES), "w").close()
reply(ho, B, "public prompt two", "public reply two", cwd=public)
check("a file in the home folder itself does not opt everything out",
      "public reply two" in read(os.path.join(liveo, "sessions", f"{B}.md")))

print("…or with CLAUDE_NOTES_EXCLUDE in paths.env")
hk = home('CLAUDE_VAULT_ROOT="$TMPHOME/vault"\nCLAUDE_NOTES_EXCLUDE="$HOME/clients \'$HOME/My Work\'"\n')
livek = os.path.join(hk, "vault", "_claude-live")
reply(hk, A, "client prompt alpha", "client reply alpha", cwd=os.path.join(hk, "clients", "acme"))
reply(hk, B, "work prompt bravo", "work reply bravo", cwd=os.path.join(hk, "My Work", "x"))
reply(hk, C, "other prompt charlie", "other reply charlie", cwd=os.path.join(hk, "clients2"))
latest = read(os.path.join(livek, "latest.md"))
check("a session inside a listed folder ($HOME expanded) gets no note",
      not os.path.exists(os.path.join(livek, "sessions", f"{A}.md")) and "client prompt alpha" not in latest)
check("a quoted folder with a space in its name works too",
      not os.path.exists(os.path.join(livek, "sessions", f"{B}.md")) and "work prompt bravo" not in latest)
check("a folder whose name only starts the same is still copied", "other reply charlie" in latest)

print("opting out later stops the copy; backfill leaves the old note alone")
hb = home('CLAUDE_VAULT_ROOT="$TMPHOME/vault"\n')
liveb = os.path.join(hb, "vault", "_claude-live")
proj = os.path.join(hb, "dev", "proj")
os.makedirs(proj)
reply(hb, A, "before the opt-out", "first reply", cwd=proj)
notep = os.path.join(liveb, "sessions", f"{A}.md")
before = read(notep)
open(os.path.join(proj, NO_NOTES), "w").close()
reply(hb, A, "after the opt-out", "second reply", cwd=proj)
check("a turn after the opt-out is not added", read(notep) == before and "after the opt-out" not in read(notep))
out = subprocess.run([sys.executable, MIRROR, "--backfill"], capture_output=True, text=True,
                     env=env_for(hb), timeout=60).stdout
check("backfill leaves the opted-out session's note as it was", read(notep) == before)
check("…and says so", "1 in an opted-out project" in out)

print("an opted-out session stays out of latest.md after a purge and after its note goes")
hp = home('CLAUDE_VAULT_ROOT="$TMPHOME/vault"\n')
livep = os.path.join(hp, "vault", "_claude-live")
proj, other = os.path.join(hp, "dev", "proj"), os.path.join(hp, "dev", "other")
os.makedirs(proj)
os.makedirs(other)
reply(hp, A, "before the opt-out", "first reply", cwd=proj)
open(os.path.join(proj, NO_NOTES), "w").close()
reply(hp, A, "PRIVATE prompt after opt-out", "PRIVATE reply after opt-out", cwd=proj)
reply(hp, B, "public prompt bravo", "public reply bravo", cwd=other)
purge(hp, B)
latest = read(os.path.join(livep, "latest.md"))
check("purge in another session does not feature the opted-out one", "PRIVATE" not in latest)
check("…and keeps the table", "All sessions" in latest)
os.remove(os.path.join(livep, "sessions", f"{A}.md"))
reply(hp, C, "public prompt charlie", "public reply charlie", cwd=other)
latest = read(os.path.join(livep, "latest.md"))
check("deleting the opted-out note drops its row", A[:8] not in latest and "before the opt-out" not in latest)
check("…and its index entry", A not in read(os.path.join(livep, ".sessions.json")))
check("the other session's row stays", C[:8] in latest)

print("a late Stop run does not shrink a fuller note")
hl = home('CLAUDE_VAULT_ROOT="$TMPHOME/vault"\n')
notel = os.path.join(hl, "vault", "_claude-live", "sessions", f"{A}.md")
reply(hl, A, "first prompt", "first reply")
tl = os.path.join(hl, ".claude", "projects", "-tmp-proj", f"{A}.jsonl")
stale = os.path.join(hl, "stale.jsonl")
shutil.copy(tl, stale)   # the transcript as an early Stop run saw it
reply(hl, A, "second prompt", "second reply")
full = read(notel)
subprocess.run([sys.executable, MIRROR], text=True, env=env_for(hl), timeout=60,
               input=json.dumps({"session_id": A, "transcript_path": stale, "cwd": "/tmp/proj",
                                 "hook_event_name": "Stop"}))
check("the note keeps its last turn", "second reply" in full and read(notel) == full)

print("a reply that quotes turn headers does not freeze the note")
hq = home('CLAUDE_VAULT_ROOT="$TMPHOME/vault"\n')
noteq = os.path.join(hq, "vault", "_claude-live", "sessions", f"{A}.md")
quoted = "a note looks like this:\n\n### 🕐 2026-01-01 00:00:00\n\nand\n\n### 🕐 2026-01-02 00:00:00\n"
reply(hq, A, "show me a note", quoted)
reply(hq, A, "second prompt", "SECOND TURN reply")
check("the next turn still reaches the note", "SECOND TURN reply" in read(noteq))

print("sessions switching incognito on at once each keep their record")
hi = home("# nothing configured\n")
procs = [subprocess.Popen([sys.executable, INCOG, "on"], env=env_for(hi, f"sess-{i:02d}"),
                          stdout=subprocess.DEVNULL) for i in range(12)]
for p in procs:
    p.wait(timeout=60)
try:
    with open(os.path.join(hi, ".claude", "state", "incognito.json"), encoding="utf-8") as f:
        recs = json.load(f).get("sessions", {})
except Exception:
    recs = {}
check("all 12 sessions are incognito", sorted(recs) == [f"sess-{i:02d}" for i in range(12)])

print("a purge during a mirror run is not undone by its write")
hp = home('CLAUDE_VAULT_ROOT="$TMPHOME/vault"\n')
notep = os.path.join(hp, "vault", "_claude-live", "sessions", f"{A}.md")
reply(hp, A, "first prompt", "first reply")
reply(hp, A, "SECRET prompt", "SECRET reply", wait=False)  # runs until it needs the lock
procp = _pending.pop()
time.sleep(1.5)   # the run has read the ledger and rendered by now; the lock is held
now_ms = int(time.time() * 1000)
with open(os.path.join(hp, ".claude", "state", "incognito.json"), "w") as f:
    json.dump({"sessions": {A: {"active": True, "since_ms": now_ms,
                                "windows": [[0, now_ms]]}}}, f)
os.remove(notep)  # what the purge does next
fcntl.flock(_heldlock, fcntl.LOCK_UN)
procp.wait(timeout=60)
check("the purged note stays gone", not os.path.exists(notep))

for d in _dirs:
    shutil.rmtree(d, ignore_errors=True)
if _fails:
    print(f"FAIL: {len(_fails)} check(s)")
    sys.exit(1)
print("ALL PASS")
