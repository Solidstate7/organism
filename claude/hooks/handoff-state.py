#!/usr/bin/env python3
"""handoff-state: the facts /handoff may state about a project, printed by a script so the
brief never carries a hash, a branch or a pushed-or-not claim written from memory.

  organism handoff-state            print the state; save a copy of the current brief
  organism handoff-state --check    compare the rewritten brief with the saved copy
  organism handoff-state --root DIR

The root is the git top level, else the vault root (the configured CLAUDE_VAULT_ROOT, or the
nearest folder above holding .obsidian or docs/adr), else none: one brief per repo or vault.
The state: root, vault, branch, HEAD, upstream, ahead and behind, dirty files, the brief and
when it was written, every hash the brief names (exists? pushed?), the sessions active since
the brief was written, the remotes (credentials stripped), and `adr-link state`.
The check: hashes the new brief names that don't exist or that it calls pushed while the
upstream lacks them, and open threads the old brief had that the new one dropped.
Exit 0; --check exits 1 when a hash claim doesn't hold (dropped threads are listed, not
failed: finishing an item drops it too). Read-only apart from the saved copy under
<state dir>/handoff-prev/. Python 3.9, standard library only.
"""
import datetime
import hashlib
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(HERE, "lib"))
try:
    import where
except Exception:  # the lib is beside this file; a copy without it still prints git facts
    where = None

HASH_RE = re.compile(r"(?<![0-9A-Za-z/_.-])(?=[0-9a-f]*[a-f])([0-9a-f]{7,40})(?![0-9A-Za-z])")
PUSHED_WORDS = re.compile(r"\b(pushed|in sync|on origin|upstream|published)\b", re.I)
NOT_PUSHED = re.compile(r"\b(not pushed|unpushed|no push|never pushed|local only|not yet pushed"
                        r"|ahead of|pushed nothing|without pushing)\b", re.I)
THREAD_HEAD = re.compile(r"^(#{1,6})\s*(.*\b(next|open|backlog|todo|threads)\b.*)$", re.I)
BULLET = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(.*\S)\s*$")
MAX_HASHES = 40
MAX_SESSIONS = 10
MAX_DIRTY = 30


def run(args, cwd, timeout=10):
    """stdout of a command, '' on any failure (a git error never stops the state)."""
    try:
        p = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return ""
    return p.stdout.rstrip("\n") if p.returncode == 0 else ""


def git(root, *args):
    return run(["git", "-c", "core.hooksPath=" + os.devnull, "-C", root] + list(args), cwd=root)


def find_root(start):
    """(root, kind): the git top level ('git'); else the vault root ('vault'); else the
    start folder itself ('none'), where /handoff writes no brief. One brief per repo or
    vault, so a run from a vault subfolder never leaves a brief that hides the vault's."""
    start = os.path.realpath(start)
    top = git(start, "rev-parse", "--show-toplevel")
    if top:
        return os.path.realpath(top), "git"
    vault = vault_of(start)
    if vault:
        return vault, "vault"
    return start, "none"


def vault_of(root):
    """The vault holding root: CLAUDE_VAULT_ROOT when root is under it, else the nearest
    ancestor (root included) holding an .obsidian folder or a docs/adr folder."""
    try:
        import pathsenv
        v = pathsenv.read_raw().get("CLAUDE_VAULT_ROOT", "")
        v = os.path.realpath(os.path.expandvars(os.path.expanduser(v))) if v else ""
        if v and (root == v or root.startswith(v + os.sep)):
            return v
    except Exception:
        pass
    d = root
    while True:
        if os.path.isdir(os.path.join(d, ".obsidian")) or os.path.isdir(os.path.join(d, "docs", "adr")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return ""
        d = parent


def strip_credentials(url):
    return re.sub(r"://[^/@\s]+@", "://", url)


def redact(text):
    try:
        import mirror
        return mirror.redact(text)[0]
    except Exception:
        return text


def state_dir():
    if where is not None:
        try:
            return where.state_dir()
        except Exception:
            pass
    return os.environ.get("ORGANISM_STATE_DIR") or os.path.join(
        os.environ.get("HOME") or os.path.expanduser("~"), ".claude", "state")


def snapshot_path(root):
    slug = hashlib.sha1(root.encode("utf-8")).hexdigest()[:12]
    return os.path.join(state_dir(), "handoff-prev", slug + ".md")


def read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def hashes_in(text):
    seen, out = set(), []
    for m in HASH_RE.finditer(text or ""):
        h = m.group(1)
        if h not in seen:
            seen.add(h)
            out.append(h)
        if len(out) >= MAX_HASHES:
            break
    return out


def commit_facts(root, h, upstream, has_remote):
    """(exists, pushed, subject) for one hash. pushed: yes|no|no-remote."""
    exists = subprocess.run(["git", "-C", root, "cat-file", "-e", h + "^{commit}"],
                            capture_output=True).returncode == 0
    if not exists:
        return False, "", ""
    subject = git(root, "show", "-s", "--format=%s", h)
    if not has_remote:
        return True, "no-remote", subject
    if upstream:
        ok = subprocess.run(["git", "-C", root, "merge-base", "--is-ancestor", h, "@{u}"],
                            capture_output=True).returncode == 0
    else:
        ok = bool(git(root, "branch", "-r", "--contains", h))
    return True, ("yes" if ok else "no"), subject


def git_facts(root):
    f = {}
    f["branch"] = git(root, "branch", "--show-current") or "(detached)"
    f["head"] = git(root, "rev-parse", "--short", "HEAD") or "none"
    f["head_subject"] = git(root, "show", "-s", "--format=%s", "HEAD")
    up = git(root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    f["upstream"] = up or "none"
    f["ahead"] = f["behind"] = "0"
    if up:
        lr = git(root, "rev-list", "--left-right", "--count", "@{u}...HEAD")
        parts = lr.split()
        if len(parts) == 2:
            f["behind"], f["ahead"] = parts
        f["pushed"] = "yes" if f["ahead"] == "0" else "no"
    else:
        f["pushed"] = "no-upstream"
    status = git(root, "status", "--porcelain")
    f["dirty_lines"] = [ln for ln in status.splitlines() if ln.strip()]
    f["dirty"] = str(len(f["dirty_lines"]))
    f["log"] = git(root, "log", "-8", "--oneline")
    remotes = []
    for ln in git(root, "remote", "-v").splitlines():
        parts = ln.split()
        if len(parts) >= 2:
            remotes.append(parts[0] + " " + redact(strip_credentials(parts[1]))
                           + (" " + parts[2] if len(parts) > 2 else ""))
    f["remotes"] = remotes
    f["has_remote"] = bool(remotes)
    return f


def sessions_since(root, since):
    try:
        import mirror
        home = os.environ.get("HOME") or os.path.expanduser("~")
        items = mirror.sessions_since(root, since, exclude=None, home=home) or []
    except Exception:
        return None
    out = []
    for it in items:
        host = re.sub(r"\s*\([A-Za-z]+\d+,\d+\)$", "", it.get("machine") or "") or "unknown host"
        out.append("%s  %s  %s  %s" % ((it.get("ts") or "")[:16], (it.get("id") or "")[:8],
                                       host, it.get("path") or ""))
    return out


def adr_state(root):
    tool = os.path.join(HERE, "adr-link.sh")
    if not os.path.exists(tool):
        return []
    return run(["bash", tool, "state"], cwd=root, timeout=30).splitlines()


def open_threads(text):
    """The bullets of the brief's open-threads section (the first heading that names
    next, open, backlog, todo or threads), up to the next heading of its level or higher."""
    lines = (text or "").splitlines()
    level, out, inside = 0, [], False
    for ln in lines:
        m = THREAD_HEAD.match(ln)
        if m and not inside:
            level, inside = len(m.group(1)), True
            continue
        if inside:
            h = re.match(r"^(#{1,6})\s", ln)
            if h and len(h.group(1)) <= level:
                break
            b = BULLET.match(ln)
            if b:
                out.append(b.group(1))
    return out


def words(s):
    s = re.sub(r"[`*_\[\]()|]", " ", s.lower())
    return {w for w in re.findall(r"[0-9a-zÀ-￿]+", s) if len(w) > 2}


def dropped(old_bullets, new_text):
    """Old bullets no line of the new brief carries (60% of the bullet's words)."""
    new_lines = [words(ln) for ln in (new_text or "").splitlines() if ln.strip()]
    out = []
    for b in old_bullets:
        w = words(b)
        if not w:
            continue
        kept = any(len(w & nl) / len(w) >= 0.6 for nl in new_lines)
        if not kept:
            out.append(b)
    return out


def fmt_time(epoch):
    return datetime.datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M")


def print_state(root, kind, save=True):
    vault = vault_of(root)
    brief = os.path.join(root, "HANDOFF.md")
    brief_text = read(brief)
    out = ["root=" + root, "root_kind=" + kind, "vault=" + ("yes" if vault else "no")]
    if vault:
        out.append("vault_root=" + vault)
    if kind == "none":
        out.append("no repository and no vault above this folder: /handoff writes no brief here")
    if brief_text is not None:
        out.append("brief=" + brief)
        out.append("brief_written=" + fmt_time(os.path.getmtime(brief)))
        if save:
            sp = snapshot_path(root)
            try:
                os.makedirs(os.path.dirname(sp), exist_ok=True)
                with open(sp, "w", encoding="utf-8") as f:
                    f.write(brief_text)
                out.append("brief_saved=" + sp)
            except OSError:
                out.append("brief_saved=none")
    else:
        out.append("brief=none")
    g = git_facts(root) if kind == "git" else None
    if g:
        for k in ("branch", "head", "head_subject", "upstream", "ahead", "behind", "pushed", "dirty"):
            out.append("%s=%s" % (k, g[k]))
    sessions = sessions_since(root, os.path.getmtime(brief)) if brief_text is not None else None
    out.append("sessions_since=" + (str(len(sessions)) if sessions is not None else "unknown"))
    out.append("")
    if g:
        out.append("## dirty")
        out.extend(g["dirty_lines"][:MAX_DIRTY] or ["(clean)"])
        if len(g["dirty_lines"]) > MAX_DIRTY:
            out.append("... and %d more" % (len(g["dirty_lines"]) - MAX_DIRTY))
        out.append("")
        out.append("## log")
        out.append(g["log"] or "(no commits)")
        out.append("")
        out.append("## remotes")
        out.extend(g["remotes"] or ["(none)"])
        out.append("")
        out.append("## hashes named in the brief")
        hs = hashes_in(brief_text)
        for h in hs:
            exists, pushed, subject = commit_facts(root, h, g["upstream"] != "none" and g["upstream"], g["has_remote"])
            out.append("%s exists=%s%s" % (h, "yes" if exists else "no",
                                           ("  pushed=%s  %s" % (pushed, subject)) if exists else ""))
        if not hs:
            out.append("(none)")
        out.append("")
    else:
        out.append("not a git repository: no branch, commit or push facts to state")
        out.append("")
    out.append("## sessions since the brief")
    if sessions:
        out.extend(sessions[:MAX_SESSIONS])
        if len(sessions) > MAX_SESSIONS:
            out.append("... and %d more" % (len(sessions) - MAX_SESSIONS))
    else:
        out.append("(none)" if sessions is not None else "(unknown: no mirror)")
    out.append("")
    out.append("## decision links")
    out.extend(adr_state(root) or ["(adr-link state unavailable)"])
    print(redact("\n".join(out)))
    return 0


def check(root, kind):
    brief = os.path.join(root, "HANDOFF.md")
    new = read(brief)
    if new is None:
        print("check: no HANDOFF.md at " + root)
        return 1
    sp = snapshot_path(root)
    old = read(sp)
    problems = 0
    if old is None:
        print("check: no saved copy of the old brief (run handoff-state before rewriting); "
              "only the hash check ran")
    elif old == new:
        print("check: the saved copy equals the brief, so nothing was compared; run "
              "handoff-state before rewriting, then --check after")
    if kind == "git":
        g = git_facts(root)
        up = g["upstream"] != "none" and g["upstream"]
        print("## hashes")
        hs = hashes_in(new)
        for h in hs:
            exists, pushed, subject = commit_facts(root, h, up, g["has_remote"])
            if not exists:
                print("%s missing: no such commit in this repository" % h)
                problems += 1
                continue
            for ln in new.splitlines():
                if h in ln and pushed == "no" and PUSHED_WORDS.search(ln) and not NOT_PUSHED.search(ln):
                    print("%s unpushed, but the brief calls it pushed: %s" % (h, ln.strip()[:120]))
                    problems += 1
                    break
        if not hs:
            print("(the brief names no commit)")
        print("")
    else:
        print("not a git repository: no hash check")
        print("")
    print("## dropped from the open threads")
    if old is not None and old != new:
        d = dropped(open_threads(old), new)
        for b in d:
            print("- " + b[:160])
        if not d:
            print("(none)")
    else:
        print("(not compared)")
    print("")
    print("ok" if problems == 0 else "%d hash claim(s) don't hold" % problems)
    return 1 if problems else 0


def main(argv):
    root_arg, mode = None, "state"
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--check":
            mode = "check"
        elif a == "--root" and i + 1 < len(argv):
            root_arg = argv[i + 1]
            i += 1
        elif a in ("-h", "--help"):
            print(__doc__.strip())
            return 0
        else:
            print("handoff-state: unknown argument " + a, file=sys.stderr)
            return 2
        i += 1
    root, kind = find_root(root_arg or os.getcwd())
    return check(root, kind) if mode == "check" else print_state(root, kind)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
