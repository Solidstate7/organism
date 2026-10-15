#!/usr/bin/env python3
"""CLAUDE.md ↔ disk self-check.

A repo's always-loaded doc (CLAUDE.md, else README.md) names files and dirs. When
the code moves and the doc doesn't, those references rot (the "spec lies to me"
debt). This is the mechanical half: extract backtick-quoted PATH references from
the doc and report any that no longer exist on disk. (Prose-claim drift ("tests
green", "5 sensors") is left to the reconciler's probes.)

Conservative by design: only tokens that clearly denote a path are checked, so a
clean repo stays clean (no false alarms). Prints ONE line for a reconcile probe:
  ✅ all <doc> path references exist
  ⚠️ <doc> references N missing path(s): a, b, c

Usage: claude-md-check.py <repo_dir> [doc_file]
"""
import sys, os, re

BACKTICK = re.compile(r"`([^`\n]+)`")


# /handoff, /incognito, and plugin-namespaced /organism:handoff: commands, not paths
SLASH_CMD = re.compile(r"^/[A-Za-z][\w-]*(?::[A-Za-z][\w-]*)?$")


def looks_like_path(tok):
    """A backtick token is a candidate repo path iff it contains a '/' separator
    and isn't one of the known not-a-path classes. Precision over recall: bare
    filenames (often markdown-link text), npm scoped packages (`@scope/name`),
    global `.claude/` refs, and slash commands are skipped. The MAIN check then
    further anchors relative paths to a real top-level repo entry (so API routes
    and context-relative refs like `config/test` or `_archive/` never alarm)."""
    t = tok.strip()
    if "/" not in t:                                  # require a path separator
        return False
    if t.startswith(("@", ".claude/", "-", "#", "~/.claude/")):  # npm scope / global config / flag
        return False
    if SLASH_CMD.match(t):                             # slash command, not a path
        return False
    if any(c in t for c in " \t*?<>$|()[]{}!") or "://" in t:     # globs / placeholders / URLs
        return False
    return True


def main():
    if len(sys.argv) < 2:
        print("usage: claude-md-check.py <repo_dir> [doc_file]"); return
    repo = os.path.abspath(os.path.expanduser(sys.argv[1]))
    doc = sys.argv[2] if len(sys.argv) > 2 else None
    if not doc:
        for cand in ("CLAUDE.md", "README.md"):
            if os.path.isfile(os.path.join(repo, cand)):
                doc = cand
                break
    if not doc or not os.path.isfile(os.path.join(repo, doc)):
        print("✅ no CLAUDE.md/README.md to check")
        return

    try:
        toplevel = set(os.listdir(repo))  # real top-level entries, to anchor relative paths
    except Exception:
        toplevel = set()
    text = open(os.path.join(repo, doc), encoding="utf-8", errors="replace").read()
    seen, missing = set(), []
    for m in BACKTICK.finditer(text):
        tok = m.group(1).strip().rstrip(".,;:")
        if tok in seen or not looks_like_path(tok):
            continue
        seen.add(tok)
        if tok.startswith(("/", "~")):                # absolute / home, check directly
            p = os.path.expanduser(tok)
        else:
            # Relative: only check it if its FIRST segment is a real top-level
            # repo entry. Otherwise it's a route / context-relative ref / non-repo
            # path, not doc-rot, skip rather than false-alarm.
            first = tok.split("/", 1)[0]
            if first not in toplevel:
                continue
            p = os.path.join(repo, tok)
        if not os.path.exists(p.rstrip("/")):
            missing.append(tok)

    if missing:
        shown = ", ".join(sorted(missing)[:8]) + (" …" if len(missing) > 8 else "")
        print(f"⚠️ {doc} references {len(missing)} missing path(s): {shown}")
    else:
        print(f"✅ all {doc} path references exist")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"⚠️ check error: {e!r}")
