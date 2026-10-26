#!/usr/bin/env python3
"""tests for the vault-copy cap on session notes: lib/mirror.trim_note / archive_line /
MD_ARCHIVE / full_text, and obsidian-mirror.write_note / --trim-oversize. Obsidian's
indexer runs out of memory on multi-MB notes (Obsidian forum bug 115709) and the window
never opens, so the vault keeps the newest turns of a long session and the full note lives
outside the vault. Pinned here: a small note is byte-identical; a trimmed note stays under
the cap, keeps its header and newest turns, carries the archive marker in the header
region only, and every reader (the reviews' gather(), the handoff pointer's
sessions_since(), the writer's row parser) still works; a purge-shrunk note takes its
archive with it; the one-off --trim-oversize keeps mtimes and is idempotent.
Self-contained: temp dirs only, never touches the real mirror or ~/.claude.
Run:  python3 claude/hooks/tests/test_note_trim.py"""
import datetime
import importlib.util
import os
import shutil
import sys
import tempfile

HOOKS = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
TMP = tempfile.mkdtemp(prefix="note-trim-")
HOME = os.path.join(TMP, "home")
PROJ = os.path.join(HOME, "proj")
os.makedirs(PROJ)
os.environ["HOME"] = HOME
os.environ["CLAUDE_PATHS_ENV"] = os.path.join(TMP, "paths.env")      # absent: no real paths.env
os.environ["CLAUDE_OBSIDIAN_MIRROR_DIR"] = os.path.join(TMP, "vault", "_claude-live")
os.environ["CLAUDE_MIRROR_ARCHIVE_DIR"] = os.path.join(TMP, "archive")
sys.path.insert(0, os.path.join(HOOKS, "lib"))
import mirror  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "obsidian_mirror", os.path.join(HOOKS, "obsidian-mirror.py"))
om = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(om)

SDIR = mirror.sessions_dir()
ADIR = mirror.archive_dir()
SID = "0123abcd-0000-4000-8000-00000000abcd"
ARC = os.path.join(ADIR, f"{SID}.md")
_fails = []


def check(name, cond):
    print(("  ✓ " if cond else "  ✗ ") + name)
    if not cond:
        _fails.append(name)


def render(turns, project_dir=PROJ):
    """Assemble a note the way obsidian-mirror.render_session_file does (duplicated on
    purpose, as test_mirror.py does, so a writer-side reorder breaks a test)."""
    out = [mirror.session_h1(SID[:8], "proj"), "", mirror.machine_line("air"), "",
           mirror.project_dir_line(project_dir), ""]
    for ts, human, reply in turns:
        out += ["---", "", mirror.turn_block(ts, human, reply), ""]
    return "\n".join(out)


def stamps(n):
    base = datetime.datetime.now().replace(microsecond=0) - datetime.timedelta(minutes=n + 1)
    return [(base + datetime.timedelta(minutes=i)).strftime(mirror.TS_FMT) for i in range(n)]


SMALL = render([(ts, f"small prompt {i} please", "ok") for i, ts in enumerate(stamps(3))])
BIG_TURNS = [(ts, f"big prompt {i} please", "x" * 2500) for i, ts in enumerate(stamps(400))]
BIG = render(BIG_TURNS)


def test_trim_note():
    print("trim_note")
    check("a note under the cap is byte-identical", mirror.trim_note(SMALL, ARC) == (SMALL, 3, 3))
    check("the fixture is over the cap", len(BIG.encode("utf-8")) > mirror.MAX_NOTE_BYTES)
    note, kept, total = mirror.trim_note(BIG, ARC)
    check("total counts every turn", total == 400)
    check("some but not all turns kept", 0 < kept < total)
    check("trimmed note is under the cap", len(note.encode("utf-8")) <= mirror.MAX_NOTE_BYTES)
    marks = list(mirror.TURN_RE.finditer(note))
    check("the newest turns are kept, in order",
          [m.group(1) for m in marks] == [t[0] for t in BIG_TURNS[-kept:]])
    head = note[:marks[0].start()]
    mk = mirror.MD_ARCHIVE.search(head)
    check("archive marker sits in the header region", bool(mk))
    check("marker names the archive path", bool(mk) and mk.group(1) == ARC)
    check("H1 survives", mirror.HEADER_RE.search(note).group(1) == "proj")
    check("host label survives", mirror.MD_MACHINE.search(note).group(1) == "air")
    check("project-dir marker survives", mirror.MD_PROJECT_DIR.search(head).group(1) == PROJ)
    um = mirror.USER_RE.search(note[marks[-1].end():])
    check("last prompt parses", bool(um) and mirror.clean_prompt(um.group(1)) == "big prompt 399 please")
    check("writer's row parser reads the last stamp", mirror.MD_TS.findall(note)[-1] == BIG_TURNS[-1][0])
    one = render([(stamps(1)[0], "one giant turn", "y" * (mirror.MAX_NOTE_BYTES + 1000))])
    n1, k1, t1 = mirror.trim_note(one, ARC)
    check("a single over-cap turn is still kept", (k1, t1) == (1, 1) and n1 == one)
    quoted = render([(stamps(1)[0], "look:\n" + mirror.archive_line(ARC, 1, 2, 3e6), "seen")])
    qm = mirror.TURN_RE.search(quoted)
    check("a quoted marker does not count as the header marker",
          not mirror.MD_ARCHIVE.search(quoted[:qm.start()]))
    check("archive_line refuses a path with a backtick", mirror.archive_line("/a/`b`", 1, 2, 3) == "")


def test_full_text_and_gather():
    print("full_text + gather follow the archive")
    os.makedirs(SDIR, exist_ok=True)
    os.makedirs(ADIR, exist_ok=True)
    note, kept, total = mirror.trim_note(BIG, ARC)
    with open(os.path.join(SDIR, f"{SID}.md"), "w", encoding="utf-8") as f:
        f.write(note)
    with open(ARC, "w", encoding="utf-8") as f:
        f.write(BIG)
    check("full_text returns the archived note", mirror.full_text(note) == BIG)
    check("full_text leaves an untrimmed note alone", mirror.full_text(SMALL) == SMALL)
    check("gather sees every turn through the archive", len(mirror.gather(hours=48)) == total)
    hits = mirror.sessions_since(PROJ, 0, home=HOME, transcripts=os.path.join(TMP, "none"))
    check("sessions_since lists the session from its last kept turn",
          len(hits) == 1 and hits[0]["ts"] == BIG_TURNS[-1][0]
          and hits[0]["prompt"] == "big prompt 399 please")
    os.remove(ARC)
    check("without the archive gather degrades to the kept turns", len(mirror.gather(hours=48)) == kept)
    check("full_text falls back when the archive is gone", mirror.full_text(note) == note)
    stray = os.path.join(TMP, "stray.md")
    with open(stray, "w", encoding="utf-8") as f:
        f.write(BIG)
    forged = note.replace(ARC, stray)
    check("a marker pointing outside the archive folder is not followed",
          mirror.full_text(forged) == forged)


def test_write_note():
    print("write_note")
    om_note = os.path.join(om.SESS, f"{SID}.md")
    check("module sees the sandbox dirs", om.SESS == SDIR and om.archive_path(SID) == ARC)
    kept, total = om.write_note(SID, BIG)
    with open(om_note, encoding="utf-8") as f:
        vault = f.read()
    check("big note: vault copy is trimmed",
          kept < total and len(vault.encode("utf-8")) <= mirror.MAX_NOTE_BYTES)
    with open(ARC, encoding="utf-8") as f:
        check("big note: archive holds the full render", f.read() == BIG)
    kept, total = om.write_note(SID, SMALL)
    with open(om_note, encoding="utf-8") as f:
        vault = f.read()
    check("shrunk note: vault copy is the full note", (kept, total) == (3, 3) and vault == SMALL)
    check("shrunk note: the stale archive is removed", not os.path.exists(ARC))


def test_trim_oversize():
    print("--trim-oversize on an existing raw note")
    p = os.path.join(SDIR, f"{SID}.md")
    with open(p, "w", encoding="utf-8") as f:
        f.write(BIG)
    old = 1_700_000_000
    os.utime(p, (old, old))
    om.trim_oversize()
    check("note is under the cap afterwards", os.path.getsize(p) <= mirror.MAX_NOTE_BYTES)
    with open(ARC, encoding="utf-8") as f:
        check("archive has the full note", f.read() == BIG)
    check("note mtime preserved", int(os.path.getmtime(p)) == old)
    check("archive mtime preserved", int(os.path.getmtime(ARC)) == old)
    with open(p, encoding="utf-8") as f:
        before = f.read()
    om.trim_oversize()
    with open(p, encoding="utf-8") as f:
        check("a second run changes nothing", f.read() == before)


if __name__ == "__main__":
    try:
        test_trim_note()
        test_full_text_and_gather()
        test_write_note()
        test_trim_oversize()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    if _fails:
        print(f"\nFAILED: {len(_fails)}")
        for name in _fails:
            print("  ✗ " + name)
        sys.exit(1)
    print("\nall OK")
