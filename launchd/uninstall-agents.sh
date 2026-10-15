#!/bin/bash
# launchd/uninstall-agents.sh: unload + remove EVERY organism launchd agent on this device.
# Covers everything install-agents.sh can create, manifest agents, agents.local instance agents,
# and orphans from renamed/deleted jobs, since all of them live in the $NS.* namespace this
# sweeps. Plists outside $NS are never touched.
set -u
. "$(dirname "$0")/../claude/hooks/lib/color.sh" 2>/dev/null || true
LA="${ORGANISM_LA:-$HOME/Library/LaunchAgents}"   # override kept in lockstep with install-agents.sh (test renders)
NS="${ORGANISM_LAUNCHD_NS:-com.organism}"
# Test-seam safety (same guard as install-agents.sh):
# `launchctl unload <path>` acts on the LABEL inside the file, in the REAL launchd.
# A sandbox that overrides LA but forgets DRY would therefore unload the machine's
# LIVE agents while "cleaning" scratch plists. An overridden LA can only ever mean
# "operate on rendered files elsewhere", never "touch the live daemon", force dry.
if [ -n "${ORGANISM_LA:-}" ] && [ -z "${ORGANISM_DRY:-}" ]; then
  echo "${CY}⚠️ ORGANISM_LA is overridden but ORGANISM_DRY is not set: forcing dry (refusing to touch the real launchd)${CX}" >&2
  ORGANISM_DRY=1
fi
# Launch safety: launchd is per account, not per HOME. With HOME pointed at a sandbox,
# `launchctl load` would still replace this account's live jobs of the same name, so a
# HOME that is not the account's own home only ever renders (dry run).
ACCOUNT_HOME="$(python3 -c 'import os, pwd; print(pwd.getpwuid(os.getuid()).pw_dir)' 2>/dev/null || true)"
if [ -z "${ORGANISM_DRY:-}" ] && { [ -z "$ACCOUNT_HOME" ] || \
     [ "$(cd -P "$HOME" 2>/dev/null && pwd)" != "$(cd -P "$ACCOUNT_HOME" 2>/dev/null && pwd)" ]; }; then
  echo "${CY}⚠️ HOME ($HOME) is not this account's home (${ACCOUNT_HOME:-unknown}): forcing dry (refusing to touch the real launchd)${CX}" >&2
  ORGANISM_DRY=1
fi
n=0
for plist in "$LA/$NS."*.plist; do
  [ -f "$plist" ] || continue
  if [ -n "${ORGANISM_DRY:-}" ]; then
    echo "  ${CD}would remove $(basename "$plist" .plist) [dry]${CX}"
  else
    launchctl unload "$plist" 2>/dev/null
    rm -f "$plist"
    echo "  ${CY}removed${CX} $(basename "$plist" .plist)"
  fi
  n=$((n + 1))
done
echo "${ORGANISM_DRY:+[dry] }uninstalled $n agent(s)."
