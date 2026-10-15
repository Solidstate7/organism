#!/usr/bin/env python3
"""tests for the SessionStart mirror pointer that replaced the SessionEnd HANDOFF.md
snapshot: session-end-handoff.py never writes HANDOFF.md;
mirror.sessions_since() lists exactly the user's sessions a brief doesn't cover;
inject-handoff.sh renders them as a pointer after the brief, and drops its
Reconcile Queue reminder while ~/.claude/state/reconcile-reminder.off exists.
Self-contained: temp dirs only (HOME, mirror and projects all sandboxed), no network,
never touches the real mirror or ~/.claude.
Run:  python3 claude/hooks/tests/test_handoff_pointer.py"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HOOKS = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, os.path.join(HOOKS, "lib"))
import mirror

_fails = []


def check(name, cond):
    print(("  ✓ " if cond else "  ✗ ") + name)
    if not cond:
        _fails.append(name)


def sid(n):
    return f"{n:08x}-0000-4000-8000-000000000000"


def ts(epoch):
    return time.strftime(mirror.TS_FMT, time.localtime(epoch))


def write_note(sessions, n, turn_epoch, prompt, project_dir=None, headless=None,
               mtime=None):
    """A sessions/<id>.md assembled like obsidian-mirror.render_session_file."""
    out = [mirror.session_h1(sid(n)[:8], "proj"), "", mirror.machine_line(
        "Office Mac (Mac16,10)"), ""]
    if headless:
        out += [mirror.headless_line(headless), ""]
    pd = mirror.project_dir_line(project_dir) if project_dir else ""
    if pd:
        out += [pd, ""]
    out += ["---", "", mirror.turn_block(ts(turn_epoch), prompt, "ok."), ""]
    path = os.path.join(sessions, f"{sid(n)}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    m = mtime if mtime is not None else turn_epoch + 60
    os.utime(path, (m, m))


class Sandbox:
    """HOME, mirror dir and a project tree under one temp root. The project root holds
    a HANDOFF.md written at `since`; `sub/` has its own brief; `deep/` does not."""

    def __init__(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="pointer-"))
        self.home = os.path.join(self.tmp, "home")
        self.mirror_root = os.path.join(self.tmp, "mirror")
        self.sessions = os.path.join(self.mirror_root, "sessions")
        self.root = os.path.join(self.home, "proj")
        for d in (self.sessions, os.path.join(self.root, "sub"),
                  os.path.join(self.root, "deep", "er"), os.path.join(self.home, "other"),
                  os.path.join(self.home, ".claude", "projects", "-proj")):
            os.makedirs(d, exist_ok=True)
        self.since = time.time() - 3600
        for brief in (os.path.join(self.root, "HANDOFF.md"),
                      os.path.join(self.root, "sub", "HANDOFF.md")):
            with open(brief, "w") as f:
                f.write("# HANDOFF — curated\n")
            os.utime(brief, (self.since, self.since))
        os.environ["CLAUDE_OBSIDIAN_MIRROR_DIR"] = self.mirror_root

    def close(self):
        os.environ.pop("CLAUDE_OBSIDIAN_MIRROR_DIR", None)
        shutil.rmtree(self.tmp, ignore_errors=True)


def populate(sb):
    after = sb.since + 600
    write_note(sb.sessions, 1, after, "continue the root work", project_dir=sb.root)
    write_note(sb.sessions, 2, after, "sub has its own brief",
               project_dir=os.path.join(sb.root, "sub"))
    write_note(sb.sessions, 3, after, "deep but uncovered",
               project_dir=os.path.join(sb.root, "deep", "er"))
    write_note(sb.sessions, 4, after, "nightly job", project_dir=sb.root, headless="sdk-cli")
    # The session that ran /handoff: its last turn STARTED before the brief was
    # written, but its note landed after (mtime > since).
    write_note(sb.sessions, 5, sb.since - 120, "/handoff", project_dir=sb.root,
               mtime=sb.since + 30)
    write_note(sb.sessions, 6, after, "the current session", project_dir=sb.root)
    write_note(sb.sessions, 7, after, "another project", project_dir=os.path.join(sb.home, "other"))
    write_note(sb.sessions, 8, after, "pre-marker note ultracode please")   # local transcript
    with open(os.path.join(sb.home, ".claude", "projects", "-proj", f"{sid(8)}.jsonl"), "w") as f:
        f.write(json.dumps({"type": "user", "cwd": sb.root}) + "\n")
        f.write(json.dumps({"type": "user", "cwd": os.path.join(sb.home, "other")}) + "\n")
    write_note(sb.sessions, 9, after, "pre-marker, other machine")           # unplaceable
    write_note(sb.sessions, 10, sb.since - 7200, "old", project_dir=sb.root,
               mtime=sb.since - 7000)
    # Header says elsewhere; a turn merely QUOTES a root marker.
    write_note(sb.sessions, 11, after, "see <!-- project-dir: %s -->" % sb.root,
               project_dir=os.path.join(sb.home, "other"))
    with open(os.path.join(sb.sessions, "HANDOFF.md"), "w") as f:   # stray, not a session
        f.write("x")


def test_session_end_never_writes_handoff():
    print("session-end-handoff.py never writes HANDOFF.md")
    tmp = os.path.realpath(tempfile.mkdtemp(prefix="sessend-"))
    try:
        repo = os.path.join(tmp, "repo")
        os.makedirs(repo)
        subprocess.run(["git", "init", "-q", repo], check=True)
        transcript = os.path.join(tmp, "t.jsonl")
        with open(transcript, "w") as f:
            f.write(json.dumps({"type": "user", "timestamp": "2026-09-30T00:00:00Z",
                                "message": {"role": "user", "content": "hello there"}}) + "\n")
            f.write(json.dumps({"type": "assistant", "timestamp": "2026-09-30T00:00:05Z",
                                "message": {"role": "assistant",
                                            "content": [{"type": "text", "text": "hi"}]}}) + "\n")
        env = {**os.environ, "HOME": os.path.join(tmp, "home")}
        payload = json.dumps({"transcript_path": transcript, "session_id": sid(1),
                              "cwd": repo, "reason": "other"})

        logf = os.path.join(tmp, "home", ".claude", "logs", "session-end.log")

        def run():
            """Run the hook, then wait (up to 15 s) for its detached worker to finish."""
            before = open(logf).read().count("final mirror pass") if os.path.exists(logf) else 0
            r = subprocess.run(["python3", os.path.join(HOOKS, "session-end-handoff.py")],
                               input=payload, text=True, env=env, capture_output=True)
            for _ in range(150):
                if os.path.exists(logf) and open(logf).read().count("final mirror pass") > before:
                    break
                time.sleep(0.1)
            return r

        r = run()
        check("exits 0", r.returncode == 0)
        check("no HANDOFF.md created in a git repo without one",
              not os.path.exists(os.path.join(repo, "HANDOFF.md")))
        brief = os.path.join(repo, "HANDOFF.md")
        with open(brief, "w") as f:
            f.write("# curated\n")
        os.utime(brief, (1_000_000_000, 1_000_000_000))    # older than the session
        run()
        with open(brief) as f:
            body = f.read()
        check("a stale curated brief is left byte-identical", body == "# curated\n")
        check("…and its mtime untouched", os.path.getmtime(brief) == 1_000_000_000)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_sessions_since():
    print("mirror.sessions_since")
    sb = Sandbox()
    try:
        populate(sb)
        items = mirror.sessions_since(sb.root, sb.since, exclude=sid(6), home=sb.home)
        got = {it["id"] for it in items}
        check("lists a root session with the marker", sid(1) in got)
        check("skips a subdir that has its own brief", sid(2) not in got)
        check("lists a subdir without its own brief", sid(3) in got)
        check("skips headless jobs", sid(4) not in got)
        check("skips the session that wrote the brief (turn started before it)", sid(5) not in got)
        check("skips the current session", sid(6) not in got)
        check("skips another project", sid(7) not in got)
        check("places a pre-marker note by its local transcript's FIRST cwd", sid(8) in got)
        check("skips a pre-marker note it can't place", sid(9) not in got)
        check("skips sessions older than the brief", sid(10) not in got)
        check("a quoted marker in a turn doesn't move a session", sid(11) not in got)
        check("exactly those three", got == {sid(1), sid(3), sid(8)})
        one = next(it for it in items if it["id"] == sid(1))
        check("carries the last prompt", one["prompt"] == "continue the root work")
        check("carries the host label", one["machine"].startswith("Office Mac"))
        check("newest first", [it["epoch"] for it in items] ==
              sorted((it["epoch"] for it in items), reverse=True))
        check("a missing mirror dir yields nothing, no raise",
              (os.environ.__setitem__("CLAUDE_OBSIDIAN_MIRROR_DIR", os.path.join(sb.tmp, "nope"))
               or mirror.sessions_since(sb.root, 0, home=sb.home)) == [])
    finally:
        sb.close()


def run_inject(sb, project_dir, session_id, payload=None, env_extra=None):
    env = {**os.environ, "HOME": sb.home, "CLAUDE_PROJECT_DIR": project_dir,
           "CLAUDE_CODE_SESSION_ID": session_id,
           "CLAUDE_OBSIDIAN_MIRROR_DIR": sb.mirror_root, **(env_extra or {})}
    env.pop("RECONCILE_CONFIG", None)
    if payload is None:
        r = subprocess.run(["bash", os.path.join(HOOKS, "inject-handoff.sh")],
                           env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    else:
        r = subprocess.run(["bash", os.path.join(HOOKS, "inject-handoff.sh")],
                           env=env, capture_output=True, text=True, input=json.dumps(payload))
    if r.returncode != 0 or not r.stdout.strip():
        return r.returncode, ""
    return r.returncode, json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]


def test_inject_pointer():
    print("inject-handoff.sh renders the pointer after the brief")
    sb = Sandbox()
    try:
        populate(sb)
        rc, ctx = run_inject(sb, sb.root, sid(6))
        check("exits 0 with context", rc == 0 and bool(ctx))
        check("brief comes first", ctx.startswith("# HANDOFF — curated"))
        check("pointer header counts 3", "# Sessions since this brief: 3 not handed off" in ctx)
        check("lists the three session ids",
              all(f"`{sid(n)[:8]}`" in ctx for n in (1, 3, 8)))
        check("links each mirror note", os.path.join(sb.sessions, f"{sid(1)}.md") in ctx)
        check("strips the hardware id from the host label",
              "Office Mac, last prompt" in ctx and "Mac16,10" not in ctx)
        check("defuses quoted prompts", "ultra-code" in ctx and "ultracode" not in ctx)
        check("never lists the current session", f"`{sid(6)[:8]}`" not in ctx)

        # No brief: a git repo gets its last-7-days sessions; a plain dir gets nothing.
        repo = os.path.join(sb.home, "repo")
        os.makedirs(repo)
        subprocess.run(["git", "init", "-q", repo], check=True)
        write_note(sb.sessions, 12, time.time() - 600, "repo work", project_dir=repo)
        rc, ctx = run_inject(sb, repo, sid(6))
        check("no-brief git repo: recent-sessions pointer",
              "# Recent sessions in this repo: 1, no HANDOFF.md yet" in ctx
              and f"`{sid(12)[:8]}`" in ctx)
        plain = os.path.join(sb.home, "plain")
        os.makedirs(plain)
        write_note(sb.sessions, 13, time.time() - 600, "plain work", project_dir=plain)
        rc, ctx = run_inject(sb, plain, sid(6))
        check("no brief, no repo: prints nothing", rc == 0 and ctx == "")
    finally:
        sb.close()


def test_queue_reminder_switch():
    print("inject-handoff.sh: the Reconcile Queue reminder and its off switch")
    sb = Sandbox()
    try:
        claude = os.path.join(sb.home, ".claude")
        os.makedirs(os.path.join(claude, "hooks"))
        os.makedirs(os.path.join(claude, "state"))
        queue = os.path.join(sb.tmp, "Reconcile Queue.md")
        with open(queue, "w", encoding="utf-8") as f:
            f.write("<!-- AUTO:ALARMS:BEGIN -->\n"
                    "- **ALARM-proj-git** (2026-10-15): `proj`: repo unreadable\n"
                    "<!-- AUTO:ALARMS:END -->\n")
        with open(os.path.join(claude, "state", "reconcile-pairings.json"), "w") as f:
            json.dump({"queue_page": queue, "pairs": []}, f)
        rc, ctx = run_inject(sb, sb.root, sid(6))
        check("surfaces an unacked alarm", "Reconcile Queue" in ctx and "1 unacked item(s)" in ctx)
        open(os.path.join(claude, "state", "reconcile-reminder.off"), "w").close()
        rc, ctx = run_inject(sb, sb.root, sid(6))
        check("the off file hides it", rc == 0 and "Reconcile Queue" not in ctx)
        check("…and keeps the brief", ctx.startswith("# HANDOFF — curated"))
        os.remove(os.path.join(claude, "state", "reconcile-reminder.off"))
        rc, ctx = run_inject(sb, sb.root, sid(6))
        check("the ack hint names `organism ack`", "`organism ack`" in ctx)
        if not os.path.exists(os.path.join(HOOKS, "reconcile-pairings.json")):
            os.rename(os.path.join(claude, "state", "reconcile-pairings.json"),
                      os.path.join(claude, "hooks", "reconcile-pairings.json"))
            rc, ctx = run_inject(sb, sb.root, sid(6))
            check("the legacy ~/.claude/hooks pairings file is still read", "Reconcile Queue" in ctx)
    finally:
        sb.close()


def test_inject_session_and_incognito():
    print("inject-handoff.sh: session id, incognito reminder, once-gate")
    sb = Sandbox()
    try:
        state = os.path.join(sb.home, ".claude", "state")
        os.makedirs(state, exist_ok=True)
        inc = os.path.join(state, "incognito.json")
        rec = {"sessions": {sid(7): {"active": True, "since_ms": 1, "windows": []}}}
        with open(inc, "w") as f:
            json.dump(rec, f)
        rc, ctx = run_inject(sb, sb.root, sid(8))
        check("another session's incognito adds nothing", "Incognito" not in ctx)
        rc, ctx = run_inject(sb, sb.root, sid(7))
        check("an incognito session gets the one-line reminder first",
              ctx.startswith("# Incognito: this session is incognito; /incognito off to resume"))
        with open(inc) as f:
            check("…and stays incognito (no auto-resume)", json.load(f) == rec)
        tmpdir = os.path.join(sb.tmp, "once")
        os.makedirs(tmpdir)
        once_env = {"TMPDIR": tmpdir}
        rc, ctx = run_inject(sb, sb.root, sid(8), {"session_id": sid(7), "source": "resume"}, once_env)
        check("the payload's session id wins over the environment's",
              ctx.startswith("# Incognito: this session is incognito"))
        rc, ctx = run_inject(sb, sb.root, sid(8), {"session_id": sid(7), "source": "resume"}, once_env)
        check("the same start delivered twice gets one brief", rc == 0 and ctx == "")
        rc, ctx = run_inject(sb, sb.root, sid(8), {"session_id": sid(7), "source": "compact"}, once_env)
        check("a different start source still gets its brief", ctx.startswith("# Incognito"))
        # Python falls back to the shared /tmp when TMPDIR is unusable, so the start
        # source is unique per run: a stamp from an earlier run must not answer here.
        rc, ctx = run_inject(sb, sb.root, sid(8),
                             {"session_id": sid(7), "source": "resume-%d" % time.time_ns()},
                             {"TMPDIR": "/nonexistent/x"})
        check("an unwritable stamp folder never drops the brief", ctx.startswith("# Incognito"))
    finally:
        sb.close()


def test_relative_project_dir():
    print("inject-handoff.sh with a relative project dir")
    sb = Sandbox()
    try:
        populate(sb)
        env = {**os.environ, "HOME": sb.home, "CLAUDE_PROJECT_DIR": os.path.basename(sb.root),
               "CLAUDE_CODE_SESSION_ID": sid(6), "CLAUDE_OBSIDIAN_MIRROR_DIR": sb.mirror_root}
        env.pop("RECONCILE_CONFIG", None)
        try:
            r = subprocess.run(["bash", os.path.join(HOOKS, "inject-handoff.sh")], env=env,
                               cwd=os.path.dirname(sb.root), capture_output=True, text=True,
                               stdin=subprocess.DEVNULL, timeout=5)
            done = r.returncode == 0 and "HANDOFF" in r.stdout
        except subprocess.TimeoutExpired:
            done = False
        check("finds the brief", done)
        bare = os.path.join(sb.home, "no-brief")
        os.makedirs(bare)
        env["CLAUDE_PROJECT_DIR"] = "no-brief"
        try:
            r = subprocess.run(["bash", os.path.join(HOOKS, "inject-handoff.sh")], env=env,
                               cwd=sb.home, capture_output=True, text=True,
                               stdin=subprocess.DEVNULL, timeout=5)
            done = r.returncode == 0
        except subprocess.TimeoutExpired:
            done = False
        check("with no brief anywhere, exits instead of looping", done)
    finally:
        sb.close()


def test_inject_edge_paths():
    print("inject-handoff.sh: odd bytes, a link to HOME, a missing relative dir")
    sb = Sandbox()
    try:
        latin = os.path.join(sb.home, "latin")
        os.makedirs(latin)
        with open(os.path.join(latin, "HANDOFF.md"), "wb") as f:
            f.write(b"# HANDOFF caf\xe9 notes\n")
        rc, ctx = run_inject(sb, latin, sid(6))
        check("a brief that is not UTF-8 is still injected", rc == 0 and "HANDOFF caf" in ctx)

        with open(os.path.join(sb.home, "HANDOFF.md"), "w") as f:
            f.write("# HOME-LEVEL brief\n")
        link = os.path.join(sb.tmp, "home-link")
        os.symlink(sb.home, link)
        os.makedirs(os.path.join(sb.home, "plain2"))
        rc, ctx = run_inject(sb, os.path.join(link, "plain2"), sid(6))
        check("a start dir reached through a link to HOME never gets the home-level brief",
              rc == 0 and "HOME-LEVEL" not in ctx)

        env = {**os.environ, "HOME": sb.home, "CLAUDE_PROJECT_DIR": "nope/x",
               "CLAUDE_OBSIDIAN_MIRROR_DIR": sb.mirror_root}
        env.pop("RECONCILE_CONFIG", None)
        r = subprocess.run(["bash", os.path.join(HOOKS, "inject-handoff.sh")], env=env,
                           cwd=sb.home, capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=10)
        check("a relative project dir that does not exist exits 0", r.returncode == 0)
    finally:
        sb.close()


if __name__ == "__main__":
    test_relative_project_dir()
    test_session_end_never_writes_handoff()
    test_sessions_since()
    test_inject_pointer()
    test_queue_reminder_switch()
    test_inject_session_and_incognito()
    test_inject_edge_paths()
    print(f"\n{'ALL PASS' if not _fails else 'FAILED: ' + ', '.join(_fails)}")
    sys.exit(1 if _fails else 0)
