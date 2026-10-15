#!/bin/bash
# launchd/install-agents.sh: render + (re)load the launchd agents THIS device's role enables.
#
# Role-gated via hooks/lib/role.py: per_host agents always install; singletons only when this
# device's host.json grants them. Idempotent (unload→reload); removes any agent whose role is
# no longer assigned, and sweeps any orphaned $NS plist whose job (one
# scheduled agent in this manifest) is in NEITHER manifest
# (renamed/deleted jobs).
# Plists route through hooks/lib/run.sh (sets HOME, sources env.sh, execs), so no plist hardcodes
# an interpreter or PATH, only $HOME is rendered in.
set -u

HOOKS="${ORGANISM_HOOKS:-$HOME/.claude/hooks}"      # symlinked to the repo by deploy.sh (override for tests)
LA="${ORGANISM_LA:-$HOME/Library/LaunchAgents}"   # ORGANISM_LA + ORGANISM_DRY=1 → render-only test
RUNWRAP="$HOOKS/lib/run.sh"
ROLEPY="$HOOKS/lib/role.py"
NS="${ORGANISM_LAUNCHD_NS:-com.organism}"         # launchd label namespace (operational; kept)
. "$(dirname "$0")/../claude/hooks/lib/color.sh"
# This script's own repo, resolved, not assumed at ~/.organism, so a clone anywhere works.
# The converge agent execs $REPO/converge.sh by absolute path (no $HOOKS symlink dependency).
REPO="${ORGANISM_REPO:-$(cd "$(dirname "$0")/.." && pwd -P)}"
LOCAL_AGENTS="${ORGANISM_LOCAL_AGENTS:-$HOME/.claude/state/agents.local}"
# Set by converge.sh to its own launchd label. That agent is what runs THIS script, and
# `launchctl unload` on a running job SIGTERMs it, converge would kill itself mid-run, before
# the orphan sweep and its success marker. Render its plist, skip the reload. (Empty otherwise.)
SELF_LABEL="${ORGANISM_SELF_LABEL:-}"
# job | role | exec-args (after run.sh) | schedule | marker      (marker is OPTIONAL)
#   schedule ∈ keepalive | interval:<sec> | cal:<H>:<M> | calw:<weekday>:<H>:<M>
#   exec-args are whitespace-split into argv (globbing disabled), one token = one argv entry,
#   no quoting: a token can NEVER contain a space, so no spaces in paths/values.
#   marker  = the ~/.claude/state/<marker>-last-success file this job writes on success.
#             Blank = this job writes none. It is NOT always the job name:
#             a job may write a differently named marker, a `-fallback` job shares its parent's
#             marker, and two host-local instances of one script share a single marker, which is
#             why the freshness watch cannot be derived from job names alone.
#             Declaring it here makes `--freshness` derivable and retirement a one-line edit.
AGENTS="
# Personal utilities (e.g. a bot, a calendar reminder) live in host-local
# ~/.claude/state/agents.local, not here. Their code lives in the gitignored
# hooks/local/ directory; the published core names no instance of them.
reconcile|singleton_bookkeeping|python3 $HOOKS/reconcile.py --all --announce|cal:7:45|
converge|per_host|bash $REPO/converge.sh --scheduled|cal:6:30|converge
logrotate|per_host|bash $HOOKS/rotate-logs.sh|calw:0:4:30|logrotate
"

assigned() {  # role -> 0 if this device runs it
  [ "$1" = "per_host" ] && return 0
  "${PYTHON:-python3}" "$ROLEPY" "$1"
}

xml_escape() {  # & < > " ' in any rendered string (arg token, path, label)
  # would corrupt the plist: plutil rejects a bare '&' in a <string> outright.
  local s="$1"
  s=${s//&/&amp;}; s=${s//</&lt;}; s=${s//>/&gt;}; s=${s//\"/&quot;}; s=${s//\'/&apos;}
  printf '%s' "$s"
}

trim() {  # strip leading+trailing whitespace (incl. a stray CR) from $1
  local s="$1"; s="${s#"${s%%[![:space:]]*}"}"; printf '%s' "${s%"${s##*[![:space:]]}"}"
}

_num() { case "$1" in ''|*[!0-9]*) return 1 ;; esac; }
valid_sched() {  # schedule grammar gate. An unknown/typo'd token, from the
  # manifest, agents.local, or a host.json schedules override, would fall through emit_plist's
  # case and render a TRIGGERLESS plist that loads cleanly and never fires: a silently-dead agent
  # the dead-man's-switch design assumes is running. Callers MUST check this BEFORE the
  # `> $plist` redirect truncates the existing (working) plist.
  local hm whm w h m
  case "$1" in
    keepalive)  return 0 ;;
    interval:*) _num "${1#interval:}" ;;
    cal:*)      hm="${1#cal:}"; h="${hm%%:*}"; m="${hm##*:}"
                [ "$h:$m" = "$hm" ] && _num "$h" && _num "$m" ;;
    calw:*)     whm="${1#calw:}"; w="${whm%%:*}"; h="${whm#*:}"; h="${h%%:*}"; m="${whm##*:}"
                [ "$w:$h:$m" = "$whm" ] && _num "$w" && _num "$h" && _num "$m" ;;
    *)          return 1 ;;
  esac
}

# --- read-only introspection: the manifest as a queryable source of truth --------------------
# `--list`      job|role|assigned|sched|marker for every job (core manifest ∪ agents.local).
# `--freshness` the freshness line for the jobs assigned HERE that declare a marker:
#               "<marker> <YYYY-MM-DD>" or "<marker> (never)", space-joined, one line.
#
# --freshness lets the reconciler's job_freshness probe derive the job list from this manifest
# instead of a hand-kept list in reconcile-pairings.json, so retiring a job is a one-line edit.
# Neither mode touches launchd, ~/.claude, or the plists; both run before the deploy guard so
# they work on a machine that has never deployed.
each_organ() {  # stdout: job|role|args|sched|marker for core then local manifests (raw, untrimmed)
  printf '%s\n' "$AGENTS"
  if [ -f "$LOCAL_AGENTS" ]; then
    while IFS= read -r line || [ -n "$line" ]; do printf '%s\n' "$line"; done < "$LOCAL_AGENTS"
  fi
}

mode_list() {
  each_organ | while IFS='|' read -r organ role args sched marker; do
    organ="$(trim "${organ:-}")"; role="$(trim "${role:-}")"
    case "$organ" in ''|'#'*) continue ;; esac
    if assigned "$role"; then a=yes; else a=no; fi
    printf '%s|%s|%s|%s|%s\n' "$organ" "$role" "$a" "$(trim "${sched:-}")" "$(trim "${marker:-}")"
  done
}

mode_freshness() {
  seen=" "; out=""
  while IFS='|' read -r organ role _a _s marker; do
    [ -n "$marker" ] || continue
    [ "$_a" = yes ] || continue                       # an unassigned job has no marker to be stale
    case "$seen" in *" $marker "*) continue ;; esac   # two jobs, one marker (fallbacks)
    seen="$seen$marker "
    f="$HOME/.claude/state/$marker-last-success"
    if [ -f "$f" ]; then out="$out$marker $(tr -d '\n' < "$f") "; else out="$out$marker (never) "; fi
  done <<EOF
$(mode_list)
EOF
  printf '%s\n' "$(trim "$out")"
}

case "${1:-}" in
  --list)      mode_list; exit 0 ;;
  --freshness) mode_freshness; exit 0 ;;
esac
mkdir -p "$LA" "$HOME/.claude/logs"

# Never install from a copy inside Claude Code's plugin cache: it is replaced on every
# plugin update, and the plists would point at a folder that is about to disappear.
if [ "$(python3 "$REPO/claude/hooks/lib/where.py" in-cache 2>/dev/null)" = yes ]; then
  echo "✗ $REPO is inside Claude Code's plugin cache; launchd jobs can't run from it." >&2
  echo "  Clone the repo and run bootstrap.sh there for the background jobs." >&2
  exit 1
fi

emit_plist() {  # label  exec-args  schedule  logpath   (stdout IS the plist; warnings go to stderr)
  local label="$1" args="$2" sched="$3" log="$4" a
  printf '<?xml version="1.0" encoding="UTF-8"?>\n'
  printf '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
  printf '<plist version="1.0"><dict>\n'
  printf '  <key>Label</key><string>%s</string>\n' "$(xml_escape "$label")"
  printf '  <key>ProgramArguments</key><array>\n    <string>%s</string>\n' "$(xml_escape "$RUNWRAP")"
  set -f  # no glob expansion during the argv split: a '*.sh' token would
          # expand to whatever filenames sat in install-agents' CWD
  for a in $args; do
    # defensive: can't fire while tokens come from IFS whitespace-splitting, but the
    # no-whitespace-in-a-token constraint is load-bearing, scream if it is ever violated.
    case "$a" in *[[:space:]]*) echo "  ⚠️ $label: arg token contains whitespace after splitting: '$a'" >&2 ;; esac
    printf '    <string>%s</string>\n' "$(xml_escape "$a")"
  done
  set +f
  printf '  </array>\n'
  case "$sched" in
    keepalive)  printf '  <key>KeepAlive</key><true/>\n  <key>RunAtLoad</key><true/>\n  <key>ThrottleInterval</key><integer>10</integer>\n' ;;
    interval:*) printf '  <key>StartInterval</key><integer>%s</integer>\n  <key>RunAtLoad</key><true/>\n' "${sched#interval:}" ;;
    cal:*)      local hm="${sched#cal:}"; printf '  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>%s</integer><key>Minute</key><integer>%s</integer></dict>\n' "${hm%%:*}" "${hm##*:}" ;;
    calw:*)     local whm="${sched#calw:}" w hm; w="${whm%%:*}"; hm="${whm#*:}"; printf '  <key>StartCalendarInterval</key><dict><key>Weekday</key><integer>%s</integer><key>Hour</key><integer>%s</integer><key>Minute</key><integer>%s</integer></dict>\n' "$w" "${hm%%:*}" "${hm##*:}" ;;
    *)          echo "  ⚠️ $label: unknown schedule '$sched' reached emit_plist: callers must gate on valid_sched" >&2 ;;
  esac
  printf '  <key>StandardOutPath</key><string>%s</string>\n  <key>StandardErrorPath</key><string>%s</string>\n' "$(xml_escape "$log")" "$(xml_escape "$log")"
  printf '  <key>EnvironmentVariables</key><dict><key>HOME</key><string>%s</string></dict>\n' "$(xml_escape "$HOME")"
  printf '</dict></plist>\n'
}

# --- per-machine schedule overrides: host.json "schedules": {"<job>":"<sched-token>"} ---
# Lets each machine retune a scheduled agent's time WITHOUT editing this committed manifest
# (decouples the one machine-specific bit of the harness core). Absent key → the manifest default.
# Same grammar as the sched field: keepalive | interval:N | cal:H:M | calw:W:H:M.
HOST_JSON="${ORGANISM_HOST_JSON:-$HOME/.claude/state/host.json}"
schedule_override() {  # job -> prints the host.json override token, or nothing
  [ -f "$HOST_JSON" ] || return 0
  "${PYTHON:-python3}" - "$1" "$HOST_JSON" <<'PY' 2>/dev/null
import json, sys
organ, path = sys.argv[1], sys.argv[2]
try:
    print(json.load(open(path)).get("schedules", {}).get(organ, ""))
except Exception:
    pass
PY
}

# Without run.sh every loaded agent would crash-loop at spawn, so refuse to install against an undeployed hooks dir.
# Test-seam safety. ORGANISM_LA redirects where plists are RENDERED, but
# `launchctl load <path>` registers the job by the Label INSIDE it, in the REAL launchd. So a
# test that overrode ORGANISM_LA and forgot ORGANISM_DRY would unload the live agent of that
# name and rebind its label to a scratch plist that the test is about to delete, leaving a job
# pointing at nothing. (This is not hypothetical.) An overridden
# LA can only ever mean "render somewhere else", never "reload the live daemon", force dry.
if [ "$LA" != "$HOME/Library/LaunchAgents" ] && [ -z "${ORGANISM_DRY:-}" ]; then
  echo "⚠️ ORGANISM_LA is overridden but ORGANISM_DRY is not set: forcing dry (refusing to touch the real launchd)" >&2
  ORGANISM_DRY=1
fi
# Launch safety: launchd is per account, not per HOME. With HOME pointed at a sandbox,
# `launchctl load` would still replace this account's live jobs of the same name, so a
# HOME that is not the account's own home only ever renders (dry run).
ACCOUNT_HOME="$(python3 -c 'import os, pwd; print(pwd.getpwuid(os.getuid()).pw_dir)' 2>/dev/null || true)"
if [ -z "${ORGANISM_DRY:-}" ] && { [ -z "$ACCOUNT_HOME" ] || \
     [ "$(cd -P "$HOME" 2>/dev/null && pwd)" != "$(cd -P "$ACCOUNT_HOME" 2>/dev/null && pwd)" ]; }; then
  echo "⚠️ HOME ($HOME) is not this account's home (${ACCOUNT_HOME:-unknown}): forcing dry (refusing to touch the real launchd)" >&2
  ORGANISM_DRY=1
fi

[ -f "$RUNWRAP" ] || { echo "✗ $RUNWRAP missing: run deploy.sh first (agents would crash-loop at spawn)" >&2; exit 1; }
[ -x "$RUNWRAP" ] || chmod +x "$RUNWRAP" 2>/dev/null
# exec-args are whitespace-split, so a HOOKS/HOME containing whitespace
# corrupts every rendered argv mid-path, warn loudly up front (the realistic corruption vector).
case "$HOOKS$HOME$REPO" in *[[:space:]]*) echo "⚠️ HOOKS, HOME or REPO contains whitespace: exec-args are whitespace-split, agent argv will be corrupted" >&2 ;; esac

h1 "install-agents on $("$HOOKS/lib/hostid.sh" label 2>/dev/null)"
EXPECTED=" "   # every label named by either manifest (assigned or not); the orphan sweep below
               # removes any $NS plist NOT in this set. Space-delimited, space-padded.
MARKERS=" "    # every marker DECLARED by either manifest; the residue report below names any
               # state/<x>-last-success outside this set. Space-delimited, space-padded.
while IFS='|' read -r organ role args sched marker; do
  # Skip blank + #comment rows. This loop, unlike each_organ and the agents.local loop,
  # never skipped them: each became a bogus label, warned, and polluted EXPECTED/MARKERS
  # (a comment word colliding with a real marker would mute its stale report).
  case "$organ" in ''|'#'*) continue ;; esac
  label="$NS.$organ"; plist="$LA/$label.plist"; log="$HOME/.claude/logs/$organ.launchd.log"
  EXPECTED="$EXPECTED$label "
  MARKERS="$MARKERS$(trim "${marker:-}") "
  ov=$(schedule_override "$organ"); [ -n "$ov" ] && sched="$ov"   # host.json per-machine time override
  if ! valid_sched "$sched"; then echo "  ${CY}⚠️ unknown schedule '$sched' for $label: skipped, existing plist left untouched${CX}"; continue; fi
  if assigned "$role"; then
    emit_plist "$label" "$args" "$sched" "$log" > "$plist"
    # Self check FIRST: it is the stronger constraint (never reload), and putting it after the
    # dry branch made it unobservable, and therefore untestable, under ORGANISM_DRY.
    if [ -n "$SELF_LABEL" ] && [ "$label" = "$SELF_LABEL" ]; then
      # We are running INSIDE this agent. `launchctl unload` would SIGTERM us mid-run, before
      # the orphan sweep below and before converge.sh writes its success marker, the agent
      # would silently truncate its own job every time. Plist is refreshed on disk; launchd
      # picks up a changed schedule at the next boot or the next non-self run.
      echo "  rendered $label  ($role) [self: reload skipped, converge is running]"
    elif [ -n "${ORGANISM_DRY:-}" ]; then echo "  rendered $label  ($role) [dry]"
    else
      launchctl unload "$plist" 2>/dev/null
      if launchctl load "$plist" 2>/dev/null; then echo "  ${CG}loaded${CX}   ${CB}$label${CX}  ${CD}($role)${CX}"; else echo "  ${CR}⚠️ load failed${CX} $label"; fi
    fi
  else
    if [ -f "$plist" ] && [ -z "${ORGANISM_DRY:-}" ]; then launchctl unload "$plist" 2>/dev/null; rm -f "$plist"; echo "  ${CY}removed${CX}  $label  ${CD}($role not assigned here)${CX}"; else echo "  ${CD}skipped  $label  ($role not assigned here)${CX}"; fi
  fi
done <<< "$AGENTS"   # here-string, not a pipe: the loop must run in THIS shell so EXPECTED persists

# Host-local INSTANCE agents, the standalone-agent analogue of the gitignored paths.env: a
# manifest the published core discovers GENERICALLY and names none of. Same
# `job|role|args|sched` format as AGENTS above; $HOOKS/$HOME are expanded; #comments + blanks
# ignored; arg tokens are whitespace-split (no spaces inside a token, see AGENTS above). Lets a
# per-host instance (e.g. a personal report) run as its own agent without its codename ever
# entering committed code. Missing file → no-op. Template: config/agents.local.example.
LOCAL_AGENTS="${ORGANISM_LOCAL_AGENTS:-$HOME/.claude/state/agents.local}"
if [ -f "$LOCAL_AGENTS" ]; then
  h1 "host-local agents from ${CD}$LOCAL_AGENTS${CX}"
  # `|| [ -n "$organ" ]`: a final line missing its trailing newline would
  # be silently dropped (read returns nonzero on an unterminated last line), uninstalling the
  # host's local agent with zero output.
  while IFS='|' read -r organ role args sched marker || [ -n "$organ" ]; do
    # trim every leading space: stripping only one would let an indented #comment
    # parse as an agent and a leading-space job name leak the space into its launchd label.
    organ="$(trim "$organ")"; role="$(trim "$role")"; sched="$(trim "$sched")"
    case "$organ" in ''|'#'*) continue ;; esac
    args=${args//\$HOOKS/$HOOKS}; args=${args//\$HOME/$HOME}
    label="$NS.$organ"; plist="$LA/$label.plist"; log="$HOME/.claude/logs/$organ.launchd.log"
    EXPECTED="$EXPECTED$label "
    MARKERS="$MARKERS$(trim "${marker:-}") "   # 5th field optional: a 4-field row declares none
    ov=$(schedule_override "$organ"); [ -n "$ov" ] && sched="$ov"   # host.json per-machine time override
    if ! valid_sched "$sched"; then echo "  ${CY}⚠️ unknown schedule '$sched' for $label: skipped, existing plist left untouched${CX}"; continue; fi
    if assigned "$role"; then
      emit_plist "$label" "$args" "$sched" "$log" > "$plist"
      if [ -n "$SELF_LABEL" ] && [ "$label" = "$SELF_LABEL" ]; then
        echo "  rendered $label  ($role) [local, self: reload skipped, converge is running]"
      elif [ -n "${ORGANISM_DRY:-}" ]; then echo "  rendered $label  ($role) [local,dry]"
      else
        launchctl unload "$plist" 2>/dev/null
        if launchctl load "$plist" 2>/dev/null; then echo "  loaded   $label  ($role) [local]"; else echo "  ${CR}⚠️ load failed${CX} $label"; fi
      fi
    else
      if [ -f "$plist" ] && [ -z "${ORGANISM_DRY:-}" ]; then launchctl unload "$plist" 2>/dev/null; rm -f "$plist"; echo "  removed  $label  ($role not assigned here) [local]"; else echo "  skipped  $label  ($role not assigned here) [local]"; fi
    fi
  done < "$LOCAL_AGENTS"
fi

# Orphan sweep: a renamed or deleted job would leave its old plist installed and LOADED forever, since the loops above
# only ever touch jobs still PRESENT in a manifest. Reconcile the $NS namespace against the
# expected set; plists outside $NS are never touched. Report-only under ORGANISM_DRY.
for plist in "$LA/$NS."*.plist; do
  [ -f "$plist" ] || continue
  label="$(basename "$plist" .plist)"
  case "$EXPECTED" in *" $label "*) continue ;; esac
  if [ -n "${ORGANISM_DRY:-}" ]; then echo "  orphan   $label  (in neither manifest) [dry: would remove]"
  else launchctl unload "$plist" 2>/dev/null; rm -f "$plist"; echo "  removed  $label  (orphan: in neither manifest)"; fi
done

# Residue report (NOT a sweep, deliberately). Retiring a job also strands its
# success marker, which then reads "(never)" or freezes at its last date forever: a REMOVED job
# disguised as a SILENTLY-DYING one (the freshness signal inverted).
#
# This reports and does not delete, and the asymmetry is load-bearing. The orphan plist sweep is
# safe because a launchd label maps 1:1 to a job. Markers do NOT: a job may write a
# differently named marker, every `-fallback` job shares its parent's marker, and two host-local instances
# of one script share a single marker. A marker deleted while its job still lives re-arms that
# job's post-noon --fallback gate, it would re-run a job that already succeeded. So we
# name the residue and let a human (who can read `--list`) remove it. `rm` is one line; a
# wrongly-disarmed dead-man's-switch is invisible until the day it matters.
for f in "$HOME/.claude/state/"*-last-success; do
  [ -f "$f" ] || continue
  m="$(basename "$f")"; m="${m%-last-success}"
  case "$MARKERS" in *" $m "*) continue ;; esac
  echo "  ⚠️ stale marker  state/$m-last-success  (no job in either manifest uses it)"
  echo "     job retired? remove it by hand:  rm ~/.claude/state/$m-last-success"
done
