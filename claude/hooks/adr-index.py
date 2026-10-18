#!/usr/bin/env python3
"""adr-index: the mechanical parts of /handoff's record keeping, so the skill reads a value
instead of describing a procedure.

  organism adr-index next [--root DIR]
      the records folder (resolved like adr-link), whether it exists, how many records it
      holds, the next number with its padding, the file pattern, the style of the latest
      record (vault, plain, madr or unknown) and whether the root is inside a vault
  organism adr-index moc [--root DIR] [--write] [--project NAME]
      regenerate <adrdir>/_decisions-moc.md from every record: Accepted, Superseded and
      Proposed, newest first. An existing MOC keeps its header (frontmatter, title and the
      callout) and the line it already has for a record; only new records get a generated
      line. Prints the page; --write saves it.
  organism adr-index trim [--root DIR] [--keep N] [--dry-run]
      keep the N newest entries (default 20) under CLAUDE.md's "## Decisions" and add the
      pointer to the rest when something was cut; nothing else in the file is touched

Python 3.9, standard library only. Reads records; writes only with --write (moc) or by
default for trim (--dry-run prints instead).
"""
import os
import re
import sys

ADR_DIRS = ("docs/adr", "docs/decisions", "docs/architecture/decisions", "doc/adr", "adr",
            "architecture/decisions")
RECORD = re.compile(r"^(\d{4,})-(.+)\.md$")
STATUS_LINE = re.compile(r"^[-*]\s*Status:\s*(.+?)\s*$", re.I | re.M)
DATE_LINE = re.compile(r"^[-*]\s*Date:\s*(\d{4}-\d{2}-\d{2})", re.I | re.M)
FM_DATE = re.compile(r"^date:\s*[\"']?(\d{4}-\d{2}-\d{2})", re.M)
FM_TAG = re.compile(r"adr/([a-z-]+)")
TITLE = re.compile(r"^#\s+(.*\S)\s*$", re.M)
NUM_PREFIX = re.compile(r"^\d{4,}\.\s*")
SUPERSEDED_BY = re.compile(r"superseded by\s+(?:\[\[([^\]|]+)(?:\|[^\]]*)?\]\]|(?:ADR-|DEC-)?(\d{4,}))", re.I)
MOC_LINE = re.compile(r"^- \[\[([^\]|]+)\|DEC-(\d{4,})\]\]")
OLDER = re.compile(r"^_Older decisions", re.M)
MAX_LINE = 160


def read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def vault_of(root):
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), "lib"))
        import pathsenv
        v = pathsenv.read_raw().get("CLAUDE_VAULT_ROOT", "")
        v = os.path.realpath(os.path.expandvars(os.path.expanduser(v))) if v else ""
        if v and (root == v or root.startswith(v + os.sep)):
            return v
    except Exception:
        pass
    d = root
    while True:
        if os.path.isdir(os.path.join(d, ".obsidian")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return ""
        d = parent


def resolve_adr_dir(root):
    """The records folder, in /handoff step 1a's order: .adr-dir, a log4brains config, the
    first existing usual folder, else docs/adr (which may not exist yet)."""
    p = read(os.path.join(root, ".adr-dir"))
    if p and p.strip():
        return os.path.normpath(os.path.join(root, p.strip()))
    for name in (".log4brains.yml", "log4brains.yml"):
        y = read(os.path.join(root, name))
        if y:
            m = re.search(r"adrFolder:\s*[\"']?([^\"'\n#]+)", y)
            if m:
                return os.path.normpath(os.path.join(root, m.group(1).strip()))
    for rel in ADR_DIRS:
        if os.path.isdir(os.path.join(root, rel)):
            return os.path.join(root, rel)
    return os.path.join(root, "docs/adr")


def records(adr_dir):
    """[(number, basename without .md, path)] sorted by number, 0000 and the MOC left out."""
    out = []
    try:
        names = os.listdir(adr_dir)
    except OSError:
        return out
    for n in names:
        m = RECORD.match(n)
        if m and int(m.group(1)) > 0:
            out.append((int(m.group(1)), n[:-3], os.path.join(adr_dir, n)))
    return sorted(out)


def style_of(text):
    head = (text or "")[:4000]
    if head.startswith("---") and re.search(r"^tags:", head, re.M):
        return "vault"
    if re.search(r"^\*\s*Status:", head, re.M) or "## Decision Outcome" in head:
        return "madr"
    if re.search(r"^-\s*Status:", head, re.M):
        return "plain"
    return "unknown"


def parse_record(path):
    text = read(path) or ""
    fm, body = "", text
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            fm, body = text[3:end], text[end + 4:]
    t = TITLE.search(body)
    title = NUM_PREFIX.sub("", t.group(1)) if t else os.path.basename(path)[:-3]
    status, by = "accepted", ""
    tag = FM_TAG.search(fm)
    if tag:
        status = tag.group(1)
    else:
        s = STATUS_LINE.search(body)
        if s:
            status = s.group(1).split()[0].lower().strip(",.")
            sb = SUPERSEDED_BY.search(s.group(1))
            if sb:
                by = sb.group(1) or sb.group(2)
    if status == "superseded" and not by:
        sb = SUPERSEDED_BY.search(body)
        if sb:
            by = sb.group(1) or sb.group(2)
    d = FM_DATE.search(fm) or DATE_LINE.search(body)
    date = d.group(1) if d else ""
    line = ""
    m = re.search(r"^##\s+Decision(?: Outcome)?\s*$\n+(.+?)$", body, re.M | re.I)
    if m and not re.match(r"^\s*(?:[-*+]|\d+[.)])\s", m.group(1)):
        # a Decision section that opens with a list has no one-line ruling; the title is it
        line = m.group(1)
        line = re.sub(r"^Chosen option:\s*", "", line, flags=re.I)
        line = re.sub(r"[*_`]", "", line).strip().rstrip(".")
    if not line:
        line = title
    if len(line) > MAX_LINE:
        line = line[:MAX_LINE - 1].rstrip() + "…"
    return {"title": title, "status": status, "by": by, "date": date, "line": line}


def cmd_next(root):
    d = resolve_adr_dir(root)
    recs = records(d)
    vault = vault_of(root)
    out = ["adr_dir=" + d, "exists=" + ("yes" if os.path.isdir(d) else "no"),
           "count=" + str(len(recs)), "vault=" + ("yes" if vault else "no")]
    if recs:
        n, base, path = recs[-1]
        pad = len(RECORD.match(os.path.basename(path)).group(1))
        out += ["next=" + str(n + 1).zfill(pad), "pad=" + str(pad),
                "pattern=" + "N" * pad + "-kebab-title.md",
                "style=" + style_of(read(path)), "latest=" + path]
    else:
        out += ["next=0001", "pad=4", "pattern=NNNN-kebab-title.md",
                "style=" + ("vault" if vault else "plain"), "latest=none"]
    print("\n".join(out))
    return 0


def existing_moc(path):
    """(header text up to the first section, {number: (line, section)}) of the current MOC,
    or ('', {}). A record already listed keeps its line and its section."""
    text = read(path)
    if not text:
        return "", {}
    header_end = re.search(r"^## ", text, re.M)
    header = text[:header_end.start()] if header_end else ""
    kept, section = {}, ""
    for ln in text.splitlines():
        h = re.match(r"^##\s+(.+?)\s*$", ln)
        if h:
            section = h.group(1).strip().lower()
            continue
        m = MOC_LINE.match(ln)
        if m:
            kept[int(m.group(2))] = (ln, section)
    return header, kept


def cmd_moc(root, write, project):
    d = resolve_adr_dir(root)
    recs = records(d)
    moc_path = os.path.join(d, "_decisions-moc.md")
    header, kept = existing_moc(moc_path)
    rel = os.path.relpath(d, root)
    if not header.strip():
        name = project or os.path.basename(root.rstrip(os.sep))
        header = ("---\ntags: [adr, moc]\n---\n# Decisions — %s\n\n> [!info] Map of Content for "
                  "this project's architecture decisions. Auto-maintained by `/handoff`; full "
                  "records are the linked ADR notes in `%s/`.\n\n" % (name, rel))
    by_num = {n: base for n, base, _ in recs}
    sections = {}
    for n, base, path in reversed(recs):
        r = parse_record(path)
        dec = "DEC-" + str(n).zfill(len(str(n)) if len(str(n)) > 4 else 4)
        key = r["status"]
        old_line, old_section = kept.get(n, ("", ""))
        if old_line and (r["status"] != "superseded" or "superseded by" in old_line):
            line = old_line
            key = old_section or key
        else:
            line = "- [[%s|%s]] — %s" % (base, dec, r["line"])
            if r["status"] == "superseded":
                by = r["by"]
                if by and by.isdigit() and int(by) in by_num:
                    line += " · superseded by [[%s|DEC-%s]]" % (by_num[int(by)], str(int(by)).zfill(4))
                elif by:
                    line += " · superseded by [[%s|DEC-%s]]" % (by, re.sub(r"\D.*", "", by).zfill(4)) \
                        if re.match(r"\d{4,}", by) else " · superseded by [[%s]]" % by
                else:
                    line += " · superseded"
            elif r["date"]:
                line += " · " + r["date"]
        sections.setdefault(key, []).append(line)
    order = ["accepted", "superseded", "proposed"] + sorted(k for k in sections if k not in ("accepted", "superseded", "proposed"))
    parts = [header.rstrip("\n") + "\n"]
    for key in order:
        if key not in sections:
            continue
        parts.append("\n## " + key.capitalize() + "\n\n" + "\n".join(sections[key]) + "\n")
    page = "".join(parts)
    if write:
        try:
            os.makedirs(d, exist_ok=True)
            with open(moc_path, "w", encoding="utf-8") as f:
                f.write(page)
            print("wrote %s (%d records)" % (moc_path, len(recs)))
        except OSError as e:
            print("adr-index: cannot write %s: %s" % (moc_path, e), file=sys.stderr)
            return 1
    else:
        sys.stdout.write(page)
    return 0


def cmd_trim(root, keep, dry_run):
    path = os.path.join(root, "CLAUDE.md")
    text = read(path)
    if text is None:
        print("adr-index: no CLAUDE.md at " + root)
        return 1
    lines = text.splitlines(keepends=True)
    start = next((i for i, ln in enumerate(lines) if re.match(r"^##\s+Decisions\s*$", ln)), None)
    if start is None:
        print("cut=0 (no ## Decisions section)")
        return 0
    end = next((i for i in range(start + 1, len(lines)) if re.match(r"^#{1,2}\s", lines[i])), len(lines))
    section = lines[start + 1:end]
    entries = [i for i, ln in enumerate(section) if ln.startswith("- ")]
    cut = entries[keep:]
    if not cut:
        print("cut=0")
        return 0
    drop = set(cut)
    new = [ln for i, ln in enumerate(section) if i not in drop]
    if not any(OLDER.match(ln) for ln in new):
        vault = bool(vault_of(root))
        rel = os.path.relpath(resolve_adr_dir(root), root)
        pointer = ("_Older decisions: see the [[_decisions-moc|decisions MOC]]._\n" if vault
                   else "_Older decisions: see %s._\n" % rel)
        last = max(i for i, ln in enumerate(new) if ln.startswith("- "))
        new.insert(last + 1, pointer)
    out = "".join(lines[:start + 1] + new + lines[end:])
    if dry_run:
        sys.stdout.write("".join(new))
    else:
        with open(path, "w", encoding="utf-8") as f:
            f.write(out)
    print("cut=%d" % len(cut))
    return 0


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__.strip())
        return 0
    cmd, root, write, project, keep, dry = argv[0], os.getcwd(), False, "", 20, False
    i = 1
    while i < len(argv):
        a = argv[i]
        if a == "--root" and i + 1 < len(argv):
            root = argv[i + 1]
            i += 1
        elif a == "--write":
            write = True
        elif a == "--dry-run":
            dry = True
        elif a == "--project" and i + 1 < len(argv):
            project = argv[i + 1]
            i += 1
        elif a == "--keep" and i + 1 < len(argv):
            keep = int(argv[i + 1])
            i += 1
        else:
            print("adr-index: unknown argument " + a, file=sys.stderr)
            return 2
        i += 1
    root = os.path.realpath(root)
    if cmd == "next":
        return cmd_next(root)
    if cmd == "moc":
        return cmd_moc(root, write, project)
    if cmd == "trim":
        return cmd_trim(root, keep, dry)
    print("adr-index: unknown command " + cmd, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
