#!/usr/bin/env bash
# Global git hook dispatcher, installed machine-wide via:
#   git config --global core.hooksPath ~/.claude/git-hooks
#
# Two jobs, in order:
#  1. On `commit-msg`, run a GENTLE (warn-only, never-blocking) ADR-link reminder.
#     It is silent in repos with no ADR dir, and never runs in non-git dirs.
#  2. Chain to the repo's OWN hook of the same name, if it has one, so setting a
#     global hooksPath never silently disables a repo's existing hooks (husky, etc.).
#     The repo's own hooks live in <git-dir>/hooks, which core.hooksPath does NOT
#     redirect, so there is no loop.
#
# Every hook name in this directory is a symlink to this one file; $0's basename
# tells us which hook git invoked.
set -u
hook=$(basename "$0")
[ -n "$hook" ] || exit 0

# 1. Our behaviour: the non-blocking ADR-link reminder, only on commit-msg.
if [ "$hook" = commit-msg ] && [ -x "$HOME/.claude/hooks/adr-link.sh" ]; then
  ADR_LINK_ENFORCE=warn "$HOME/.claude/hooks/adr-link.sh" commit-msg "$1" || true
fi

# 2. Chain to the repo's own hook of this name, if present (its DEFAULT hooks dir,
#    which is unaffected by core.hooksPath). Pass through all original args.
gitdir=$(git rev-parse --absolute-git-dir 2>/dev/null) || exit 0
local_hook="$gitdir/hooks/$hook"
[ -x "$local_hook" ] && exec "$local_hook" "$@"
exit 0
