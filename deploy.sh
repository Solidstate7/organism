#!/bin/bash
# deploy.sh: symlink ~/.claude/{hooks,commands,skills,keybindings.json,git-hooks} -> this repo,
# so a `git pull` updates those trees with no copy step. Idempotent. Any REAL file/dir it
# replaces is moved aside first, to <name>.pre-organism.bak, or a timestamped
# <name>.pre-organism.<ts>.bak when that name is already taken (a real
# target is NEVER deleted without a fresh backup). Before relinking a real hooks dir it
# copies its reconcile-pairings.json into ~/.claude/state, where every mode reads it.
# In plugin mode (lib/where.py) the commands link is left out: the plugin's skills replace it.
#
# settings.json is GENERATED, not symlinked: the committed
# file is the conservative base a public repo can ship; your per-machine prefs
# (bypassPermissions, model, plugins…) live in the gitignored ~/.claude/state/settings.local.json
# overlay, and lib/settingsmerge.py writes ~/.claude/settings.json = base ⊕ overlay. Bonus:
# in-session writes (/model persisting a default) land in the generated real file and are folded
# back into the overlay on the next deploy, they no longer dirty this repo through a symlink.
#
# A `git pull` therefore updates the symlinked trees but NOT settings.json and NOT the launchd
# plists. deploy.sh closes that gap by installing this repo's own git hooks (git-hooks/ ->
# <git-dir>/hooks), so every pull runs converge.sh = deploy.sh + install-agents.sh. Run
# converge.sh, not deploy.sh, if you want the whole thing.
set -u
REPO="$(cd "$(dirname "$0")" && pwd)"
SRC="$REPO/claude"
DST="$HOME/.claude"
mkdir -p "$DST"

# load machine-local paths.env (for the optional vault decision-log symlink below)
[ -f "$SRC/hooks/lib/env.sh" ] && . "$SRC/hooks/lib/env.sh"

# install mode (lib/where.py): symlink (hooks wired in settings.json) or plugin-inplace
# (this clone is also loaded as a plugin, which supplies the hooks and skills)
. "$SRC/hooks/lib/where.sh"
MODE="$(organism_where mode)"; STATE="$(organism_where state)"
if [ "$MODE" = "plugin-copied" ]; then
  echo "  ✗ $REPO is a copy inside Claude Code's plugin cache; it is replaced on every update," >&2
  echo "    so nothing may link to it. Clone the repo and run bootstrap.sh there instead." >&2
  exit 1
fi

# carry over the pairings file from a real hooks dir before it is moved aside. The copy
# goes to the state folder, which every mode reads; the original stays in the backup.
if [ -d "$DST/hooks" ] && [ ! -L "$DST/hooks" ]; then
  rf=reconcile-pairings.json
  if [ -f "$DST/hooks/$rf" ] && [ ! -e "$STATE/$rf" ]; then
    mkdir -p "$STATE" && cp -p "$DST/hooks/$rf" "$STATE/$rf" && echo "  carried over $rf into $STATE"
  fi
fi

# plugin mode: the plugin's skills replace the commands link (both would list /handoff)
if [ "$MODE" = "plugin-inplace" ] && [ -L "$DST/commands" ] && [ "$(readlink "$DST/commands")" = "$SRC/commands" ]; then
  rm -f "$DST/commands" && echo "  removed the commands link (the plugin's skills replace it)"
fi

for item in hooks commands skills keybindings.json git-hooks; do
  [ "$MODE" = "plugin-inplace" ] && [ "$item" = commands ] && continue
  s="$SRC/$item"; d="$DST/$item"
  [ -e "$s" ] || { echo "  (skip $item: not in repo)"; continue; }
  cur=""; [ -L "$d" ] && cur="$(readlink "$d")"
  if [ -L "$d" ] && [ "$cur" = "$s" ]; then
    echo "  ✓ $item already linked"; continue
  elif [ -L "$d" ] && case "$cur" in */claude/"$item") true ;; *) false ;; esac; then
    rm -f "$d"   # a link into another (moved or older) Organism clone
  elif [ -L "$d" ] || [ -e "$d" ]; then
    # never delete a real target or the user's own link (a dotfiles repo, say): each one
    # gets a fresh backup, timestamped when the plain backup name is taken; if the move
    # fails, abort rather than clobber
    bak="$d.pre-organism.bak"
    [ -e "$bak" ] && bak="$d.pre-organism.$(date +%Y%m%d-%H%M%S).bak"
    mv "$d" "$bak" || { echo "  ✗ could not back up real $item: leaving it in place, aborting" >&2; exit 1; }
    echo "  backed up your $item -> $(basename "$bak")"
  fi
  ln -s "$s" "$d" || { echo "  ✗ failed to link $item -> $s" >&2; exit 1; }
  echo "  linked $item -> $s"
done

# settings.json: generated, not symlinked (see header). settingsmerge migrates the old
# symlink deploy automatically (it unlinks a symlinked target before writing the real file).
# Organism's hooks come from hooks/hooks.json: rendered into settings.json in symlink
# mode, left to the plugin in plugin mode.
SM_MODE=legacy; [ "$MODE" = "plugin-inplace" ] && SM_MODE=plugin
python3 "$SRC/hooks/lib/settingsmerge.py" "$SRC/settings.json" --mode "$SM_MODE" \
  --hooks "$REPO/hooks/hooks.json" --root "$REPO" \
  || { echo "  ✗ settings merge failed: ~/.claude/settings.json left as-is" >&2; exit 1; }

# Optional: co-locate the vault decision log into the repo as a gitignored, never-published view.
# The REAL ADRs live in the Obsidian vault (CLAUDE_VAULT_ADR_DIR, from paths.env); this is only a
# browse-from-the-repo convenience. Skipped when the var is unset or the target is absent (e.g. a
# host with no vault), so the repo stays usable everywhere. docs/vault-adr is gitignored, and git
# never follows the symlink into the vault, the canonical files are not in this repo.
adr_target="${CLAUDE_VAULT_ADR_DIR:-}"; link="$REPO/docs/vault-adr"
if [ -n "$adr_target" ] && [ -d "$adr_target" ]; then
  mkdir -p "$REPO/docs"
  if [ -L "$link" ]; then
    [ "$(readlink "$link")" = "$adr_target" ] && echo "  ✓ docs/vault-adr already linked" \
      || { rm -f "$link"; ln -s "$adr_target" "$link"; echo "  relinked docs/vault-adr -> $adr_target"; }
  elif [ -e "$link" ]; then
    echo "  ⚠️ $link exists and is not a symlink: leaving it"
  else
    ln -s "$adr_target" "$link"; echo "  linked docs/vault-adr -> $adr_target (gitignored decision-log view)"
  fi
elif [ -n "$adr_target" ]; then
  echo "  (skip docs/vault-adr: CLAUDE_VAULT_ADR_DIR='$adr_target' not found)"
fi
# --- this repo's OWN git hooks: make `git pull` run converge.sh --------------------------
# <git-dir>/hooks is NOT tracked and NOT redirected by core.hooksPath, so it is both the only
# place these can live and the place the global dispatcher (claude/git-hooks/_dispatch.sh:28)
# chains to. Installing them from deploy.sh means bootstrap.sh gets them free on a new clone
# and any later deploy re-asserts them, the wiring self-heals rather than being a setup step
# someone has to remember. Skipped on a gitless export (tarball, CI checkout of a subtree).
gitdir="$(git -C "$REPO" rev-parse --absolute-git-dir 2>/dev/null || true)"
if [ -n "$gitdir" ] && [ -d "$REPO/git-hooks" ]; then
  mkdir -p "$gitdir/hooks"
  for h in post-merge post-rewrite post-checkout; do
    s="$REPO/git-hooks/$h"; d="$gitdir/hooks/$h"
    [ -e "$s" ] || continue
    if [ -L "$d" ]; then
      [ "$(readlink "$d")" = "$s" ] && { echo "  ✓ git hook $h already linked"; continue; }
      rm -f "$d"
    elif [ -e "$d" ]; then
      # Same never-delete-a-real-target rule as above: someone's hand-written hook
      # is theirs, not ours. Back it up, tell them, then take over the name.
      bak="$d.pre-organism.bak"
      [ -e "$bak" ] && bak="$d.pre-organism.$(date +%Y%m%d-%H%M%S).bak"
      mv "$d" "$bak" || { echo "  ✗ could not back up real git hook $h: aborting" >&2; exit 1; }
      echo "  backed up real git hook $h -> $(basename "$bak")"
    fi
    ln -s "$s" "$d" || { echo "  ✗ failed to link git hook $h" >&2; exit 1; }
    echo "  linked git hook $h -> $s"
  done
elif [ -z "$gitdir" ]; then
  echo "  (skip self-hooks: $REPO is not a git work tree)"
fi

echo "deploy done: symlinked trees now follow \`git pull\` in $REPO."
echo "             settings.json regenerated; launchd NOT touched (that is install-agents.sh)."
echo "             \`git pull\` now runs converge.sh via the post-merge hook: both, every time."
