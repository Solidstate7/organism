#!/bin/sh
# hooks/lib/env.sh: machine-local config + binary discovery for harness scripts.
# Source it:  . "$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)/lib/env.sh"
#
#   1) loads the gitignored paths.env (vault/project paths kept out of code)
#   2) augments PATH with the usual tool dirs (brew prefix varies by arch; a
#      machine-specific dir (e.g. an app bundle shipping the `claude` CLI) comes
#      from CLAUDE_BIN_DIR in paths.env) so claude/node/python3 resolve
#   3) exports PYTHON = a discovered python3
#
# Final precedence mirrors lib/paths.bin_path() (one order everywhere):
# CLAUDE_BIN_DIR entries first, then brew/local/user dirs, then the inherited PATH.

# 1) machine-local paths.env
: "${CLAUDE_PATHS_ENV:=${HOME:-$(echo ~)}/.claude/state/paths.env}"
if [ -f "$CLAUDE_PATHS_ENV" ]; then
  # shellcheck disable=SC1090
  . "$CLAUDE_PATHS_ENV"
fi

# 2) PATH augmentation (idempotent; only prepends dirs that exist and aren't already
#    on PATH). Iterated in REVERSE precedence, each prepend lands in front, so the
#    last candidate handled ends up first.
for _d in "${HOME:-$(echo ~)}/.local/bin" /usr/local/bin /opt/homebrew/bin; do
  [ -n "$_d" ] && [ -d "$_d" ] || continue
  case ":$PATH:" in *":$_d:"*) ;; *) PATH="$_d:$PATH" ;; esac
done

# CLAUDE_BIN_DIR (paths.env): colon-separated dirs; a dir containing spaces survives.
# The old unquoted ${CLAUDE_BIN_DIR:-} in the for-list
# word-split a space-containing dir into fragments (each failing -d, silently never
# added) and glob-expanded any wildcard against the CWD. The hardcoded cmux bundle
# fallback is gone from committed code, a machine that needs it sets
# CLAUDE_BIN_DIR in its gitignored paths.env instead. A colon-free segment that is
# not itself a dir is word-split as the legacy space-separated form (parity with
# lib/paths.bin_path()'s colon/whitespace tolerance).
_env_add_dir() {  # queue $1 (leading ~ expanded) into _pre if it's a real, new dir
  case "$1" in "~") set -- "$HOME" ;; "~/"*) set -- "$HOME/${1#"~/"}" ;; esac
  [ -d "$1" ] || return 0
  case ":$PATH:$_pre" in *":$1:"*) ;; *) _pre="$_pre$1:" ;; esac
}
if [ -n "${CLAUDE_BIN_DIR:-}" ]; then
  case $- in *f*) _globoff=1 ;; *) _globoff= ;; esac
  set -f                                  # never glob-expand the split words
  _pre=; _oifs=$IFS; IFS=':'
  for _seg in $CLAUDE_BIN_DIR; do
    IFS=$_oifs
    if [ -d "$_seg" ] || [ -z "${_seg%%\~*}" ]; then
      _env_add_dir "$_seg"                # canonical: one dir per colon segment
    else
      for _d in $_seg; do _env_add_dir "$_d"; done   # legacy whitespace list
    fi
    IFS=':'
  done
  IFS=$_oifs
  [ -z "$_globoff" ] && set +f
  # _pre was built in listed order (first entry first), one prepend keeps it so.
  [ -n "$_pre" ] && PATH="$_pre$PATH"
  unset _pre _oifs _globoff _seg
fi
unset -f _env_add_dir
export PATH

# 3) discovered python3 for scripts that need to invoke one
: "${PYTHON:=$(command -v python3 2>/dev/null || echo python3)}"
export PYTHON
