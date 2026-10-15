#!/usr/bin/env python3
"""tests for the session-note renderer (obsidian-mirror.py) against synthetic transcripts
shaped like real Claude Code ones (fixtures/transcripts/*.jsonl). Each fixture is fed to the
renderer as a subprocess with the Stop-hook payload on stdin, exactly like the hook runs, in a
temp HOME with the notes folder pointed at a temp dir, so the live ~/.claude and vault are never
touched. Everything is synthetic: no real transcript content.

  tools-and-thinking.jsonl            tool_use/tool_result pairs (parallel calls, an error result, an
                                      empty result, an unknown tool, a long output), thinking blocks,
                                      several text blocks per reply
  prompts-commands-compaction.jsonl   slash command + isMeta payload, text+image and image-only
                                      prompts, the 40/41-line fold boundary, a compaction, an API
                                      error message
  noise-and-sidechain.jsonl           subagent (isSidechain) entries and every non-message entry
                                      type interleaved, an unknown type, a corrupt line
  minimal.jsonl                       one small turn, compared to a golden file

The renderer swallows its own exceptions (a hook must never block the turn), so every run also
asserts that no error breadcrumb was written.
Run:  python3 claude/hooks/tests/test_renderer_fixtures.py"""
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
FIX = os.path.join(HERE, "fixtures", "transcripts")
_fails, _dirs = [], []


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


def session_id_of(path):
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    sid = json.loads(line).get("sessionId")
                except Exception:
                    continue
                if sid:
                    return sid
    except OSError:
        pass
    return "session"


def render(transcript, cwd="/work/demo-app"):
    """Run the Stop hook on `transcript` in a fresh temp HOME. Returns a dict with the rc, the
    session note, latest.md, the index and the error log."""
    home = tempfile.mkdtemp(prefix="renderer-fixtures-")
    _dirs.append(home)
    vault = os.path.join(home, "notes")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE_", "GIT_"))}
    env.update(HOME=home, TZ="UTC", CLAUDE_OBSIDIAN_MIRROR_DIR=vault)
    sid = session_id_of(transcript)
    payload = json.dumps({"session_id": sid, "transcript_path": transcript, "cwd": cwd,
                          "hook_event_name": "Stop"})
    p = subprocess.run([sys.executable, MIRROR], input=payload, text=True, env=env,
                       capture_output=True, timeout=120)
    return {
        "rc": p.returncode, "home": home, "vault": vault, "sid": sid,
        "session": read(os.path.join(vault, "sessions", sid + ".md")),
        "latest": read(os.path.join(vault, "latest.md")),
        "index": read(os.path.join(vault, ".sessions.json")),
        "errlog": read(os.path.join(home, ".claude", "logs", "obsidian-mirror-errors.log")),
    }


def turns(doc):
    """The turn blocks of a session note (everything after each `### 🕐` stamp)."""
    parts = re.split(r"^### 🕐 ", doc, flags=re.M)[1:]
    return parts


def split_you_claude(block):
    you, _, claude = block.partition("**🤖 Claude**")
    return you, claude


def callouts(text):
    """(type, icon, title, body) for each tool callout, in order. A callout is a run of
    `> ` lines that starts at `> [!note]-` / `> [!failure]-`."""
    out = []
    for m in re.finditer(r"^> \[!(note|failure)\](-?) (\S+)(?: (.*))?\n((?:>.*\n?)*)", text, flags=re.M):
        out.append((m.group(1), m.group(3), m.group(4) or "", m.group(5), m.group(2) == "-"))
    return out


def prose_lines(text):
    """Lines of `text` outside any blockquote callout."""
    return [ln for ln in text.splitlines() if not ln.startswith(">")]


def main():
    # ------------------------------------------------------------------ tools-and-thinking
    print("tools-and-thinking.jsonl")
    r = render(os.path.join(FIX, "tools-and-thinking.jsonl"))
    doc = r["session"]
    check("hook exits 0 and wrote the session note", r["rc"] == 0 and bool(doc))
    check("no error breadcrumb was written", r["errlog"] == "")
    check("two turns (tool_result carriers do not start turns)", len(turns(doc)) == 2)
    check("turn stamps use local YYYY-MM-DD HH:MM:SS",
          all(re.match(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\n", t) for t in turns(doc)))
    check("each turn has You and Claude blocks, reply tagged with the model",
          all("**🧑 You**" in t and "**🤖 Claude** · `opus-5-5`" in t for t in turns(doc)))
    check("thinking text appears nowhere (note, dashboard, index)",
          not any("THINKING-SENTINEL" in x for x in (doc, r["latest"], r["index"])))

    cs = callouts(doc)
    want = [("note", "📖", "Read · /work/demo-app/src/parser.py"),
            ("failure", "🖥️", "Bash · Run the parser on empty input"),
            ("note", "✏️", "Edit · /work/demo-app/src/parser.py"),
            ("note", "🖥️", "Bash · Run the test suite"),
            ("note", "🔎", "Grep · parse\\("),
            ("note", "🗂️", "Glob · **/*.py"),
            ("note", "📝", "Write · /work/demo-app/NOTES.md"),
            ("note", "🌐", "WebFetch · https://example.invalid/docs"),
            ("failure", "🔧", "mcp__demo__lookup · parser"),
            ("note", "✅", "TodoWrite")]
    check("ten tool callouts, in call order, each with its icon and title",
          [(c[0], c[1], c[2]) for c in cs] == want)
    check("every tool callout is collapsed (`]-`)", bool(cs) and all(c[4] for c in cs))
    check("error results use [!failure]-, the rest [!note]-",
          [c[0] for c in cs].count("failure") == 2 and doc.count("> [!note]-") == 8)
    by_title = {c[2]: c for c in cs}
    check("the failure callout carries the error output",
          "IndexError" in by_title["Bash · Run the parser on empty input"][3])
    check("parallel results land on the right call",
          "test_case_001" in by_title["Bash · Run the test suite"][3]
          and "src/cli.py:40" in by_title["Grep · parse\\("][3]
          and "no match" in by_title["mcp__demo__lookup · parser"][3])
    check("an empty result renders as (no output)", "_(no output)_" in by_title["Glob · **/*.py"][3])
    check("a block-list result is flattened to text", "src/main.py:12" in by_title["Grep · parse\\("][3])
    check("a long output is clipped at 60 lines with a truncation marker",
          "test_case_060" in doc and "test_case_061" not in doc and doc.count("… (truncated)") == 1)
    check("tool input is dropped (the Bash description is the title)",
          "pytest -q" not in doc and "import parser" not in doc and "old_string" not in doc)
    first = turns(doc)[0]
    pl = prose_lines(first)
    check("several text blocks of one reply are all kept, inline",
          "The first line indexes `tokens[0]` with no guard." in pl
          and "Let me confirm by running it on an empty list." in pl)
    check("prose and callouts interleave in the order they happened",
          first.index("I will start by reading") < first.index("[!note]- 📖")
          < first.index("The first line indexes") < first.index("[!failure]- 🖥️")
          < first.index("Confirmed, it raises") < first.index("[!note]- ✏️")
          < first.index("Fixed: an empty list"))
    check("the dashboard features the last reply and links the session",
          "Everything is written up in NOTES.md." in r["latest"]
          and "sessions/%s.md" % r["sid"] in r["latest"]
          and r["sid"] in r["index"])

    # ------------------------------------------------------------------ prompts-commands-compaction
    print("prompts-commands-compaction.jsonl")
    r = render(os.path.join(FIX, "prompts-commands-compaction.jsonl"))
    doc = r["session"]
    ts = turns(doc)
    check("hook exits 0, wrote the note, no error breadcrumb", r["rc"] == 0 and bool(doc) and r["errlog"] == "")
    check("nine turns (command+payload, image stubs and the compaction summary merge into their turn)",
          len(ts) == 9)
    check("every turn has a reply", all(split_you_claude(t)[1].strip() for t in ts))

    you, claude = split_you_claude(ts[0])
    check("slash command shows as `/review src/app.py`", "`/review src/app.py`" in prose_lines(you))
    check("command payload folds into a collapsed quote titled by the command",
          "> [!quote]- 📄 Injected `/review` skill payload · 54 lines" in you)
    check("payload text is only inside the fold",
          "Check rule number 1 against" in you
          and not any("Check rule number" in ln for ln in prose_lines(you)))
    check("local-command caveat boilerplate is dropped", "generated by the user while running" not in doc)
    check("reply follows the command turn", "Reviewing src/app.py now." in claude)

    you = split_you_claude(ts[1])[0]
    check("text+image prompt keeps the typed text", "What does this screenshot show?" in you)
    check("text+image prompt keeps the [Image: source: ...] stub as the image's placeholder, after the text",
          "[Image: source: /work/demo-app/shot-text.png]" in prose_lines(you)
          and you.index("What does this screenshot show?") < you.index("[Image: source"))
    check("no image bytes leak into the note", "iVBOR" not in doc)
    you = split_you_claude(ts[2])[0]
    check("an image-only prompt keeps the [Image: source: ...] stub as its placeholder",
          "[Image: source: /work/demo-app/shot-only.png]" in you)

    you = split_you_claude(ts[3])[0]
    check("a 46-line paste folds; the `## User Request` tail stays visible above the fold",
          "> [!quote]- 📄 Pasted context · Build log · 46 lines" in you
          and you.index("Please summarize the log above.") < you.index("[!quote]-"))
    check("the folded paste is not inline", not any("step 5 ok" in ln for ln in prose_lines(you)))
    you = split_you_claude(ts[4])[0]
    check("a prompt of exactly 40 lines is not folded",
          "[!quote]" not in you and "short-block line 40" in prose_lines(you))
    you = split_you_claude(ts[5])[0]
    check("a prompt of 41 lines folds, titled by its first line",
          "> [!quote]- 📄 Pasted context · long-block line 1 · 41 lines" in you
          and not any("long-block line" in ln for ln in prose_lines(you)))
    check("only the three long blocks fold as quotes", doc.count("> [!quote]-") == 3)

    you, claude = split_you_claude(ts[6])
    check("compaction summary folds into an info callout, never shown as typed",
          "> [!info]- 🗜️ Conversation compacted · summary written by Claude Code · 48 lines" in you
          and "COMPACT-SUMMARY-LINE 45" in you and "[!quote]" not in you
          and not any("COMPACT-SUMMARY-LINE" in ln for ln in prose_lines(you)))
    check("the compaction fold comes after the typed prompt",
          you.index("Carry on with the next task") < you.index("[!info]-"))
    check("the prompt typed after the compaction is shown and answered",
          "Carry on with the next task: add a changelog entry." in prose_lines(you)
          and "Added the changelog entry." in claude)
    check("the compact_boundary system entry itself is not rendered",
          doc.count("Conversation compacted") == 1 and "preTokens" not in doc)

    you, claude = split_you_claude(ts[7])
    check("an API error message renders as ordinary reply text, with no model tag",
          "API Error: 529 the service is overloaded" in claude
          and "**🤖 Claude**\n" in ts[7] and "<synthetic>" not in doc)
    check("the system api_error entry is not rendered separately", doc.count("overloaded") == 1)
    check("the turn after the error is a normal turn",
          "Retry that, please." in ts[8] and "Retried, the benchmark finished." in ts[8])

    # ------------------------------------------------------------------ noise-and-sidechain
    print("noise-and-sidechain.jsonl")
    r = render(os.path.join(FIX, "noise-and-sidechain.jsonl"))
    doc = r["session"]
    ts = turns(doc)
    check("hook exits 0, wrote the note, no error breadcrumb", r["rc"] == 0 and bool(doc) and r["errlog"] == "")
    check("two turns: subagent entries and non-message entries add none", len(ts) == 2)
    check("subagent (isSidechain) text, tools and results are not rendered",
          not any("SIDECHAIN" in x for x in (doc, r["latest"], r["index"])))
    cs = callouts(ts[0])
    check("the main-thread Task call renders as one collapsed callout with its result",
          len(cs) == 1 and cs[0][:3] == ("note", "🤖", "Task · Count TODO comments")
          and cs[0][4] and "7 TODO comments" in cs[0][3])
    check("the reply after the subagent returned is kept",
          "The subagent found 7 TODO comments." in prose_lines(ts[0]))
    check("the second turn is intact after the interleaved noise",
          "Thanks. Also list the files in docs." in ts[1] and "usage.md" in ts[1])
    for leak in ("queued thought", "While you were away", "Model switched", "bridge-1",
                 "future-entry-kind", "todo_reminder", "hook_success", "stop_hook_summary",
                 "durationMs", "trackedFileBackups"):
        if leak in doc or leak in r["latest"]:
            check("non-message entry content stays out (%s)" % leak, False)
            break
    else:
        check("non-message entries (attachment, queue-operation, ai-title, system subtypes, "
              "snapshots, an unknown type) leave no trace", True)
    check("thinking text appears nowhere", "THINKING-SENTINEL" not in doc + r["latest"] + r["index"])

    # ------------------------------------------------------------------ golden
    print("minimal.jsonl (golden)")
    r = render(os.path.join(FIX, "minimal.jsonl"))
    body = r["session"][r["session"].find("---"):] if "---" in r["session"] else ""
    golden = read(os.path.join(FIX, "minimal.expected.md"))
    check("turn rendering matches the golden file", body.rstrip("\n") == golden.rstrip("\n"))
    check("the note starts with the session heading",
          r["session"].startswith("# Claude session `44444444` — demo-app"))

    # ------------------------------------------------------------------ edge transcripts
    print("edge transcripts")
    d = tempfile.mkdtemp(prefix="renderer-fixtures-src-")
    _dirs.append(d)
    only_noise = os.path.join(d, "only-noise.jsonl")
    with open(only_noise, "w", encoding="utf-8") as f:
        sid = "55555555-aaaa-4bbb-8ccc-000000000005"
        for row in ({"type": "mode", "mode": "normal", "sessionId": sid},
                    {"type": "future-entry-kind-v9", "sessionId": sid},
                    {"type": "last-prompt", "lastPrompt": "x", "sessionId": sid}):
            f.write(json.dumps(row) + "\n")
        f.write("not json\n")
    r = render(only_noise)
    check("a transcript with no messages writes no note and does not crash",
          r["rc"] == 0 and r["session"] == "" and r["latest"] == "" and r["errlog"] == "")
    r = render(os.path.join(d, "does-not-exist.jsonl"))
    check("a missing transcript path is a quiet no-op", r["rc"] == 0 and r["session"] == "")

    # ------------------------------------------------------------------ prompt groups, in-process
    print("prompt groups and titles (in-process)")
    import importlib.util
    spec = importlib.util.spec_from_file_location("obsidian_mirror", MIRROR)
    om = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(om)
    img = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "iVBORw0KGgo="}}
    md, gist = om.render_prompt_group(
        [{"message": {"role": "user", "content": [{"type": "text", "text": "Compare these two."}, img, img]}}], {})
    check("images with no [Image: source] stub get one _[image]_ placeholder each",
          md.count("_[image]_") == 2 and md.index("Compare these two.") < md.index("_[image]_")
          and gist == "Compare these two." and "iVBOR" not in md)
    summary = {"isCompactSummary": True, "message": {"role": "user",
               "content": "This session is being continued.\nSummary:\nstep one\nstep two"}}
    md, gist = om.render_prompt_group([summary], {})
    check("a compaction summary alone is a folded info callout with no gist",
          md.startswith(om.COMPACT_CALLOUT + " · 4 lines\n>\n> This session") and gist == "")
    note = os.path.join(tempfile.mkdtemp(prefix="renderer-parse-"), "%s.md" % ("1" * 8 + "-1111-4111-8111-" + "1" * 12))
    _dirs.append(os.path.dirname(note))
    doc, _, _ = om.render_session_file([
        dict(summary, type="user", timestamp="2026-03-02T09:00:00.000Z"),
        {"type": "assistant", "timestamp": "2026-03-02T09:00:05.000Z",
         "message": {"role": "assistant", "content": [{"type": "text", "text": "Picking up step three."}]}},
    ], "11111111", "demo-app")
    with open(note, "w", encoding="utf-8") as f:
        f.write(doc)
    row = om._parse_session_md(note)
    check("a row rebuilt from the note never shows the compaction summary as the last prompt",
          row is not None and row["prompt"] == "")
    def rebuilt_prompt(prompt_text):
        path = os.path.join(os.path.dirname(note), "%s.md" % ("2" * 8 + "-2222-4222-8222-" + "2" * 12))
        doc, _, _ = om.render_session_file([
            {"type": "user", "timestamp": "2026-03-02T10:00:00.000Z",
             "message": {"role": "user", "content": prompt_text}},
            {"type": "assistant", "timestamp": "2026-03-02T10:00:05.000Z",
             "message": {"role": "assistant", "content": [{"type": "text", "text": "Done."}]}},
        ], "22222222", "demo-app")
        with open(path, "w", encoding="utf-8") as f:
            f.write(doc)
        return (om._parse_session_md(path) or {}).get("prompt")
    paste = "\n".join(["Stack trace from the build"] + ["  at frame %d" % i for i in range(60)])
    check("a row rebuilt from a folded paste with no request shows the paste's first line",
          rebuilt_prompt(paste) == "Stack trace from the build")
    check("a rebuilt row keeps a typed prompt's leading >",
          rebuilt_prompt("> quoted line from the docs") == "> quoted line from the docs")
    check("Grep and Glob are titled by their pattern, even with a path",
          om.tool_title("Grep", {"pattern": "TODO", "path": "src"}) == "Grep · TODO"
          and om.tool_title("Glob", {"pattern": "*.md", "path": "docs"}) == "Glob · *.md"
          and om.tool_title("Grep", {"path": "src"}) == "Grep · src")

    # ------------------------------------------------------------------ all fixtures
    print("every fixture")
    for name in sorted(os.listdir(FIX)):
        if not name.endswith(".jsonl"):
            continue
        r = render(os.path.join(FIX, name))
        d = r["session"]
        ok = (r["rc"] == 0 and d.startswith("# Claude session") and r["errlog"] == ""
              and "THINKING-SENTINEL" not in d
              and all(c[4] for c in callouts(d))
              and "\"type\"" not in d)
        check("%s renders cleanly (rc 0, no breadcrumb, no thinking, callouts collapsed)" % name, ok)


try:
    main()
finally:
    for d in _dirs:
        shutil.rmtree(d, ignore_errors=True)
if _fails:
    print("FAILED: %d check(s)" % len(_fails))
    sys.exit(1)
print("all renderer-fixture checks passed")
