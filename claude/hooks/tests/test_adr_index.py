#!/usr/bin/env python3
"""tests for adr-index.py: the record bookkeeping /handoff used to describe in prose.
`next` resolves the records folder in step 1a's order (.adr-dir, log4brains, the usual
folders, docs/adr), continues the numbering with the same padding and names the style of
the latest record; `moc` regenerates the vault's decisions MOC keeping an existing header
and the lines it already has; `trim` keeps the 20 newest index entries in CLAUDE.md and
adds the pointer to the rest, touching nothing else. Self-contained: temp dirs only.
Run:  python3 claude/hooks/tests/test_adr_index.py"""
import os
import shutil
import subprocess
import sys
import tempfile

HOOKS = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
TOOL = os.path.join(HOOKS, "adr-index.py")

_fails = []


def check(name, cond):
    print(("  ✓ " if cond else "  ✗ ") + name)
    if not cond:
        _fails.append(name)


T = os.path.realpath(tempfile.mkdtemp(prefix="adr-index-"))   # the tool prints real paths
ENV = dict(os.environ, HOME=os.path.join(T, "home"))
os.makedirs(ENV["HOME"])


def tool(*args):
    p = subprocess.run([sys.executable, TOOL] + list(args), capture_output=True, text=True, env=ENV)
    return p.returncode, p.stdout, p.stderr


def kv(out):
    return dict(ln.split("=", 1) for ln in out.splitlines() if "=" in ln)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


VAULT_REC = "---\ntags: [adr, adr/%s]\ndate: %s\n---\n# %s. %s\n\n## Context\nx\n## Decision\n%s\n## Consequences\ny\n"
PLAIN_REC = "# %s. %s\n\n- Status: %s\n- Date: %s\n\n## Context\nx\n## Decision\n%s\n"
MADR_REC = "# %s\n\n* Status: %s\n* Date: %s\n\n## Context and Problem Statement\nx\n\n## Decision Outcome\n\nChosen option: \"%s\", because y.\n"

try:
    print("next in a vault with three records")
    V = os.path.join(T, "vault")
    os.makedirs(os.path.join(V, ".obsidian"))
    P = os.path.join(V, "proj")
    adr = os.path.join(P, "docs", "adr")
    write(os.path.join(adr, "0001-keep-notes-plain.md"), VAULT_REC % ("accepted", "2026-02-01", "0001", "Keep notes as plain Markdown", "Store every note as a Markdown file."))
    write(os.path.join(adr, "0002-sync-by-rsync.md"), VAULT_REC % ("superseded", "2026-02-03", "0002", "Sync by rsync", "Use rsync nightly.") + "\nSuperseded by [[0003-sync-by-obsidian-sync|DEC-0003]].\n")
    write(os.path.join(adr, "0003-sync-by-obsidian-sync.md"), VAULT_REC % ("accepted", "2026-02-10", "0003", "Sync by Obsidian Sync", "**Obsidian Sync** carries the vault; rsync is retired."))
    write(os.path.join(adr, "0000-template.md"), "# template\n")
    rc, out, err = tool("next", "--root", P)
    d = kv(out)
    check("exits 0", rc == 0)
    check("docs/adr found, three records, next 0004", d.get("adr_dir") == adr and d.get("count") == "3" and d.get("next") == "0004")
    check("pad 4, pattern, vault style", d.get("pad") == "4" and d.get("pattern") == "NNNN-kebab-title.md" and d.get("style") == "vault")
    check("inside a vault", d.get("vault") == "yes" and d.get("latest", "").endswith("0003-sync-by-obsidian-sync.md"))

    print("moc from scratch")
    rc, out, err = tool("moc", "--root", P)
    check("prints the page without writing", rc == 0 and not os.path.exists(os.path.join(adr, "_decisions-moc.md")))
    check("default header names the project and the folder", "# Decisions — proj" in out and "`docs/adr/`" in out)
    acc = out.split("## Accepted", 1)[1].split("## Superseded", 1)[0]
    check("accepted newest first, one line from the Decision section, date at the end",
          acc.strip().splitlines()[0] == "- [[0003-sync-by-obsidian-sync|DEC-0003]] — Obsidian Sync carries the vault; rsync is retired · 2026-02-10"
          and "- [[0001-keep-notes-plain|DEC-0001]] — Store every note as a Markdown file · 2026-02-01" in acc)
    check("superseded section links the successor",
          "## Superseded" in out and "- [[0002-sync-by-rsync|DEC-0002]] — Use rsync nightly · superseded by [[0003-sync-by-obsidian-sync|DEC-0003]]" in out)
    check("no Proposed section when empty, template left out", "## Proposed" not in out and "0000" not in out)

    print("moc keeps an existing header and curated lines")
    write(os.path.join(adr, "_decisions-moc.md"),
          "---\ntags: [adr, moc]\n---\n# Decisions — My Project\n\n> [!info] Custom callout. See [[OVERVIEW]].\n\n"
          "## Accepted\n\n- [[0001-keep-notes-plain|DEC-0001]] — notes stay plain Markdown, curated wording · 2026-02-01\n")
    rc, out, err = tool("moc", "--root", P, "--write")
    moc = open(os.path.join(adr, "_decisions-moc.md")).read()
    check("writes the file", rc == 0 and "wrote" in out)
    check("the custom header survives", "# Decisions — My Project" in moc and "Custom callout. See [[OVERVIEW]]" in moc)
    check("the curated line is kept, the new record gets a generated line",
          "notes stay plain Markdown, curated wording" in moc and "[[0003-sync-by-obsidian-sync|DEC-0003]] — Obsidian Sync" in moc)
    check("the moc itself is not listed as a record", "_decisions-moc" not in moc.split("## Accepted", 1)[1])

    print("moc: a Decision section that opens with a list yields the title")
    write(os.path.join(adr, "0004-list-decision.md"), VAULT_REC % ("accepted", "2026-03-01", "0004", "Keep the title as the ruling", "- first bullet of a list\n- second"))
    rc, out, err = tool("moc", "--root", P)
    check("the line is the title, not the first bullet", "- [[0004-list-decision|DEC-0004]] — Keep the title as the ruling · 2026-03-01" in out and "first bullet" not in out)
    os.remove(os.path.join(adr, "0004-list-decision.md"))

    print("moc keeps a listed record in its section; a new status gets its own")
    write(os.path.join(adr, "0004-moved-elsewhere.md"), VAULT_REC % ("moved", "2026-03-01", "0004", "Moved elsewhere", "Lives in the other repo now."))
    write(os.path.join(adr, "0005-also-moved.md"), VAULT_REC % ("moved", "2026-03-02", "0005", "Also moved", "Same."))
    write(os.path.join(adr, "_decisions-moc.md"),
          "---\ntags: [adr, moc]\n---\n# Decisions — My Project\n\n> [!info] c\n\n## Accepted\n\n"
          "- [[0004-moved-elsewhere|DEC-0004]] — kept under accepted by hand · 2026-03-01\n")
    rc, out, err = tool("moc", "--root", P)
    acc = out.split("## Accepted", 1)[1].split("\n## ", 1)[0]
    check("the hand-placed record stays under Accepted with its line", "kept under accepted by hand" in acc)
    check("the new moved record gets a Moved section", "## Moved" in out and "[[0005-also-moved|DEC-0005]] — Same · 2026-03-02" in out.split("## Moved", 1)[1])
    for n in ("0004-moved-elsewhere.md", "0005-also-moved.md", "_decisions-moc.md"):
        os.remove(os.path.join(adr, n))

    print("next: plain, madr, .adr-dir, log4brains, greenfield")
    Q = os.path.join(T, "plain")
    write(os.path.join(Q, "docs", "decisions", "0007-use-postgres.md"), PLAIN_REC % ("0007", "Use Postgres", "accepted", "2026-01-12", "Postgres."))
    d = kv(tool("next", "--root", Q)[1])
    check("docs/decisions, plain style, next 0008", d.get("adr_dir", "").endswith("docs/decisions") and d.get("style") == "plain" and d.get("next") == "0008" and d.get("vault") == "no")
    M = os.path.join(T, "madr")
    write(os.path.join(M, "adr", "00012-expose-http.md"), MADR_REC % ("Expose HTTP", "accepted", "2026-01-20", "HTTP"))
    d = kv(tool("next", "--root", M)[1])
    check("madr style, five-digit padding kept", d.get("style") == "madr" and d.get("next") == "00013" and d.get("pad") == "5")
    A = os.path.join(T, "adrdir")
    write(os.path.join(A, ".adr-dir"), "notes/records\n")
    write(os.path.join(A, "notes", "records", "0002-x.md"), PLAIN_REC % ("0002", "X", "proposed", "2026-01-01", "x"))
    write(os.path.join(A, "docs", "adr", "0009-decoy.md"), "# decoy\n")
    d = kv(tool("next", "--root", A)[1])
    check(".adr-dir wins over docs/adr", d.get("adr_dir", "").endswith("notes/records") and d.get("next") == "0003")
    L = os.path.join(T, "l4b")
    write(os.path.join(L, ".log4brains.yml"), "project:\n  name: x\n  adrFolder: ./records\n")
    write(os.path.join(L, "records", "0004-y.md"), PLAIN_REC % ("0004", "Y", "accepted", "2026-01-01", "y"))
    d = kv(tool("next", "--root", L)[1])
    check("log4brains adrFolder is read", d.get("adr_dir", "").endswith("records") and d.get("next") == "0005")
    G = os.path.join(T, "green")
    os.makedirs(G)
    d = kv(tool("next", "--root", G)[1])
    check("greenfield: docs/adr not yet, 0001, plain", d.get("exists") == "no" and d.get("next") == "0001" and d.get("style") == "plain" and d.get("count") == "0")

    print("trim")
    entries = "".join("- **[[%04d-x|DEC-%04d]]** · 2026-01-%02d — ruling %d\n" % (n, n, n % 28 + 1, n) for n in range(23, 0, -1))
    write(os.path.join(P, "CLAUDE.md"), "# proj\n\nIntro kept.\n\n## Decisions\n\n> Decision index, newest first.\n\n" + entries + "\n## Other\n\nAfter.\n")
    rc, out, err = tool("trim", "--root", P)
    md = open(os.path.join(P, "CLAUDE.md")).read()
    sec = md.split("## Decisions", 1)[1].split("## Other", 1)[0]
    check("three entries cut", "cut=3" in out)
    check("20 newest kept, oldest gone", sec.count("\n- ") == 20 and "DEC-0023" in sec and "DEC-0003" not in sec)
    check("vault pointer added after the entries", "_Older decisions: see the [[_decisions-moc|decisions MOC]]._" in sec)
    check("the rest of the file is untouched", md.startswith("# proj\n\nIntro kept.\n") and md.endswith("## Other\n\nAfter.\n") and "> Decision index, newest first." in sec)
    rc, out, err = tool("trim", "--root", P)
    md2 = open(os.path.join(P, "CLAUDE.md")).read()
    check("a second run cuts nothing and adds no second pointer", "cut=0" in out and md2.count("_Older decisions") == 1)
    rc, out, err = tool("trim", "--root", Q, "--keep", "1")
    check("no CLAUDE.md: reported, exit 1", rc == 1 and "no CLAUDE.md" in out)
    write(os.path.join(Q, "CLAUDE.md"), "# q\n\n## Decisions\n\n- a\n- b\n- c\n")
    rc, out, err = tool("trim", "--root", Q, "--keep", "1", "--dry-run")
    check("dry run prints, does not write", "cut=2" in out and open(os.path.join(Q, "CLAUDE.md")).read().count("\n- ") == 3)
    rc, out, err = tool("trim", "--root", Q, "--keep", "1")
    q = open(os.path.join(Q, "CLAUDE.md")).read()
    check("plain pointer names the folder", "_Older decisions: see docs/decisions._" in q and q.count("\n- ") == 1)
finally:
    shutil.rmtree(T, ignore_errors=True)

print()
if _fails:
    print("FAIL: %d check(s): %s" % (len(_fails), "; ".join(_fails)))
    sys.exit(1)
print("ALL PASS")
