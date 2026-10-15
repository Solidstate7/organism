#!/bin/sh
# guard-publish.sh, the wrapper settings.json runs for guard-publish.js.
# As a hook (no arguments) it first decides, without node, whether the Confidential
# Terms list has any active term. An empty list protects nothing, so the hook exits 0
# and node is never started: the guard costs nothing until it has terms.
# With at least one term it fails closed:
#   node found      -> run guard-publish.js; its exit 0 (allow) or 2 (block) stands,
#                      any other exit (a crash, a missing script) blocks with exit 2
#   node missing    -> exit 2 with a stderr line saying how to fix it, plus an
#                      alert.py push at most once per day (marker under ~/.claude/state)
# --status and --scan always go to node, and keep failing open when node is missing
# (stderr line + the same alert): they guard nothing by themselves.
# Node is resolved in this order: 1. node on PATH  2. /opt/homebrew/bin/node
# 3. /usr/local/bin/node ($GUARD_PUBLISH_NODE_FALLBACK replaces 2 and 3, for the tests).
# stdin passes through untouched: the JS reads the PreToolUse payload from fd 0.

# Resolve our own directory so the wrapper works both deployed (~/.claude/hooks/,
# symlinked from the repo) and from a repo checkout (tests). POSIX-safe: no BASH_SOURCE.
HOOKS_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" >/dev/null 2>&1 && pwd)
[ -n "$HOOKS_DIR" ] || HOOKS_DIR="$HOME/.claude/hooks"

# has_nul FILE: the file holds a NUL byte (awk would stop reading a line there).
has_nul() { ! LC_ALL=C tr -d '\000' < "$1" 2>/dev/null | cmp -s - "$1"; }

# armed: exit 0 when the denylist has an active term, or when this cannot be told for
# sure; exit 1 only when it certainly has none. Same sources, same order and same line
# rules as loadTerms() in guard-publish.js: $GUARD_PUBLISH_DENYLIST, then
# GUARD_PUBLISH_DENYLIST in paths.env, then the legacy ~/.claude/confidential-terms.txt;
# the first readable one wins; only the lines between the CONFIDENTIAL-TERMS markers
# count when both are there; blank, #, ``` and <!-- lines are not terms. Any line with
# a byte outside plain ASCII counts as a term here (the JS trims more kinds of space
# than awk can), so a doubt always ends in node, never in an unguarded allow.
armed() {
  [ -n "${HOME:-}" ] || return 0
  pe=${CLAUDE_PATHS_ENV:-$HOME/.claude/state/paths.env}
  from_pe=""
  if [ -f "$pe" ] && [ -r "$pe" ]; then
    has_nul "$pe" && return 0
    from_pe=$(LC_ALL=C awk -v home="$HOME" '
      function trim(s) { sub(/^[ \t\r]+/, "", s); sub(/[ \t\r]+$/, "", s); return s }
      {
        t = trim($0)
        if (t == "" || substr(t, 1, 1) == "#" || !index(t, "=")) next
        if (index($0, "GUARD_PUBLISH_DENYLIST") && $0 ~ /[^\t\r -~]/) { print "?"; exit }
        i = index(t, "=")
        if (trim(substr(t, 1, i - 1)) != "GUARD_PUBLISH_DENYLIST") next
        v = trim(substr(t, i + 1)); q = substr(v, 1, 1); j = 0
        if (q == "\"" || q == "'\''") j = index(substr(v, 2), q)
        if (j) v = substr(v, 2, j - 1)
        else { k = index(v, "#"); if (k) v = substr(v, 1, k - 1); v = trim(v) }
        out = ""
        while ((k = index(v, "$HOME")) > 0) { out = out substr(v, 1, k - 1) home; v = substr(v, k + 5) }
        v = out v
        if (substr(v, 1, 2) == "~/") v = home substr(v, 2)
        print v; exit
      }' "$pe" 2>/dev/null) || return 0
    [ "$from_pe" = "?" ] && return 0
  fi
  for src in "${GUARD_PUBLISH_DENYLIST:-}" "$from_pe" "$HOME/.claude/confidential-terms.txt"; do
    [ -n "$src" ] || continue
    if [ ! -e "$src" ] || [ -d "$src" ] || [ ! -r "$src" ]; then continue; fi   # unreadable: the JS tries the next one too
    [ -f "$src" ] || return 0
    has_nul "$src" && return 0
    verdict=$(LC_ALL=C awk '
      { text = text $0 "\n" }
      END {
        b = index(text, "CONFIDENTIAL-TERMS:BEGIN"); e = index(text, "CONFIDENTIAL-TERMS:END"); region = text
        if (b && e && e > b) { nl = index(substr(text, b), "\n"); region = (nl && b + nl < e) ? substr(text, b + nl, e - b - nl) : "" }
        n = split(region, L, "\n")
        for (i = 1; i <= n; i++) {
          l = L[i]; sub(/^[ \t\r]+/, "", l); sub(/[ \t\r]+$/, "", l)
          if (l == "" || substr(l, 1, 3) == "```" || substr(l, 1, 1) == "#" || substr(l, 1, 4) == "<!--") continue
          if (l ~ /[^\t -~]/) { print "armed"; exit }
          sub(/^[-*+][ \t]+/, "", l); gsub(/`/, "", l); sub(/#.*$/, "", l); sub(/^[ \t]+/, "", l); sub(/[ \t]+$/, "", l)
          if (l != "") { print "armed"; exit }
        }
        print "none"
      }' "$src" 2>/dev/null)
    [ "$verdict" = "none" ] && return 1
    return 0
  done
  return 1
}

# A hook call with nothing to protect: allow without starting node.
if [ $# -eq 0 ] && ! armed; then exit 0; fi

NODE=""
if command -v node >/dev/null 2>&1; then
  NODE=$(command -v node)
else
  for c in ${GUARD_PUBLISH_NODE_FALLBACK-/opt/homebrew/bin/node /usr/local/bin/node}; do
    if [ -x "$c" ]; then NODE="$c"; break; fi
  done
fi

if [ -n "$NODE" ]; then
  # --status / --scan: exec, so their output and exit code are node's own.
  [ $# -gt 0 ] && exec "$NODE" "$HOOKS_DIR/guard-publish.js" "$@"
  "$NODE" "$HOOKS_DIR/guard-publish.js"
  rc=$?
  if [ "$rc" -eq 0 ] || [ "$rc" -eq 2 ]; then exit "$rc"; fi
  echo "guard-publish: the guard failed (node exited $rc) while confidential terms are armed, so this call is BLOCKED. Fix guard-publish.js ($HOOKS_DIR), or run the command yourself in a terminal if you are sure." >&2
  exit 2
fi

# ---- node missing ----
if [ $# -eq 0 ]; then
  echo "guard-publish: node not found (PATH, /opt/homebrew/bin, /usr/local/bin) while confidential terms are armed, so this call is BLOCKED: the guard cannot check it. Install node (e.g. brew install node), or empty the Confidential Terms list to turn the guard off." >&2
  ALERT_TEXT="node is not installed or resolvable on this machine while the Confidential Terms list has terms, so the publish guard blocks every call it sees (fail-closed). Install node (e.g. brew install node), or empty the list. This alert repeats at most once/day."
  RC=2
else
  echo "guard-publish: node not found (PATH, /opt/homebrew/bin, /usr/local/bin), so $1 DID NOT RUN (fail-open). Install node to run it." >&2
  ALERT_TEXT="node is not installed or resolvable on this machine, so guard-publish $1 did not run (fail-open). Install node (e.g. brew install node). This alert repeats at most once/day."
  RC=0
fi

# Alert the user at most once/day. Marker is written BEFORE the push: this hook
# fires on EVERY Bash tool call, so gating the marker on delivery would retry a slow/
# failing alert.py on each call and wedge the session, once-a-day breadcrumb beats
# alert-spam (alert.py is itself best-effort with its own banner fallback). The push
# runs in the background: a slow send must not outlast the hook's timeout, because a
# killed hook lets the command run.
STAMP="$HOME/.claude/state/guard-publish-node-missing.alerted"
today=$(date +%F)
last=""
[ -f "$STAMP" ] && last=$(cat "$STAMP" 2>/dev/null)
if [ "$last" != "$today" ] && [ -z "${GUARD_PUBLISH_NO_ALERT:-}" ]; then
  mkdir -p "$(dirname "$STAMP")" 2>/dev/null
  printf '%s' "$today" > "$STAMP" 2>/dev/null
  PY=""
  if command -v python3 >/dev/null 2>&1; then
    PY=$(command -v python3)
  else
    for c in /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
      if [ -x "$c" ]; then PY="$c"; break; fi
    done
  fi
  [ -n "$PY" ] && "$PY" "$HOOKS_DIR/alert.py" \
    --title "⚠️ guard-publish: node missing" \
    --text "$ALERT_TEXT" \
    </dev/null >/dev/null 2>&1 &
fi
exit "$RC"
