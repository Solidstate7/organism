#!/usr/bin/env python3
"""hooks/lib/pathsenv.py: the WRITER counterpart to the read-only paths.py.

In-place `KEY="val"` updates to ~/.claude/state/paths.env (or $CLAUDE_PATHS_ENV),
preserving comments, section headers, blank lines, and unknown keys. Values are
double-quoted so `env.sh`'s `. paths.env` still parses.

The cross-key caveat is enforced here: paths.py (Python) can't expand a `$CLAUDE_*`
reference inside another value, only keys that env.sh SHELL-sources may use them.
`set_keys` rejects a `$CLAUDE_*` cross-ref in any other (Python-read) key.
"""
import difflib
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths as _paths

# Keys that env.sh shell-sources (so a $CLAUDE_* cross-ref expands at source time).
# Everything else is read by paths.py and MUST be literal or $HOME-based.
SHELL_ONLY_KEYS = {"CLAUDE_ROTATE_EXTRA_DIRS"}


def path():
    return _paths._env_path()


def _read_lines():
    try:
        # encoding pinned: paths.env carries vault paths (possibly non-Latin);
        # a non-UTF-8 default locale would corrupt them on read/rewrite.
        with open(path(), encoding="utf-8") as f:
            return f.read().splitlines(keepends=True)
    except FileNotFoundError:
        return []


def read_raw():
    """KEY -> raw stored value (quotes + trailing comment stripped, NOT expanded), for
    showing idempotent, portable defaults ($HOME/dev, not /Users/you/dev)."""
    out = {}
    for line in _read_lines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        v = v.strip()
        if v[:1] in ('"', "'"):
            end = v.find(v[0], 1)
            v = v[1:end] if end != -1 else v[1:]
        else:
            v = v.split("#", 1)[0].strip()
        out[k.strip()] = v
    return out


def _validate(key, value):
    if key not in SHELL_ONLY_KEYS and re.search(r"\$\{?CLAUDE_[A-Z_]", value):
        raise ValueError(
            f"{key} is Python-read (paths.py); it can't use a $CLAUDE_* cross-reference "
            f"(paths.py won't expand it). Use a literal or $HOME path instead.")


def _value_of(rest):
    """The stored value of a value-part (quotes + trailing comment stripped)."""
    s = rest.rstrip("\n").strip()
    if s[:1] in ('"', "'"):
        end = s.find(s[0], 1)
        return s[1:end] if end != -1 else s[1:]
    return s.split("#", 1)[0].strip()


def _inline_comment(rest):
    """Return the trailing ` # ...` comment of a value-part (after the closing quote /
    the unquoted token), or '', so we preserve the user's annotations on rewrite."""
    s = rest.rstrip("\n").strip()
    if not s:
        return ""
    if s[0] in ('"', "'"):
        end = s.find(s[0], 1)
        after = s[end + 1:] if end != -1 else ""
    else:
        after = s
    h = after.find("#")
    return after[h:].rstrip() if h != -1 else ""


def set_keys(updates, write=True):
    """updates: {KEY: value}. Replace each key's value in place (preserving its inline
    comment + placement) or append not-found keys under a trailing section. Returns a
    unified diff string ('' if nothing changed). Writes atomically when write=True."""
    for k, v in updates.items():
        _validate(k, v)
    orig = _read_lines()
    lines = list(orig)
    pending = dict(updates)
    pats = {k: re.compile(r"^(\s*)" + re.escape(k) + r"(\s*=\s*)(.*)$") for k in updates}
    for i, line in enumerate(lines):
        for k in list(pending):
            m = pats[k].match(line.rstrip("\n"))
            if m:
                indent, _eq, rest = m.groups()
                if _value_of(rest) == pending[k]:      # unchanged → leave the line exactly as-is (idempotent)
                    del pending[k]
                    break
                comment = _inline_comment(rest)
                nl = "\n" if line.endswith("\n") else ""
                lines[i] = f'{indent}{k}="{pending[k]}"' + (f"  {comment}" if comment else "") + nl
                del pending[k]
                break
    if pending:  # keys not present in the file, append under a header (rare: example seeds most)
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        lines.append("\n# --- set by organism setup ---\n")
        for k, v in pending.items():
            lines.append(f'{k}="{v}"\n')
    diff = "".join(difflib.unified_diff(orig, lines, fromfile="paths.env", tofile="paths.env"))
    if write and diff:
        p = path()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:   # utf-8 pinned
            f.writelines(lines)
        os.replace(tmp, p)
    return diff
