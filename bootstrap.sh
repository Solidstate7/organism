#!/bin/bash
# bootstrap.sh: link THIS device to the organism. Idempotent; safe to re-run.
#
#   0. preflight  (required tools actually RUN, abort before touching anything)
#   1. seed per-machine local config from examples if absent (paths.env); carry over an
#      existing live reconcile-pairings.json (never the placeholder example, the setup
#      wizard GENERATES real pairings from your repos)
#   2. host-role init (per_host default) if no host.json, a new device runs NO singletons
#   3. deploy.sh  (symlink ~/.claude -> repo)
#   4. install-agents.sh  (role-gated)
#   5. verify  (host-role status)
#
# --plugin: also load this clone as a Claude Code plugin, in place (marketplace add <clone>,
# install organism@organism), before step 3. The plugin then supplies the hooks and skills,
# and deploy.sh keeps them out of settings.json. An existing symlink install moves over
# with `organism migrate --to-plugin` instead, which snapshots and checks every step.
#
# Secrets (state/telegram-alerts.json, .credentials.json) are NOT seeded or touched, they stay
# as real local files under ~/.claude. A bookkeeping/sideeffect owner must fill paths.env +
# reconcile-pairings.json with real values; a per_host device needs neither.
#
# set -euo pipefail + per-step gates. Without them a failed step still
# printed its success line (a CLT-less Mac's python3 shim fails, yet 'host.json: created'
# printed), and a failed deploy still loaded launchd agents against a half-deployed ~/.claude
# (crash-loop every spawn). Now every step either succeeds or aborts with a clear message.
set -euo pipefail
REPO="$(cd "$(dirname "$0")" && pwd)"
STATE="$HOME/.claude/state"
HOOKS_LIVE="$HOME/.claude/hooks"
. "$REPO/claude/hooks/lib/color.sh"
die() { echo "${CR}✗ bootstrap aborted:${CX} $*" >&2; exit 1; }

# 0. preflight, RUN each required tool: `command -v` alone passes the CLT-less xcrun shims
git --version >/dev/null 2>&1   || die "git not working; install Xcode Command Line Tools: xcode-select --install"
python3 -c '' >/dev/null 2>&1   || die "python3 not working; install Xcode Command Line Tools: xcode-select --install"
command -v node >/dev/null 2>&1 || echo "  ${CY}⚠️ node not found: the publish guard (guard-publish.js, PreToolUse hook in settings.json) blocks every Bash call while your Confidential Terms list has terms, until node is installed${CX}"

CONFIGURE=""; PLUGIN=""
for a in "$@"; do
  case "$a" in
    --configure) CONFIGURE=1 ;;   # opt-in: run the setup wizard after linking
    --plugin)    PLUGIN=1 ;;
    *) die "unknown option $a (use --configure and/or --plugin)" ;;
  esac
done
if [ -n "$PLUGIN" ]; then
  command -v claude >/dev/null 2>&1 || die "--plugin needs the claude CLI on PATH"
  [ "$(python3 "$REPO/claude/hooks/lib/where.py" in-cache)" = no ] \
    || die "$REPO is a copy inside Claude Code's plugin cache; clone the repo and run bootstrap.sh there"
  if [ -L "$HOOKS_LIVE" ] && [ "$(python3 "$REPO/claude/hooks/lib/where.py" mode)" = symlink ]; then
    die "this machine already has a symlink install; move it over with: organism migrate --to-plugin"
  fi
fi

mkdir -p "$STATE" "$HOME/.claude/logs"

echo "== organism bootstrap on $("$REPO/claude/hooks/lib/hostid.sh" label) =="

# 1. seed local config (never overwrite)
if [ ! -f "$STATE/paths.env" ]; then
  cp "$REPO/config/paths.env.example" "$STATE/paths.env" || die "could not seed state/paths.env"
  echo "  seeded state/paths.env; configure it with the wizard: organism setup"
else echo "  ✓ state/paths.env exists"; fi

# pairings: lib/where.py reads $RECONCILE_CONFIG, then ~/.claude/state, then this clone's
# claude/hooks, then ~/.claude/hooks. A live file in a real ~/.claude/hooks is copied into
# state before deploy.sh moves that folder aside; an existing file is never moved.
RP_FOUND="$(python3 "$REPO/claude/hooks/lib/where.py" pairings)"
if [ -n "$RP_FOUND" ] && [ "$RP_FOUND" != "$HOOKS_LIVE/reconcile-pairings.json" ]; then
  echo "  ✓ reconcile-pairings.json present ($RP_FOUND)"
elif [ -f "$HOOKS_LIVE/reconcile-pairings.json" ] && [ ! -L "$HOOKS_LIVE" ]; then
  cp -p "$HOOKS_LIVE/reconcile-pairings.json" "$STATE/reconcile-pairings.json" \
    || die "could not carry over reconcile-pairings.json into $STATE"
  echo "  carried over live reconcile-pairings.json into $STATE"
else
  echo "  reconcile-pairings.json: none yet; 'organism setup' generates it from your repos"
fi

# 2. role: per_host default if no host.json
if [ ! -f "$STATE/host.json" ]; then
  python3 "$REPO/claude/hooks/host-role.py" init >/dev/null || die "host-role.py init failed: host.json NOT created"
  echo "  host.json: created (per_host only, runs no singletons)"
else echo "  ✓ host.json exists"; fi

# plugin first, then the install record, then deploy: the plugin is on before deploy.sh
# keeps Organism's hooks out of settings.json, so no session ever starts without the guard
if [ -n "$PLUGIN" ]; then
  python3 "$REPO/claude/cli/migrate.py" --install-plugin || die "installing the plugin failed"
  python3 - "$STATE/organism-install.json" "$REPO" <<'PY' || die "could not write the install record"
import json, os, sys
path, root = sys.argv[1], sys.argv[2]
tmp = path + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump({"mode": "plugin-inplace", "root": root}, f, indent=2)
    f.write("\n")
os.replace(tmp, path)
PY
  echo "  plugin organism@organism loaded in place from $REPO"
fi

# 3. deploy + 4. install, each gated: never load agents against a half-deployed ~/.claude
bash "$REPO/deploy.sh" || die "deploy.sh failed: NOT installing launchd agents"
bash "$REPO/launchd/install-agents.sh" || die "install-agents.sh failed"

# 5. verify
echo "${CB}${CC}== verify ==${CX}"
python3 "$REPO/claude/hooks/host-role.py" status || die "verify failed: host-role.py status errored"
# ── the `organism` CLI ──────────────────────────────────────────────────────
mkdir -p "$HOME/.local/bin"
ln -sf "$REPO/bin/organism" "$HOME/.local/bin/organism"
case ":$PATH:" in
  *":$HOME/.local/bin:"*) echo "  organism CLI installed (~/.local/bin/organism)";;
  *) echo "  organism CLI installed at ~/.local/bin/organism; add ~/.local/bin to your PATH:"
     echo '     echo '"'"'export PATH="$HOME/.local/bin:$PATH"'"'"' >> ~/.zshrc';;
esac


cat <<EOF

Linked. A per_host machine runs only its own jobs and can never double-run a shared one.
Configure THIS machine (repo paths · vault + Obsidian · role · schedules) with the guided wizard:

  organism setup                                     # or re-run: bootstrap.sh --configure

To make THIS device the owner of the singletons (release them on the OTHER device first):
  python3 ~/.claude/hooks/host-role.py release       # on the OTHER device
  python3 ~/.claude/hooks/host-role.py claim owner   # here, then re-run install-agents.sh
EOF

if [ -n "$CONFIGURE" ]; then
  echo; echo "${CB}${CC}== configure with the organism setup wizard ==${CX}"
  python3 "$REPO/claude/hooks/organism-setup.py" || die "setup wizard exited with an error"
fi
