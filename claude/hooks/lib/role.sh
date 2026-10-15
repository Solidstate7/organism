#!/bin/sh
# hooks/lib/role.sh: `should_run <role>` returns 0 (run) / 1 (skip), delegating to
# role.py so there is one source of truth. Source it, then guard a job's entry:
#   . "$(cd "$(dirname "$0")/lib" && pwd)/role.sh"
#   should_run singleton_bookkeeping || { echo "role not assigned; exiting"; exit 0; }
#
# The lib dir is resolved ONCE at source time. The old
# ${BASH_SOURCE[0]:-$0} inside the function was a bash-only array expansion, a hard
# "Bad substitution" when sourced into dash/POSIX sh, and its $0 fallback resolved
# role.py against the CALLER's directory. ${BASH_SOURCE:-} (no subscript) is plain
# POSIX parameter syntax that bash expands to element 0; non-bash shells probe the
# documented caller-relative layouts, then the deployed location.
if [ -n "${BASH_SOURCE:-}" ]; then
  _ROLE_LIB_DIR=$(cd "$(dirname "${BASH_SOURCE}")" 2>/dev/null && pwd)
else
  _ROLE_LIB_DIR=""
  for _role_d in "$(dirname "$0")/lib" "$(dirname "$0")" "${HOME:-$(echo ~)}/.claude/hooks/lib"; do
    if [ -f "$_role_d/role.py" ]; then
      _ROLE_LIB_DIR=$(cd "$_role_d" 2>/dev/null && pwd)
      break
    fi
  done
  unset _role_d
fi

should_run() {
  "${PYTHON:-python3}" "${_ROLE_LIB_DIR:-${HOME}/.claude/hooks/lib}/role.py" "$1"
}
