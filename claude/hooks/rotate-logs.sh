#!/bin/bash
# ~/.claude/hooks/rotate-logs.sh
# Weekly log hygiene: launchd itself never rotates StandardOut/ErrorPath, so they
# grow forever (a service log once hit 198 MB). Three duties:
#   1. Any *.log/*.out/*.err over the threshold (this glob includes the launchd
#      <job>.launchd.log streams) keeps its tail and is truncated IN PLACE
#      (cat tmp > file) so live services holding the fd in append mode keep writing
#      with no gap / sparse file.
#   2. alerts.jsonl, the alerts ledger: NEVER discarded. Over 1 MB it is
#      archived whole to alerts-<ts>.jsonl.gz (archives are kept), then the live
#      file keeps its last rows so /alerts stays continuous.
#   3. reconcile-backups/: per-change page copies (reconcile.py) that no rotation
#      glob could see (subdirectory): pruned after 30 days; the vault page and its
#      git history remain the durable record.
# On a CLEAN pass it writes ~/.claude/state/logrotate-last-success (bare YYYY-MM-DD,
# byte-identical to lib/jobstatus.mark_success) so freshness is checkable; a partial
# run leaves the marker stale rather than lying healthy. Run by the logrotate agent.
set -u

THRESHOLD_MB=${ROTATE_THRESHOLD_MB:-20}
KEEP_LINES=${ROTATE_KEEP_LINES:-5000}
ALERTS_MAX_MB=${ROTATE_ALERTS_MAX_MB:-1}
ALERTS_KEEP_LINES=${ROTATE_ALERTS_KEEP_LINES:-400}   # /alerts tails <=40 rows; 10x margin
BACKUPS_KEEP_DAYS=${ROTATE_BACKUPS_KEEP_DAYS:-30}

# Extra log dirs come from the gitignored paths.env (CLAUDE_ROTATE_EXTRA_DIRS).
_ENVSH="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd)/lib/env.sh"
[ -f "$_ENVSH" ] && . "$_ENVSH"
# The value is (a) a single dir, spaces fine, else (b) a
# colon-separated list, else (c) the legacy space-separated list, which cannot express
# a dir containing spaces. `read -a` word-splits WITHOUT pathname expansion, so a glob
# char in the value no longer enumerates the CWD the way the old unquoted
# `for d in $VAR` did.
DIRS=("$HOME/.claude/logs")
_extra=()
if [ -n "${CLAUDE_ROTATE_EXTRA_DIRS:-}" ]; then
  if [ -d "$CLAUDE_ROTATE_EXTRA_DIRS" ]; then
    _extra=("$CLAUDE_ROTATE_EXTRA_DIRS")
  elif [[ "$CLAUDE_ROTATE_EXTRA_DIRS" == *:* ]]; then
    IFS=':' read -r -a _extra <<< "$CLAUDE_ROTATE_EXTRA_DIRS"
  else
    read -r -a _extra <<< "$CLAUDE_ROTATE_EXTRA_DIRS"
  fi
fi
for d in ${_extra[@]+"${_extra[@]}"}; do   # ${…+…}: empty array is set -u-safe on bash 3.2
  [ -n "$d" ] && [ -d "$d" ] && DIRS+=("$d")
done

ts() { date '+%F %T'; }
size() { stat -f %z "$1" 2>/dev/null || stat -c %s "$1" 2>/dev/null || echo 0; }

ok=1   # cleared by any skipped/failed action; gates the freshness marker below

echo "[$(ts)] rotate-logs start (threshold=${THRESHOLD_MB}MB keep=${KEEP_LINES})"

# 1) oversized plain logs -> keep tail, truncate in place
for d in "${DIRS[@]}"; do
  [ -d "$d" ] || continue
  for f in "$d"/*.log "$d"/*.out "$d"/*.err; do
    [ -f "$f" ] || continue
    sz=$(size "$f")
    if [ "$sz" -gt $((THRESHOLD_MB * 1024 * 1024)) ]; then
      tmp="$f.rot.$$"
      # The write-back is checked too: a root-owned log (e.g. a
      # stray install log) would otherwise fail the bare `cat` redirect noisily yet count
      # as success.
      if tail -n "$KEEP_LINES" "$f" > "$tmp" 2>/dev/null \
         && { cat "$tmp" > "$f"; } 2>/dev/null; then   # in-place: preserves inode/fd for live writers
        rm -f "$tmp"
        echo "[$(ts)] rotated $f ($((sz / 1024 / 1024))MB -> last ${KEEP_LINES} lines)"
      else
        rm -f "$tmp"
        ok=0
        echo "[$(ts)] SKIP $f (tail/write-back failed)"
      fi
    fi
  done
done

# 2) alerts.jsonl, the alerts ledger: archive whole (never discard), then shrink
#    the live file in place. Archives (alerts-<ts>.jsonl.gz) are deliberately NOT
#    pruned, the ledger is the evidence /alerts claims rest on.
LEDGER="$HOME/.claude/logs/alerts.jsonl"
if [ -f "$LEDGER" ] && [ "$(size "$LEDGER")" -gt $((ALERTS_MAX_MB * 1024 * 1024)) ]; then
  arch="$HOME/.claude/logs/alerts-$(date '+%Y%m%d-%H%M%S').jsonl.gz"
  if gzip -c "$LEDGER" > "$arch" 2>/dev/null; then
    tmp="$LEDGER.rot.$$"
    if tail -n "$ALERTS_KEEP_LINES" "$LEDGER" > "$tmp" 2>/dev/null \
       && { cat "$tmp" > "$LEDGER"; } 2>/dev/null; then
      echo "[$(ts)] archived alerts.jsonl -> $(basename "$arch"); live tail ${ALERTS_KEEP_LINES} rows kept"
    else
      ok=0
      echo "[$(ts)] alerts.jsonl archived to $(basename "$arch") but in-place shrink FAILED (ledger intact)"
    fi
    rm -f "$tmp"
  else
    rm -f "$arch"   # a failed gzip may leave a partial (corrupt) archive; drop it
    ok=0
    echo "[$(ts)] SKIP alerts.jsonl (gzip failed: ledger left untouched)"
  fi
fi

# 3) reconcile-backups/, prune page copies older than BACKUPS_KEEP_DAYS
BK="$HOME/.claude/logs/reconcile-backups"
if [ -d "$BK" ]; then
  n=$(find "$BK" -type f -mtime +"$BACKUPS_KEEP_DAYS" 2>/dev/null | wc -l | tr -d ' ')
  if [ "${n:-0}" -gt 0 ]; then
    if find "$BK" -type f -mtime +"$BACKUPS_KEEP_DAYS" -delete 2>/dev/null; then
      echo "[$(ts)] pruned $n reconcile-backup file(s) older than ${BACKUPS_KEEP_DAYS}d"
    else
      ok=0
      echo "[$(ts)] SKIP reconcile-backups prune (find -delete failed)"
    fi
  fi
fi

# Freshness marker, only a clean pass arms it (same doctrine as the review markers:
# a degraded run must read as stale, not healthy).
if [ "$ok" = 1 ]; then
  mkdir -p "$HOME/.claude/state" 2>/dev/null
  if printf '%s' "$(date +%F)" > "$HOME/.claude/state/logrotate-last-success" 2>/dev/null; then
    echo "[$(ts)] rotate-logs done (clean; marker updated)"
  else
    echo "[$(ts)] rotate-logs done (clean, but marker write failed)"
  fi
else
  echo "[$(ts)] rotate-logs done (with skips: freshness marker NOT updated)"
fi
