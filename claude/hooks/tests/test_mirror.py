#!/usr/bin/env python3
"""tests for hooks/lib/mirror.py: the session-mirror on-disk format contract. Round-trips the writer templates through BOTH parser layers (the reviews'
strict TURN_RE/HEADER_RE/USER_RE trio and the writer's tolerant MD_* recovery set) and
runs gather() against a synthetic sessions dir, so a writer-side format tweak can never
again silently zero every review into a confident, false "No CLI activity" push, the
failure class this lib was extracted to prevent.
Self-contained: temp dirs only, no network, never touches the real mirror or ~/.claude.
Run:  python3 claude/hooks/tests/test_mirror.py"""
import datetime
import os
import shutil
import sys
import tempfile

HOOKS = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, os.path.join(HOOKS, "lib"))
import mirror

_fails = []


def check(name, cond):
    print(("  ✓ " if cond else "  ✗ ") + name)
    if not cond:
        _fails.append(name)


def render_session(short, project, machine, turns, headless=None):
    """Assemble a session doc exactly the way obsidian-mirror.render_session_file does:
    H1, blank, machine line, blank, headless marker, blank, then per turn `---`, blank,
    turn_block, blank, duplicated here ON PURPOSE so a drive-by change to the writer's
    assembly order breaks this test instead of silently breaking the readers."""
    out = [mirror.session_h1(short, project), ""]
    if machine:
        out += [mirror.machine_line(machine), ""]
    if headless:
        out += [mirror.headless_line(headless), ""]
    for ts, human, reply in turns:
        out += ["---", "", mirror.turn_block(ts, human, reply), ""]
    return "\n".join(out)


def test_roundtrip():
    print("writer templates ↔ reader regexes")
    turns = [("2026-07-03 08:15:00", "fix the fence guard please", "Done."),
             ("2026-07-03 09:30:45", "> quoted noise\nwrite the round-trip test", "OK.")]
    doc = render_session("abcd1234", "myproject", "mini", turns)

    mh = mirror.HEADER_RE.search(doc)
    check("HEADER_RE recovers the project", bool(mh) and mh.group(1).strip() == "myproject")
    marks = list(mirror.TURN_RE.finditer(doc))
    check("TURN_RE finds every stamp", [m.group(1) for m in marks] == [t[0] for t in turns])
    check("stamps parse back with TS_FMT",
          all(datetime.datetime.strptime(m.group(1), mirror.TS_FMT) for m in marks))
    prompts = []
    for i, m in enumerate(marks):
        seg = doc[m.end(): (marks[i + 1].start() if i + 1 < len(marks) else len(doc))]
        um = mirror.USER_RE.search(seg)
        prompts.append(mirror.clean_prompt(um.group(1)) if um else None)
    check("USER_RE + clean_prompt recover a plain prompt", prompts[0] == "fix the fence guard please")
    check("clean_prompt skips quote bars", prompts[1] == "write the round-trip test")

    # the writer's own tolerant parsers (dashboard-row recovery layer)
    check("tolerant MD_H1 agrees", mirror.MD_H1.search(doc).group(1) == "myproject")
    check("tolerant MD_MACHINE agrees", mirror.MD_MACHINE.search(doc).group(1) == "mini")
    check("tolerant MD_TS agrees", mirror.MD_TS.findall(doc) == [t[0] for t in turns])

    # documented divergence (kept deliberate, see mirror.py): a projectless H1 passes
    # the reader trio but not the writer-side parser.
    check("HEADER_RE tolerates a missing project",
          mirror.HEADER_RE.search("# Claude session `beef0000`") is not None)
    check("MD_H1 requires the project suffix",
          mirror.MD_H1.search("# Claude session `beef0000`") is None)


def test_clean_prompt_clip():
    print("clean_prompt clipping")
    long = "word " * 200
    check("default clip ≤ MAX_PROMPT_CHARS", len(mirror.clean_prompt(long)) <= mirror.MAX_PROMPT_CHARS)
    check("a 200-char clip is honored", len(mirror.clean_prompt(long, 200)) <= 200)
    check("noise-only block cleans to empty",
          mirror.clean_prompt("> quoted\n```\n**bold**\n---\n[!note]\n# heading\n| cell |") == "")


def test_gather():
    print("gather() on a synthetic mirror dir")
    root = tempfile.mkdtemp(prefix="mirror-test-")
    os.makedirs(os.path.join(root, "sessions"))
    # paths.mirror_dir(): an explicit env var wins over paths.env/default
    os.environ["CLAUDE_OBSIDIAN_MIRROR_DIR"] = root
    try:
        now = datetime.datetime.now()
        fmt = lambda dt: dt.strftime(mirror.TS_FMT)
        doc_a = render_session("aaaa1111", "projA", "mini", [
            (fmt(now - datetime.timedelta(hours=1)), "recent turn in project A", "r"),
            (fmt(now - datetime.timedelta(hours=30)), "old turn outside the window", "r"),
            (fmt(now - datetime.timedelta(minutes=30)), "tiny", "r"),  # <6 chars → dropped
        ])
        doc_b = render_session("bbbb2222", "projB", None, [
            (fmt(now - datetime.timedelta(minutes=10)), "newest turn in project B", "r"),
        ])
        for name, doc in (("a.md", doc_a), ("b.md", doc_b)):
            with open(os.path.join(root, "sessions", name), "w", encoding="utf-8") as f:
                f.write(doc)
        items = mirror.gather(hours=24)
        check("window + min-length filters applied", len(items) == 2)
        check("newest first", [i[0] for i in items] == ["projB", "projA"])
        check("prompt text survives the full read path",
              items[1][2] == "recent turn in project A")
        check("timestamps are datetimes in-window",
              all(isinstance(i[1], datetime.datetime) and i[1] > now - datetime.timedelta(hours=24)
                  for i in items))
    finally:
        del os.environ["CLAUDE_OBSIDIAN_MIRROR_DIR"]
        shutil.rmtree(root, ignore_errors=True)


def test_model_tag():
    print("reply model tag")
    check("short_model strips the vendor prefix", mirror.short_model("claude-opus-4-8") == "opus-4-8")
    check("short_model passes an unprefixed id through", mirror.short_model("gpt-x") == "gpt-x")
    check("short_model drops None / '' / synthetic",
          mirror.short_model(None) == "" and mirror.short_model("") == ""
          and mirror.short_model("<synthetic>") == "")

    plain = mirror.turn_block("2026-07-06 10:00:00", "one two three four", "body")
    check("model=None is byte-identical to the pre-tag line",
          mirror.turn_block("2026-07-06 10:00:00", "one two three four", "body", None) == plain)
    check("model='<synthetic>' is byte-identical too",
          mirror.turn_block("2026-07-06 10:00:00", "one two three four", "body", "<synthetic>") == plain)

    tagged = mirror.turn_block("2026-07-06 10:00:00", "one two three four", "body", "claude-fable-5")
    check("tag renders on the Claude line", "**🤖 Claude** · `fable-5`" in tagged)
    um = mirror.USER_RE.search(tagged)
    check("USER_RE still terminates at the marker (human intact)",
          bool(um) and mirror.clean_prompt(um.group(1)) == "one two three four")
    check("TURN_RE still recovers the stamp on a tagged turn",
          bool(mirror.TURN_RE.search(tagged)) and mirror.TURN_RE.search(tagged).group(1) == "2026-07-06 10:00:00")


def test_gather_survives_tags():
    print("gather() on a tagged mirror dir (the reviews must not break)")
    root = tempfile.mkdtemp(prefix="mirror-tagtest-")
    os.makedirs(os.path.join(root, "sessions"))
    os.environ["CLAUDE_OBSIDIAN_MIRROR_DIR"] = root
    try:
        now = datetime.datetime.now()
        ts = (now - datetime.timedelta(minutes=20)).strftime(mirror.TS_FMT)
        # a fully-tagged session, assembled through the real templates
        doc = "\n".join([mirror.session_h1("cccc3333", "projC"), "",
                         mirror.machine_line("mini"), "",
                         "---", "",
                         mirror.turn_block(ts, "recent tagged prompt here", "r", "claude-opus-4-8"),
                         ""])
        with open(os.path.join(root, "sessions", "c.md"), "w", encoding="utf-8") as f:
            f.write(doc)
        items = mirror.gather(hours=24)
        check("tagged session still yields its turn", len(items) == 1)
        check("prompt survives past the model tag",
              items and items[0][2] == "recent tagged prompt here")
        check("project still recovered from a tagged file", items and items[0][0] == "projC")
    finally:
        del os.environ["CLAUDE_OBSIDIAN_MIRROR_DIR"]
        shutil.rmtree(root, ignore_errors=True)


def test_gather_headless():
    print("gather() headless exclusion (machine sessions are left out of the reviews)")
    root = tempfile.mkdtemp(prefix="mirror-headless-")
    os.makedirs(os.path.join(root, "sessions"))
    os.environ["CLAUDE_OBSIDIAN_MIRROR_DIR"] = root
    try:
        now = datetime.datetime.now()
        fmt = lambda dt: dt.strftime(mirror.TS_FMT)
        # a machine session: 2 turns in-window, 1 outside, only the 2 count
        doc_m = render_session("dddd4444", "botproj", "mini", [
            (fmt(now - datetime.timedelta(hours=1)), "You compile facts. Respond with JSON.", "r"),
            (fmt(now - datetime.timedelta(hours=2)), "You compile more facts as JSON.", "r"),
            (fmt(now - datetime.timedelta(hours=30)), "old machine turn outside window", "r"),
        ], headless="sdk-cli")
        # a user session that merely QUOTES the marker inside a turn: still included
        doc_q = render_session("eeee5555", "opproj", "mini", [
            (fmt(now - datetime.timedelta(minutes=15)),
             "why does this line\n" + mirror.headless_line("sdk-cli") + "\nappear in the mirror?",
             "Explained."),
        ])
        for name, doc in (("m.md", doc_m), ("q.md", doc_q)):
            with open(os.path.join(root, "sessions", name), "w", encoding="utf-8") as f:
                f.write(doc)
        counts = {}
        items = mirror.gather(hours=24, machine_counts=counts)
        check("machine session yields no items", all(i[0] != "botproj" for i in items))
        check("quoting session still yields its turn",
              len(items) == 1 and items[0][0] == "opproj")
        check("excluded turns tallied per project (window-scoped)",
              counts == {"botproj": 2})
        check("gather without the out-param still just filters",
              len(mirror.gather(hours=24)) == 1)
        note = mirror.machine_summary(counts)
        check("machine_summary discloses project and count",
              "botproj: 2" in note and "2 machine-run turn(s)" in note)
        check("machine_summary is empty for empty counts", mirror.machine_summary({}) == "")
    finally:
        del os.environ["CLAUDE_OBSIDIAN_MIRROR_DIR"]
        shutil.rmtree(root, ignore_errors=True)


def test_mirror_dir():
    print("where session notes go (paths.mirror_dir)")
    import paths
    fd, env = tempfile.mkstemp(suffix=".env")
    os.close(fd)
    saved = {k: os.environ.pop(k, None) for k in ("CLAUDE_OBSIDIAN_MIRROR_DIR", "CLAUDE_VAULT_ROOT", "CLAUDE_PATHS_ENV")}
    os.environ["CLAUDE_PATHS_ENV"] = env
    try:
        open(env, "w").write("# nothing set\n")
        check("no vault, no key → off ('')", paths.mirror_dir() == "")
        check("…and sessions_dir() is '' too, never a relative 'sessions'", mirror.sessions_dir() == "")
        check("…and gather() reads nothing", mirror.gather(hours=24) == [])
        open(env, "w").write('CLAUDE_VAULT_ROOT="/v/My Vault"\n')
        check("vault only → <vault>/_claude-live", paths.mirror_dir() == "/v/My Vault/_claude-live")
        open(env, "w").write('CLAUDE_VAULT_ROOT="/v"\nCLAUDE_OBSIDIAN_MIRROR_DIR="/elsewhere/live"\n')
        check("an explicit key wins", paths.mirror_dir() == "/elsewhere/live")
        open(env, "w").write('CLAUDE_VAULT_ROOT="/v"\nCLAUDE_OBSIDIAN_MIRROR_DIR=""\n')
        check("a key set to '' turns the notes off", paths.mirror_dir() == "")
        os.environ["CLAUDE_OBSIDIAN_MIRROR_DIR"] = "/from/env"
        check("the environment beats the file", paths.mirror_dir() == "/from/env")
    finally:
        for k, v in saved.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v
        os.unlink(env)


def test_notes_excluded():
    print("per-project opt-out (notes_excluded)")
    home = tempfile.mkdtemp(prefix="mirror-optout-")
    fd, env = tempfile.mkstemp(suffix=".env")
    os.close(fd)
    saved = {k: os.environ.pop(k, None) for k in ("CLAUDE_NOTES_EXCLUDE", "CLAUDE_PATHS_ENV", "HOME")}
    os.environ["CLAUDE_PATHS_ENV"] = env
    os.environ["HOME"] = home
    mark = lambda d: open(os.path.join(d, mirror.NO_NOTES_FILE), "w").close()
    try:
        dev = os.path.join(home, "dev")
        proj = os.path.join(dev, "proj")
        deep = os.path.join(proj, "src", "deep")
        os.makedirs(deep)
        open(env, "w").write("# nothing set\n")
        check("no file, no key → copied", not mirror.notes_excluded(deep))
        check("no start dir → copied", not mirror.notes_excluded(None) and not mirror.notes_excluded(""))
        mark(proj)
        check("the file in the start dir opts out", mirror.notes_excluded(proj))
        check("…and in any folder above it", mirror.notes_excluded(deep))
        check("…but not in a sibling project", not mirror.notes_excluded(os.path.join(dev, "other")))
        os.remove(os.path.join(proj, mirror.NO_NOTES_FILE))
        mark(home)
        check("a file in $HOME itself does not count", not mirror.notes_excluded(deep))
        os.remove(os.path.join(home, mirror.NO_NOTES_FILE))
        mark(dev)
        os.makedirs(os.path.join(home, "elsewhere"))
        link = os.path.join(home, "elsewhere", "proj")
        os.symlink(proj, link)
        check("a project reached through a symlink is judged by its real place too",
              mirror.notes_excluded(link))
        os.remove(os.path.join(dev, mirror.NO_NOTES_FILE))

        open(env, "w").write('CLAUDE_NOTES_EXCLUDE="$HOME/clients \'$HOME/My Work\' relative/dir"\n')
        check("a folder listed in paths.env opts out what is inside it",
              mirror.notes_excluded(os.path.join(home, "clients", "acme")))
        check("…and the folder itself", mirror.notes_excluded(os.path.join(home, "clients")))
        check("…not a folder whose name only starts the same",
              not mirror.notes_excluded(os.path.join(home, "clients2")))
        check("a quoted path with a space works", mirror.notes_excluded(os.path.join(home, "My Work", "x")))
        check("a relative entry is ignored",
              not mirror.notes_excluded(os.path.join(os.getcwd(), "relative", "dir")))
        open(env, "w").write('CLAUDE_NOTES_EXCLUDE="$HOME/it\'s here"\n')
        check("an unbalanced quote falls back to plain splitting, never raises",
              mirror.notes_excluded(os.path.join(home, "it's", "x")) is True)
        os.environ["CLAUDE_NOTES_EXCLUDE"] = "~/from-env"
        check("the environment beats the file, and ~ expands",
              mirror.notes_excluded(os.path.join(home, "from-env", "a"))
              and not mirror.notes_excluded(os.path.join(home, "clients", "acme")))
    finally:
        for k, v in saved.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v
        os.unlink(env)
        shutil.rmtree(home, ignore_errors=True)


if __name__ == "__main__":
    test_mirror_dir()
    test_roundtrip()
    test_clean_prompt_clip()
    test_gather()
    test_model_tag()
    test_gather_survives_tags()
    test_gather_headless()
    test_notes_excluded()
    print(f"\n{'ALL PASS' if not _fails else 'FAILED: ' + ', '.join(_fails)}")
    sys.exit(1 if _fails else 0)
