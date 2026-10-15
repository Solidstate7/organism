#!/bin/sh
# lib/color.sh: the one CLI palette. Source it; use $CB bold, $CC cyan (names/paths),
# $CG green (ok/running), $CY yellow (attention/stale), $CR red (failed), $CD dim
# (secondary), $CX reset. Gate is the convention everywhere: color only on a real
# terminal (or CLICOLOR_FORCE=1), never under NO_COLOR, piped output is byte-identical.
if { [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; } || [ "${CLICOLOR_FORCE:-}" = "1" ]; then
  CB="$(printf '\033[1m')";  CC="$(printf '\033[36m')"; CG="$(printf '\033[32m')"
  CY="$(printf '\033[33m')"; CR="$(printf '\033[31m')"; CD="$(printf '\033[2m')"
  CX="$(printf '\033[0m')"
else
  CB=""; CC=""; CG=""; CY=""; CR=""; CD=""; CX=""
fi
# h1 "title", a bold cyan section header
h1() { echo "${CB}${CC}== $* ==${CX}"; }
