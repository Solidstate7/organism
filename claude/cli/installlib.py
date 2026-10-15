"""Shared helpers for `organism uninstall`, `organism migrate` and `organism doctor`.

Everything here reads HOME at call time, so tests can point it at a sandbox. Nothing
in this module changes anything by itself; the callers decide what to apply.
"""
import json
import os
import re
import shutil
import subprocess
import sys

CLI = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(os.path.dirname(CLI))
LIB = os.path.join(ROOT, "claude", "hooks", "lib")
sys.path.insert(0, LIB)
import where  # noqa: E402
import settingsmerge  # noqa: E402

PLUGIN_ID = "organism@organism"
MARKETPLACE = "organism"
LINKS = ("hooks", "commands", "skills", "keybindings.json", "git-hooks")
SELF_HOOKS = ("post-merge", "post-rewrite", "post-checkout")


def home():
    return where.home()


def claude_dir():
    return os.path.join(home(), ".claude")


def settings_paths():
    """(settings.json, overlay, snapshot) for the current HOME."""
    c = claude_dir()
    return (os.path.join(c, "settings.json"),
            os.path.join(c, "state", "settings.local.json"),
            os.path.join(c, "state", "settings.generated.json"))


def load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    settingsmerge.atomic_write(path, obj)


def run(cmd, timeout=120, **kw):
    """(returncode, combined output). A missing program is returncode 127."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **kw)
        return r.returncode, (r.stdout + r.stderr).strip()
    except FileNotFoundError:
        return 127, f"{cmd[0]}: not found"
    except subprocess.TimeoutExpired:
        return 124, f"{cmd[0]}: timed out after {timeout} s"


def have(prog):
    return shutil.which(prog) is not None


def same_path(a, b):
    try:
        return os.path.realpath(a) == os.path.realpath(b)
    except Exception:
        return False


def under(path, parent):
    path, parent = os.path.realpath(path), os.path.realpath(parent)
    return path == parent or path.startswith(parent.rstrip(os.sep) + os.sep)


def link_into(path, root=ROOT):
    """True when path is a symlink whose target lies inside root."""
    if not os.path.islink(path):
        return False
    t = os.readlink(path)
    if not os.path.isabs(t):
        t = os.path.join(os.path.dirname(path), t)
    return under(t, root)


def newest_backup(path):
    """The newest `<path>.pre-organism*.bak` that deploy.sh left, or None. deploy.sh takes
    the plain name first and adds a timestamp when that name is taken, so a timestamped
    backup is always newer than the plain one; among timestamps the largest is newest."""
    d, name = os.path.split(path)
    try:
        entries = os.listdir(d)
    except OSError:
        return None
    stamped = sorted(e for e in entries
                     if re.fullmatch(re.escape(name) + r"\.pre-organism\.\d{8}-\d{6}\.bak", e))
    if stamped:
        return os.path.join(d, stamped[-1])
    plain = name + ".pre-organism.bak"
    return os.path.join(d, plain) if plain in entries else None


def all_backups():
    """Every `*.pre-organism*.bak` directly under ~/.claude, for the manual-restore list."""
    try:
        return sorted(os.path.join(claude_dir(), e) for e in os.listdir(claude_dir())
                      if ".pre-organism" in e and e.endswith(".bak"))
    except OSError:
        return []


def deployed_root():
    """The clone this machine runs: the install record's root, else the clone the
    ~/.claude/hooks link points into. None when neither exists."""
    m = where.read_marker()
    if m.get("root"):
        return os.path.realpath(m["root"])
    hooks = os.path.join(claude_dir(), "hooks")
    if os.path.islink(hooks):
        return os.path.realpath(os.path.join(hooks, "..", ".."))
    return None


def other_deployed(root=ROOT):
    """The deployed clone when it is not root and still exists, else None (a clone
    that was moved or deleted cannot run anything, so it never blocks this one)."""
    d = deployed_root()
    return d if d and os.path.isdir(d) and not same_path(d, root) else None


def git_dir(root=ROOT):
    rc, out = run(["git", "-C", root, "rev-parse", "--absolute-git-dir"], timeout=20)
    return out.splitlines()[-1] if rc == 0 and out else None


def global_hooks_path():
    rc, out = run(["git", "config", "--global", "--get", "core.hooksPath"], timeout=20)
    return out if rc == 0 and out else None


def ours_hooks_path(value):
    if not value:
        return False
    v = os.path.expanduser(value.replace("$HOME", home()))
    return os.path.normpath(v) == os.path.normpath(os.path.join(claude_dir(), "git-hooks"))


def plugin_list():
    """`claude plugin list --json` as a list, or None when the CLI is missing or fails."""
    if not have("claude"):
        return None
    rc, out = run(["claude", "plugin", "list", "--json"], timeout=60)
    if rc != 0:
        return None
    try:
        data = json.loads(out[out.index("["):]) if "[" in out else []
        return data if isinstance(data, list) else None
    except Exception:
        return None


def plugin_entry(plugins=None):
    plugins = plugin_list() if plugins is None else plugins
    for p in plugins or []:
        if isinstance(p, dict) and p.get("id") == PLUGIN_ID:
            return p
    return None


def marketplace_list():
    if not have("claude"):
        return None
    rc, out = run(["claude", "plugin", "marketplace", "list", "--json"], timeout=60)
    if rc != 0:
        return None
    try:
        data = json.loads(out[out.index("["):]) if "[" in out else []
        return data if isinstance(data, list) else None
    except Exception:
        return None


def organism_hook_commands(settings):
    """Organism's own hook commands in a settings dict (any wiring)."""
    out = []
    hooks = settings.get("hooks") if isinstance(settings, dict) else None
    for groups in (hooks or {}).values() if isinstance(hooks, dict) else []:
        for g in groups if isinstance(groups, list) else []:
            for h in (g.get("hooks") or []) if isinstance(g, dict) else []:
                if settingsmerge.is_ours(h):
                    out.append(" ".join([h.get("command", "")] + list(h.get("args") or [])))
    return out


def plugin_enabled_in(settings):
    ep = settings.get("enabledPlugins") if isinstance(settings, dict) else None
    return isinstance(ep, dict) and ep.get(PLUGIN_ID) is True


def launchd_dir():
    return os.environ.get("ORGANISM_LA") or os.path.join(home(), "Library", "LaunchAgents")


def launchd_ns():
    return os.environ.get("ORGANISM_LAUNCHD_NS") or "com.organism"


def plists():
    d, ns = launchd_dir(), launchd_ns()
    try:
        return sorted(os.path.join(d, e) for e in os.listdir(d)
                      if e.startswith(ns + ".") and e.endswith(".plist"))
    except OSError:
        return []


def launchctl_jobs():
    """Loaded job labels in the namespace, or None when launchctl is unavailable."""
    if not have("launchctl"):
        return None
    rc, out = run(["launchctl", "list"], timeout=30)
    if rc != 0:
        return None
    ns = launchd_ns() + "."
    return sorted({w for ln in out.splitlines() for w in ln.split() if w.startswith(ns)})


def say(msg=""):
    print(msg, flush=True)
