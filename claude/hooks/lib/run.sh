#!/bin/sh
# hooks/lib/run.sh: launchd exec-wrapper. Sets HOME, sources env.sh (PATH + paths.env +
# PYTHON), then execs the given command, so plists stop hardcoding the interpreter + PATH.
#
#   plist ProgramArguments:  <repo>/claude/hooks/lib/run.sh  python3  <repo>/claude/hooks/X.py
#
# (launchd doesn't expand $HOME or run a login shell, so this wrapper sets the environment up.)
export HOME="${HOME:-$(echo ~)}"
_dir=$(cd "$(dirname "$0")" 2>/dev/null && pwd)
[ -f "$_dir/env.sh" ] && . "$_dir/env.sh"
exec "$@"
