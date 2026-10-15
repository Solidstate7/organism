#!/usr/bin/env python3
"""hooks/lib/paths.py: load the machine-local, gitignored paths.env.

Vault/project paths and codenames live in ~/.claude/state/paths.env (or the
path in $CLAUDE_PATHS_ENV), never in committed code. Shell scripts source
env.sh; Python scripts use this:

    import paths
    repos = paths.get("CLAUDE_HANDOFF_REPOS", "").split()

An explicit environment variable always wins over the file, so launchd
EnvironmentVariables or a test harness can override any key.
"""
import os


def _env_path():
    return os.environ.get("CLAUDE_PATHS_ENV") or os.path.join(
        os.environ.get("HOME") or os.path.expanduser("~"),
        ".claude", "state", "paths.env",
    )


def load():
    vals = {}
    try:
        # encoding pinned: paths.env holds vault/project paths (the config file most
        # likely to carry non-ASCII, such as an accented or non-Latin folder name), and the
        # ambient locale on a non-UTF-8 host would round-trip it corrupted.
        with open(_env_path(), encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                val = val.strip()
                if val[:1] in ('"', "'"):       # quoted: take inside the quotes, ignore a trailing # comment
                    end = val.find(val[0], 1)
                    val = val[1:end] if end != -1 else val[1:]
                else:                            # unquoted: drop a trailing # comment (as the shell would)
                    val = val.split("#", 1)[0].strip()
                vals[key.strip()] = os.path.expandvars(os.path.expanduser(val))
    except FileNotFoundError:
        pass
    return vals


def get(key, default=None):
    if key in os.environ:
        return os.environ[key]
    return load().get(key, default)


def mirror_dir():
    """Root of the session notes (`_claude-live`), or "" when they are off.

    An explicit $CLAUDE_OBSIDIAN_MIRROR_DIR wins (env var, then paths.env); a key that
    is present but empty turns the notes off. Without the key the notes live in the
    vault, at $CLAUDE_VAULT_ROOT/_claude-live. With neither key set there is no vault,
    so nothing is written ("", every caller treats it as off): there is no fallback
    folder outside the vault.
    Single source: consumers once read os.environ only, so a relocated
    vault silently broke the mirror."""
    d = get("CLAUDE_OBSIDIAN_MIRROR_DIR")
    if d is not None:
        return d
    vault = get("CLAUDE_VAULT_ROOT")
    return os.path.join(vault, "_claude-live") if vault else ""


def bin_path():
    """A PATH string augmented with the usual `claude`/brew locations, for a subprocess
    env (so a spawned `claude -p` is found regardless of how this process was launched).

    THE one precedence order (five diverged copies once let different
    jobs resolve different `claude` binaries for the same job class):
    $CLAUDE_BIN_DIR (env var wins, then paths.env; colon/whitespace-separated dirs)
    first, then brew/system dirs, then the caller's own PATH. No app-bundle dir is
    hardcoded here: a machine-specific bundle (e.g. cmux) belongs
    in the gitignored paths.env as CLAUDE_BIN_DIR."""
    home = os.environ.get("HOME") or os.path.expanduser("~")
    extra = [os.path.expanduser(d)
             for d in (get("CLAUDE_BIN_DIR") or "").replace(os.pathsep, " ").split()]
    extra += ["/opt/homebrew/bin", "/usr/local/bin", os.path.join(home, ".local", "bin")]
    seen, out = set(), []
    for d in extra + (os.environ.get("PATH", "").split(os.pathsep)):
        if d and d not in seen:
            seen.add(d)
            out.append(d)
    return os.pathsep.join(out)
