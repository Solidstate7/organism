#!/usr/bin/env bash
# git-hooks/self-converge.sh: THIS repo's own git hooks. Makes `git pull` the whole deploy.
#
# deploy.sh symlinks <git-dir>/hooks/{post-merge,post-rewrite,post-checkout} to the
# same-named symlinks beside this file, all of which point here; $0's basename tells us which
# hook git invoked (the pattern claude/git-hooks/_dispatch.sh already uses machine-wide).
#
# This works whether or not the machine has a global core.hooksPath:
#   - hooksPath unset  → git runs <git-dir>/hooks/<name> directly.
#   - hooksPath set to ~/.claude/git-hooks → _dispatch.sh execs <git-dir>/hooks/<name> (its
#     line 28 chain), which core.hooksPath does NOT redirect. Either way we run.
#
# Exit status is deliberately 0 in every path: git ignores post-* hook exit codes, so a
# nonzero here would only look like a failure it silently swallowed. A failed converge
# SHOUTS on stderr instead, and the `converge` launchd agent retries it on a timer.
set -u

hook=$(basename "$0")
case "$hook" in
  post-merge)
    ;;                                              # `git pull` (merge): always converge
  post-rewrite)
    cat >/dev/null                                  # git feeds the rewritten-commit list on stdin
    [ "${1:-}" = rebase ] || exit 0                 # `pull --rebase` yes; `commit --amend` no
    ;;
  post-checkout)
    [ "${3:-0}" = 1 ] || exit 0                     # branch checkout ($3=1); never a file checkout
    ;;
  *)
    exit 0 ;;
esac

REPO=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0
[ -x "$REPO/converge.sh" ] || exit 0                # not the organism repo (or a partial tree)

# Only the deployed clone converges: the one ~/.claude/hooks links into, or the root an
# install marker names. A worktree or a second clone firing this hook (a checkout there)
# must never repoint ~/.claude at itself.
here=$(cd -P "$REPO" 2>/dev/null && pwd)
deployed=$(cd -P "$HOME/.claude/hooks/../.." 2>/dev/null && pwd)
if [ "$here" != "$deployed" ] && \
   [ "$(python3 "$REPO/claude/hooks/lib/where.py" mode 2>/dev/null)" != plugin-inplace ]; then
  echo "→ ${hook}: $REPO is not the deployed clone (${deployed:-none}); not converging"
  exit 0
fi

echo "→ ${hook}: converging ~/.claude with $REPO (settings.json + launchd are NOT updated by the pull itself)"
if bash "$REPO/converge.sh"; then
  exit 0
fi
echo "✗ converge failed after ${hook}. ~/.claude may be half-deployed: the symlinked trees" >&2
echo "  followed the pull, but settings.json and/or the launchd agents did NOT." >&2
echo "  Fix: bash $REPO/converge.sh   (the converge agent will also retry on its next run)" >&2
exit 0
