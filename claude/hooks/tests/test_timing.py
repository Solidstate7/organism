#!/usr/bin/env python3
"""timing tests: how long the heavy paths take on big inputs, printed so a slowdown shows up
before it hurts. Fails only past generous ceilings (CI runners are slow).

  - renders a synthetic 2,000-turn transcript through the Stop-hook path (obsidian-mirror.py
    in a subprocess, temp HOME) and checks all 2,000 turns came out
  - builds a synthetic repo with `git fast-import` and times adr-link.sh `state` and `backfill`
    on it, checking they counted every commit and linked the right ones

adr-link.sh starts several git processes per commit, so its time grows with history: roughly
8 ms a commit for `state` and 7 ms for `backfill`. The repo defaults to 2,000 commits to keep
this test near 30 seconds; run ORGANISM_TIMING_COMMITS=10000 for the full size (about three
minutes). The ceilings scale with the commit count.

Environment:
  ORGANISM_SKIP_TIMING=1       skip everything (exit 0)
  ORGANISM_TIMING_SCALE=N      multiply every ceiling by N (slow machines)
  ORGANISM_TIMING_COMMITS=N    commits in the adr-link repo (default 2000)

Self-contained: temp dirs only, git hooks off, HOME sandboxed; never touches a real repo.
Run:  python3 claude/hooks/tests/test_timing.py"""
import datetime
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.realpath(__file__))
HOOKS = os.path.dirname(HERE)
MIRROR = os.path.join(HOOKS, "obsidian-mirror.py")
ADR = os.path.join(HOOKS, "adr-link.sh")

TURNS = 2000
DEFAULT_COMMITS = 2000
# Ceilings in seconds at the default sizes, before ORGANISM_TIMING_SCALE: about ten times
# what a laptop measures, never under 30.
CEIL_RENDER = 30.0
CEIL_STATE = 200.0
CEIL_BACKFILL = 150.0

_fails, _dirs = [], []


def check(name, cond):
    print(("  ✓ " if cond else "  ✗ ") + name)
    if not cond:
        _fails.append(name)


def scale():
    try:
        return max(float(os.environ.get("ORGANISM_TIMING_SCALE") or 1), 0.01)
    except ValueError:
        return 1.0


def clean_env(home):
    e = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE_", "GIT_"))}
    e.update(HOME=home, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull,
             GIT_TERMINAL_PROMPT="0", GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@test.invalid",
             GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@test.invalid")
    return e


def tmp(prefix):
    d = tempfile.mkdtemp(prefix=prefix)
    _dirs.append(d)
    return d


# ---------------------------------------------------------------------------- render
def write_transcript(path, sid, turns):
    base = datetime.datetime(2026, 3, 2, 9, 0, 0)
    with open(path, "w", encoding="utf-8") as f:
        for i in range(turns):
            ts = (base + datetime.timedelta(seconds=i * 10)).strftime("%Y-%m-%dT%H:%M:%S.000Z")

            def put(typ, msg):
                f.write(json.dumps({"type": typ, "uuid": "u%d-%s" % (i, typ), "timestamp": ts,
                                    "sessionId": sid, "cwd": "/work/demo-app",
                                    "entrypoint": "cli", "message": msg}) + "\n")
            put("user", {"role": "user", "content": "Prompt %d: please handle item number %d" % (i, i)})
            put("assistant", {"role": "assistant", "model": "claude-opus-5-5", "content": [
                {"type": "thinking", "thinking": "work out item %d" % i},
                {"type": "text", "text": "On it."},
                {"type": "tool_use", "id": "t%d" % i, "name": "Bash",
                 "input": {"command": "ls", "description": "List item %d" % i}}]})
            put("user", {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t%d" % i, "content": "a\nb\nc"}]})
            put("assistant", {"role": "assistant", "model": "claude-opus-5-5",
                              "content": [{"type": "text", "text": "Finished item %d." % i}]})
            if i % 5 == 0:
                f.write(json.dumps({"type": "last-prompt", "sessionId": sid}) + "\n")


def test_render():
    print("render a %d-turn transcript (Stop hook path)" % TURNS)
    home = tmp("timing-render-")
    sid = "66666666-aaaa-4bbb-8ccc-000000000006"
    tpath = os.path.join(home, "session.jsonl")
    write_transcript(tpath, sid, TURNS)
    size = os.path.getsize(tpath)
    env = clean_env(home)
    env["CLAUDE_OBSIDIAN_MIRROR_DIR"] = os.path.join(home, "notes")
    payload = json.dumps({"session_id": sid, "transcript_path": tpath, "cwd": "/work/demo-app",
                          "hook_event_name": "Stop"})
    t0 = time.time()
    p = subprocess.run([sys.executable, MIRROR], input=payload, text=True, env=env,
                       capture_output=True, timeout=600)
    took = time.time() - t0
    print("  . rendered %d turns (%.1f MB of transcript) in %.2f s" % (TURNS, size / 1e6, took))
    note = os.path.join(home, "notes", "sessions", sid + ".md")
    body = open(note, encoding="utf-8").read() if os.path.exists(note) else ""
    check("all %d turns were rendered" % TURNS, body.count("### 🕐 ") == TURNS and p.returncode == 0)
    ceil = CEIL_RENDER * scale()
    check("render stays under %.0f s" % ceil, took < ceil)


# ---------------------------------------------------------------------------- adr-link
def build_repo(repo, n, env):
    """n commits via git fast-import. Commit 1 adds docs/adr/0001-*.md. Every 100th commit
    carries an `ADR: 0001` trailer and every 250th (the others) names `(ADR-1)` in its subject,
    so backfill has a known number of notes to write. Returns that number."""
    os.makedirs(repo)
    git = ["git", "-c", "core.hooksPath=/dev/null"]
    subprocess.run(git + ["init", "-q", "-b", "main", repo], check=True, env=env,
                   capture_output=True)
    out = []

    def commit(i, msg, files, ts):
        m = msg.encode()
        out.append(b"commit refs/heads/main\nmark :%d\nauthor t <t@test.invalid> %d +0000\n"
                   b"committer t <t@test.invalid> %d +0000\ndata %d\n%s\n" % (i, ts, ts, len(m), m))
        if i > 1:
            out.append(b"from :%d\n" % (i - 1))
        for path, content in files:
            c = content.encode()
            out.append(b"M 644 inline %s\ndata %d\n%s\n" % (path.encode(), len(c), c))

    base = 1700000000
    commit(1, "feat: first version", [("docs/adr/0001-keep-it-simple.md", "# 1. Keep it simple\n"),
                                      ("src/f0.txt", "0\n")], base)
    expect_notes = 0
    for i in range(2, n + 1):
        msg = "feat: change %d" % i
        if i % 100 == 0:
            msg += "\n\nADR: 0001"
            expect_notes += 1
        elif i % 250 == 0:
            msg += " (ADR-1)"
            expect_notes += 1
        commit(i, msg, [("src/f%d.txt" % (i % 50), "v%d\n" % i)], base + i)
    subprocess.run(git + ["-C", repo, "fast-import", "--quiet"], input=b"".join(out), check=True,
                   env=env, capture_output=True)
    subprocess.run(git + ["-C", repo, "reset", "-q", "--hard", "main"], check=True,
                   env=env, capture_output=True)
    return expect_notes


def test_adr_link():
    try:
        n = int(os.environ.get("ORGANISM_TIMING_COMMITS") or DEFAULT_COMMITS)
    except ValueError:
        n = DEFAULT_COMMITS
    n = max(n, 300)
    print("adr-link.sh state and backfill on a %d-commit repo" % n)
    home = tmp("timing-adr-")
    repo = os.path.join(home, "repo")
    env = clean_env(home)
    t0 = time.time()
    expect_notes = build_repo(repo, n, env)
    print("  . built the repo with git fast-import in %.2f s" % (time.time() - t0))
    git = ["git", "-c", "core.hooksPath=/dev/null", "-C", repo]
    count = subprocess.run(git + ["rev-list", "--count", "HEAD"], capture_output=True, text=True,
                           env=env).stdout.strip()
    check("the repo has %d commits" % n, count == str(n))

    ratio = n / float(DEFAULT_COMMITS)
    t0 = time.time()
    p = subprocess.run(["bash", ADR, "state"], cwd=repo, env=env, capture_output=True, text=True,
                       timeout=3600)
    took = time.time() - t0
    print("  . state: %.1f s (%.1f ms a commit)" % (took, 1000.0 * took / n))
    lines = p.stdout.splitlines()
    check("state counts every commit as work", "work_commits=%d" % n in lines)
    check("state sees the one ADR and no hook enforcer",
          "decision_repo=yes" in lines and "adrs=1" in lines and "enforcer=off" in lines)
    ceil = CEIL_STATE * scale() * ratio
    check("state stays under %.0f s" % ceil, took < ceil)

    t0 = time.time()
    p = subprocess.run(["bash", ADR, "backfill"], cwd=repo, env=env, capture_output=True, text=True,
                       timeout=3600)
    took = time.time() - t0
    print("  . backfill: %.1f s (%.1f ms a commit)" % (took, 1000.0 * took / n))
    notes = subprocess.run(git + ["notes", "--ref=adr", "list"], capture_output=True, text=True,
                           env=env).stdout.split("\n")
    notes = [x for x in notes if x.strip()]
    check("backfill linked exactly the %d commits that name the ADR" % expect_notes,
          len(notes) == expect_notes)
    check("backfill reports what it did", "%d commit(s) linked" % expect_notes in p.stderr)
    ceil = CEIL_BACKFILL * scale() * ratio
    check("backfill stays under %.0f s" % ceil, took < ceil)


if os.environ.get("ORGANISM_SKIP_TIMING") == "1":
    print("timing tests skipped (ORGANISM_SKIP_TIMING=1)")
    sys.exit(0)
try:
    test_render()
    test_adr_link()
finally:
    for d in _dirs:
        shutil.rmtree(d, ignore_errors=True)
if _fails:
    print("FAILED: %d check(s)" % len(_fails))
    sys.exit(1)
print("all timing checks passed")
