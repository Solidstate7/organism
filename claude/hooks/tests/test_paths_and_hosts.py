#!/usr/bin/env python3
"""tests for what a session note says a tool call touched (obsidian-mirror.py, trace_lines):
the paths a call read or wrote; a call that touched a path under a folder that opted out
shows "[excluded]" as its title detail and its paths line and withholds its output, so no
path and no text from that folder reaches another project's note; the host a web fetch or
a browser call reached (a browser call inherits the host its tab last opened, a batch that
navigates moves the tab first, never a query string, in the title either); the browser's
own description of its action, marked as the session's claim; the connector's name; and
the outbound mark: remote for a push (also `git -C dir push`), a deploy, a web fetch, a
page opened in the browser, a curl that posts, or a connector call that writes or sends;
local for a commit. A plain read, `ls`, `gh pr list`, `ls ~/.vercel`, `git branch -a` and
a connector call that only reads add no mark. The publish-class name test must equal the
publish guard's. The transcript is generated here because the opted-out folder has to
exist on disk. Temp dirs only; never touches the live vault or ~/.claude.
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
        ("t2", "Read", {"file_path": excluded_file}, "private text of the excluded file"),
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
        ("t12", "Bash", {"command": "git -C /work/other push origin main", "description": "Push the other repo"}, "done"),
        ("t13", "Bash", {"command": "gh pr list --state open", "description": "List open PRs"}, "none"),
        ("t14", "Bash", {"command": "ls ~/.vercel", "description": "Look at the vercel folder"}, "project.json"),
        ("t15", "Bash", {"command": "curl -s -X POST https://api.example.net/v1/send?key=SECRETQ -d '{}'"}, "ok"),
        ("t16", "mcp__claude-in-chrome__browser_batch", {"actions": [
            {"name": "navigate", "input": {"tabId": 5, "url": "https://github.com/x/y?tab=readme"}},
            {"name": "computer", "input": {"action": "left_click", "tabId": 5, "action_summary": "Opens the file"}}]}, "ok"),
        ("t17", "mcp__claude-in-chrome__computer", {"action": "left_click", "tabId": 5, "action_summary": "Clicks Raw"}, "ok"),
        ("t18", "mcp__claude_ai_Gmail__get_message", {"id": "m1"}, "a mail"),
        ("t19", "mcp__claude_ai_Vercel__list_deployments", {"projectId": "p"}, "[]"),
        ("t20", "Bash", {"command": "git branch -a", "description": "List branches"}, "main"),
        ("t21", "Bash", {"command": "rsync -a ./build/ ./backup/", "description": "Copy locally"}, "ok"),
        ("t22", "Bash", {"command": "npx wrangler deploy", "description": "Deploy the worker"}, "ok"),
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
    return len(calls)


def callouts(doc):
    """Every callout as one string, in order."""
    out, cur = [], None
    for ln in doc.splitlines():
        if ln.startswith("> [!"):
            if cur:
                out.append("\n".join(cur))
            cur = [ln]
        elif cur is not None and ln.startswith(">"):
            cur.append(ln)
        elif cur is not None:
            out.append("\n".join(cur))
            cur = None
    if cur:
        out.append("\n".join(cur))
    return out


def by_title(cs, part):
    return [c for c in cs if part in c.splitlines()[0]]


home = tempfile.mkdtemp(prefix="pah-")
try:
    cwd = os.path.join(home, "w", "demo-app")
    os.makedirs(os.path.join(cwd, "src"))
    private = os.path.join(home, "w", "private-proj")
    os.makedirs(private)
    open(os.path.join(private, ".organism-no-notes"), "w").close()
    excluded_file = os.path.join(private, "notes.md")
    vault = os.path.join(home, "notes")
    tpath = os.path.join(home, "t.jsonl")
    n_calls = transcript(tpath, cwd, excluded_file)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE_", "GIT_"))}
    env.update(HOME=home, TZ="UTC", CLAUDE_OBSIDIAN_MIRROR_DIR=vault)
    payload = json.dumps({"session_id": SID, "transcript_path": tpath, "cwd": cwd, "hook_event_name": "Stop"})
    p = subprocess.run([sys.executable, MIRROR], input=payload, text=True, env=env, capture_output=True, timeout=120)
    doc = read(os.path.join(vault, "sessions", SID + ".md"))
    errlog = read(os.path.join(home, ".claude", "logs", "obsidian-mirror-errors.log"))
    cs = callouts(doc)

    print("the note was written")
    check("the hook exits 0 and logs no error", p.returncode == 0 and not errlog.strip())
    check("one callout per call", len(cs) == n_calls)

    print("paths and the opt-out")
    check("an edit names its path", "- paths: `%s/src/app.py`" % cwd in by_title(cs, "Edit")[0])
    ex = by_title(cs, "[excluded]")
    check("the excluded call's title is the tool and [excluded], its paths line [excluded], its output withheld",
          len(ex) == 1 and ex[0].startswith("> [!note]- 📖 Read · [excluded]") and "- paths: [excluded]" in ex[0]
          and "output withheld" in ex[0])
    check("neither the folder, the file name nor its text reaches the note",
          "private-proj" not in doc and "notes.md" not in doc and "private text" not in doc)
    c3 = by_title(cs, "List files")[0]
    check("a plain ls adds no line", "- " not in c3.split("\n>\n", 1)[1].split("```")[0])

    print("hosts and the browser")
    c6 = by_title(cs, "WebFetch")[0]
    check("a web fetch names the host only; the query string reaches neither title nor body",
          "- hosts: example.org" in c6 and "token=abc123" not in doc)
    check("a web fetch is outbound", "- outbound: remote" in c6)
    c7 = by_title(cs, "navigate")[0]
    check("opening a page names the host and is outbound", "- hosts: medium.com" in c7 and "- outbound: remote" in c7)
    c8 = by_title(cs, "computer")[0]
    check("a click inherits its tab's host", "- hosts: medium.com" in c8)
    check("the browser's description is marked as the session's claim", '- as described by the session: "Clicks Publish"' in c8)
    check("a click is not outbound", "outbound" not in c8)
    c9 = by_title(cs, "browser_batch")[0]
    check("a batch collects nested descriptions and inherits the host", '"Types the title"' in c9 and "- hosts: medium.com" in c9)
    c16 = by_title(cs, "browser_batch")[1]
    check("a batch that navigates names the new host, is outbound and drops the query string",
          "- hosts: github.com" in c16 and "- outbound: remote" in c16 and "tab=readme" not in doc)
    c17 = by_title(cs, "computer")[1]
    check("the next click inherits the host the batch moved the tab to", "- hosts: github.com" in c17 and "medium.com" not in c17)

    print("connectors")
    c10 = by_title(cs, "send_message")[0]
    check("a connector send names the connector and is outbound", "- connector: claude_ai_Gmail" in c10 and "- outbound: remote" in c10)
    for part in ("notion-fetch", "get_message", "list_deployments"):
        c = by_title(cs, part)[0]
        check("a connector read (%s) names the connector and is not outbound" % part, "- connector: " in c and "outbound" not in c)

    print("commands")
    check("a push is outbound", "- outbound: remote" in by_title(cs, "Push main")[0])
    check("git -C dir push is outbound", "- outbound: remote" in by_title(cs, "Push the other repo")[0])
    c15 = by_title(cs, "curl")[0]
    check("a curl that posts is outbound; its URL is no path and its query string is gone",
          "- outbound: remote" in c15 and "paths:" not in c15 and "SECRETQ" not in doc)
    check("npx wrangler deploy is outbound", "- outbound: remote" in by_title(cs, "Deploy the worker")[0])
    check("a commit is marked local", "- git: local" in by_title(cs, "Commit the work")[0])
    for part in ("List open PRs", "Look at the vercel folder", "List branches", "Copy locally"):
        c = by_title(cs, part)[0]
        check("%s: no outbound and no local mark" % part, "outbound" not in c and "git: local" not in c)
    check("eight outbound marks in all", doc.count("- outbound: remote") == 8)

    print("the publish-class name test equals the guard's")
    m1 = re.search(r'OUTBOUND_TOOL_RE = re\.compile\(r"([^"]+)", re\.I\)', read(MIRROR))
    m2 = re.search(r"!web && !/([^/]+)/i\.test\(name\)", read(GUARD))
    check("both patterns were found", bool(m1) and bool(m2))
    check("the mirror's pattern equals guard-publish.js's", bool(m1 and m2) and m1.group(1) == m2.group(1))
finally:
    shutil.rmtree(home, ignore_errors=True)

print()
if _fails:
    print("FAIL: %d check(s): %s" % (len(_fails), "; ".join(_fails)))
    sys.exit(1)
print("ALL PASS")
