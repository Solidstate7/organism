#!/usr/bin/env python3
"""tests for what a session note says a tool call touched (obsidian-mirror.py, trace_lines):
the paths a call read or wrote, with a path under a folder that opted out shown as
[excluded]; the host a web fetch or a browser call reached (a browser call inherits the
host its tab last opened, never the query string); the browser's own description of its
action, marked as the session's claim; the connector's name; and the outbound mark
(remote for a push, a web fetch, a page opened in the browser or a connector call that
sends; local for a commit). A plain read or `ls` adds no line. The publish-class name test
must equal the publish guard's. The transcript is generated here because the opted-out
folder has to exist on disk. Temp dirs only; never touches the live vault or ~/.claude.
Run:  python3 claude/hooks/tests/test_paths_and_hosts.py"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
HOOKS = os.path.dirname(HERE)
MIRROR = os.path.join(HOOKS, "obsidian-mirror.py")
GUARD = os.path.join(HOOKS, "guard-publish.js")
_fails = []


def check(name, cond):
    print(("  ✓ " if cond else "  ✗ ") + name)
    if not cond:
        _fails.append(name)


def read(p):
    try:
        with open(p, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return ""


SID = "22222222-aaaa-4bbb-8ccc-000000000002"
T0 = "2026-03-03T10:00:%02d.000Z"


def entry(i, role, content, cwd):
    return {"parentUuid": None, "isSidechain": False, "type": role, "uuid": "22222222-%04d" % i,
            "timestamp": T0 % (i % 60), "sessionId": SID, "cwd": cwd, "entrypoint": "cli",
            "version": "2.1.0", "message": {"role": role, "content": content,
                                            **({"model": "opus-5-5"} if role == "assistant" else {})}}


def transcript(path, cwd, excluded_file):
    calls = [
        ("t1", "Edit", {"file_path": cwd + "/src/app.py", "old_string": "a", "new_string": "b"}, "ok"),
        ("t2", "Read", {"file_path": excluded_file}, "private text"),
        ("t3", "Bash", {"command": "ls", "description": "List files"}, "README.md"),
        ("t4", "Bash", {"command": "git commit -m 'feat: x'", "description": "Commit the work"}, "[main 1234abc] feat: x"),
        ("t5", "Bash", {"command": "git push origin main", "description": "Push main"}, "done"),
        ("t6", "WebFetch", {"url": "https://example.org/page?token=abc123", "prompt": "summarise"}, "page text"),
        ("t7", "mcp__claude-in-chrome__navigate", {"tabId": 5, "url": "https://medium.com/p/1/edit"}, "loaded"),
        ("t8", "mcp__claude-in-chrome__computer", {"action": "left_click", "tabId": 5, "ref": "r1",
                                                   "action_summary": "Clicks Publish"}, "clicked"),
        ("t9", "mcp__claude-in-chrome__browser_batch", {"actions": [
            {"name": "computer", "input": {"action": "type", "tabId": 5, "text": "hello",
                                           "action_summary": "Types the title"}},
            {"name": "computer", "input": {"action": "wait", "tabId": 5, "duration": 1}}]}, "ok"),
        ("t10", "mcp__claude_ai_Gmail__send_message", {"to": "someone@example.invalid", "subject": "hi"}, "sent"),
        ("t11", "mcp__claude_ai_Notion__notion-fetch", {"id": "abc"}, "page"),
    ]
    lines = [entry(1, "user", "Do the things.", cwd)]
    blocks = [{"type": "text", "text": "Starting."}]
    blocks += [{"type": "tool_use", "id": i, "name": n, "input": inp} for i, n, inp, _ in calls]
    lines.append(entry(2, "assistant", blocks, cwd))
    lines.append(entry(3, "user", [{"type": "tool_result", "tool_use_id": i, "content": out}
                                   for i, _, _, out in calls], cwd))
    lines.append(entry(4, "assistant", [{"type": "text", "text": "Done."}], cwd))
    with open(path, "w", encoding="utf-8") as f:
        for ln in lines:
            f.write(json.dumps(ln) + "\n")


def callout(doc, title_part):
    """The callout whose title line holds title_part, as one string (its `> ` lines)."""
    lines = doc.splitlines()
    for i, ln in enumerate(lines):
        if ln.startswith("> [!") and title_part in ln:
            j = i + 1
            while j < len(lines) and lines[j].startswith(">"):
                j += 1
            return "\n".join(lines[i:j])
    return ""


home = tempfile.mkdtemp(prefix="paths-and-hosts-")
try:
    cwd = os.path.join(home, "work", "demo-app")
    os.makedirs(os.path.join(cwd, "src"))
    private = os.path.join(home, "work", "private-proj")
    os.makedirs(private)
    open(os.path.join(private, ".organism-no-notes"), "w").close()
    excluded_file = os.path.join(private, "notes.md")
    vault = os.path.join(home, "notes")
    tpath = os.path.join(home, "t.jsonl")
    transcript(tpath, cwd, excluded_file)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE_", "GIT_"))}
    env.update(HOME=home, TZ="UTC", CLAUDE_OBSIDIAN_MIRROR_DIR=vault)
    payload = json.dumps({"session_id": SID, "transcript_path": tpath, "cwd": cwd, "hook_event_name": "Stop"})
    p = subprocess.run([sys.executable, MIRROR], input=payload, text=True, env=env, capture_output=True, timeout=120)
    doc = read(os.path.join(vault, "sessions", SID + ".md"))
    errlog = read(os.path.join(home, ".claude", "logs", "obsidian-mirror-errors.log"))

    print("the note was written")
    check("the hook exits 0 and logs no error", p.returncode == 0 and not errlog.strip())
    check("eleven callouts", doc.count("> [!note]-") == 11)

    print("paths")
    check("an edit names its path", "- paths: `%s/src/app.py`" % cwd in callout(doc, "Edit"))
    check("a path under an opted-out folder shows as [excluded], never the path",
          "- paths: [excluded]" in callout(doc, "Read") and "private-proj" not in doc)
    check("a plain ls adds no line", "- " not in callout(doc, "List files").split("\n>\n", 1)[1].split("```")[0])

    print("hosts and the browser")
    c6 = callout(doc, "WebFetch")
    check("a web fetch names the host only, never the query string", "- hosts: example.org" in c6 and "token=abc123" not in doc)
    check("a web fetch is outbound", "- outbound: remote" in c6)
    c7 = callout(doc, "navigate")
    check("opening a page in the browser names the host and is outbound", "- hosts: medium.com" in c7 and "- outbound: remote" in c7)
    c8 = callout(doc, "computer")
    check("a browser click inherits its tab's host", "- hosts: medium.com" in c8)
    check("the browser's own description is marked as the session's claim",
          '- as described by the session: "Clicks Publish"' in c8)
    check("a click is not marked outbound", "outbound" not in c8)
    c9 = callout(doc, "browser_batch")
    check("a batch's nested descriptions are collected", '"Types the title"' in c9 and "- hosts: medium.com" in c9)

    print("connectors and commands")
    c10 = callout(doc, "send_message")
    check("a connector send names the connector and is outbound", "- connector: claude_ai_Gmail" in c10 and "- outbound: remote" in c10)
    c11 = callout(doc, "notion-fetch")
    check("a connector read names the connector and is not outbound", "- connector: claude_ai_Notion" in c11 and "outbound" not in c11)
    check("a push is outbound", "- outbound: remote" in callout(doc, "Push main"))
    check("a commit is marked local", "- git: local" in callout(doc, "Commit the work"))
    check("four outbound marks in all", doc.count("- outbound: remote") == 4)

    print("the publish-class name test equals the guard's")
    mirror_src = read(MIRROR)
    guard_src = read(GUARD)
    m1 = re.search(r'OUTBOUND_TOOL_RE = re\.compile\(r"([^"]+)", re\.I\)', mirror_src)
    m2 = re.search(r"!web && !/([^/]+)/i\.test\(name\)", guard_src)
    check("both patterns were found", bool(m1) and bool(m2))
    check("the mirror's pattern equals guard-publish.js's", bool(m1 and m2) and m1.group(1) == m2.group(1))
finally:
    shutil.rmtree(home, ignore_errors=True)

print()
if _fails:
    print("FAIL: %d check(s): %s" % (len(_fails), "; ".join(_fails)))
    sys.exit(1)
print("ALL PASS")
