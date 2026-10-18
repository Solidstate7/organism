"""rootof: the one answer to "which folder does /handoff work from", shared by
handoff-state.py, adr-index.py and adr-link.sh.

find_root(start) -> (root, kind)
    the git top level ('git'); else the vault root ('vault'): CLAUDE_VAULT_ROOT when start
    is under it, else the nearest folder at or above start holding .obsidian or docs/adr;
    else (start, 'none'). One brief per repo or vault. A vault that is $HOME itself counts
    as none: a brief there would be ~/HANDOFF.md, which the SessionStart hook never loads,
    and the skill says never $HOME.

obsidian_vault_of(start) -> path or ''
    the Obsidian vault holding start (CLAUDE_VAULT_ROOT or an .obsidian ancestor), which is
    what decides the record style; a bare docs/adr folder does not make a vault here.

As a script: python3 rootof.py [DIR]  prints "<root>\\t<kind>\\t<obsidian vault or ->".
Python 3.9, standard library only.
"""
import os
import subprocess
import sys


def home():
    return os.path.realpath(os.environ.get("HOME") or os.path.expanduser("~"))


def git_top(start):
    try:
        p = subprocess.run(["git", "-c", "core.hooksPath=" + os.devnull, "-C", start,
                            "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return ""
    return os.path.realpath(p.stdout.strip()) if p.returncode == 0 and p.stdout.strip() else ""


def configured_vault():
    try:
        sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
        import pathsenv
        v = pathsenv.read_raw().get("CLAUDE_VAULT_ROOT", "")
        return os.path.realpath(os.path.expandvars(os.path.expanduser(v))) if v else ""
    except Exception:
        return ""


def _under(path, parent):
    return bool(parent) and (path == parent or path.startswith(parent.rstrip(os.sep) + os.sep))


def _ancestor_with(start, markers):
    d = start
    while True:
        if any(os.path.isdir(os.path.join(d, *m.split("/"))) for m in markers):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return ""
        d = parent


def _not_home(path):
    return path if path and path != home() else ""


def obsidian_vault_of(start):
    """The vault that decides the record style. A home vault counts here (a repo inside
    it writes vault-style records); only the brief's root (find_root) treats $HOME as none."""
    start = os.path.realpath(start)
    v = configured_vault()
    if _under(start, v):
        return v
    return _ancestor_with(start, (".obsidian",))


def vault_root_of(start):
    """The vault root for the brief: the configured vault, else .obsidian or docs/adr above.
    Never $HOME itself."""
    start = os.path.realpath(start)
    v = configured_vault()
    if _under(start, v):
        return _not_home(v)
    return _not_home(_ancestor_with(start, (".obsidian", "docs/adr")))


def find_root(start):
    start = os.path.realpath(start)
    top = git_top(start)
    if top:
        return top, "git"
    v = vault_root_of(start)
    if v:
        return v, "vault"
    return start, "none"


if __name__ == "__main__":
    s = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()
    root, kind = find_root(s)
    print("%s\t%s\t%s" % (root, kind, obsidian_vault_of(s) or "-"))   # a home vault prints as the vault
