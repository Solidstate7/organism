#!/usr/bin/env bash
# adr-link.sh, reusable, OPT-IN ADR<->commit linkage for any git repo.
#
# Turns the ADR<->commit join from uneven prose ("(ADR-12)" in a subject, a hash
# in an ADR body) into a typed, queryable, BIDIRECTIONAL link. Companion doctrine:
# the "Retroactive ADR<->commit Linking" synthesis note. Pattern-sibling of
# any repo-level commit hook (composes; fails safe).
#
# Subcommands:
#   commit-msg <file>  git commit-msg hook: require an `ADR:` trailer whose number
#                      resolves to <adrdir>/NNNN-*.md (or the literal `none`).
#                      The FORWARD link, enforced. Fails safe (off if misconfigured).
#   backfill           one-time RETRO pass: promote existing in-message ADR mentions
#                      across ALL history into `git notes --ref=adr`. Non-destructive
#                      (never rewrites a commit / never touches the working tree).
#                      Skips commits that already carry a note. Re-runnable.
#   backlinks [write]  reverse index (ADR -> commits), from notes + trailers, each
#                      commit tagged [records] (touched the ADR file) vs [implements].
#                      No arg => print to stdout (no mutation). `write` => inject an
#                      idempotent <!-- adr-links --> block into each ADR file.
#   query <N|hash>     print the link both directions for an ADR number or a commit.
#
# OPT-IN, per repo:
#   # forward enforcement (a 1-line shim; never auto-installed):
#   printf '#!/bin/sh\nexec organism adr-link commit-msg "$1"\n' \
#     > .git/hooks/commit-msg && chmod +x .git/hooks/commit-msg
#   # retro link + reverse index (run once, then on each /handoff):
#   organism adr-link backfill && organism adr-link backlinks write
#
# Config (optional) env ADR_LINK_ENFORCE=require|warn|off (default require).
# ADR dir: detect-and-conform, resolved the same way as /handoff step 1a (see adr_dir).
set -euo pipefail

NOTES_REF=adr
err() { printf 'adr-link: %s\n' "$*" >&2; }

repo_root() { git rev-parse --show-toplevel 2>/dev/null; }

# The ADR dir under base dir $1, first hit wins, in /handoff step 1a's order: a
# `.adr-dir` file (adr-tools); a log4brains config's project.adrFolder; the first
# existing of the usual folder names; else docs/adr. Prints an absolute path.
_resolve_adr_dir() {
  local base="$1" rel="" f c
  [ -f "$base/.adr-dir" ] && rel=$(tr -d '[:space:]' < "$base/.adr-dir")
  if [ -z "$rel" ]; then
    for f in .log4brains.yml log4brains.yml; do
      [ -f "$base/$f" ] || continue
      rel=$(_log4brains_folder "$base/$f"); rel=${rel#[\"\']}; rel=${rel%[\"\']}
      [ -n "$rel" ] && break
    done
  fi
  if [ -z "$rel" ]; then
    for c in docs/adr docs/decisions docs/architecture/decisions doc/adr adr architecture/decisions; do
      [ -d "$base/$c" ] && { rel=$c; break; }
    done
  fi
  # squeeze repeated slashes before dropping leading "./", so ".//x" stays relative
  rel=$(printf '%s' "${rel:-docs/adr}" | tr -s /)
  while [ "${rel#./}" != "$rel" ]; do rel=${rel#./}; done
  rel=${rel%/}
  case "$rel" in /*) printf '%s\n' "$rel" ;; *) printf '%s/%s\n' "$base" "$rel" ;; esac
}

# project.adrFolder from a log4brains config. Only the key at the project block's own
# indent counts, so a package's adrFolder (nested deeper) is never taken.
_log4brains_folder() {
  awk '
    /^[^[:space:]#]/ { inp = ($0 ~ /^project:[[:space:]]*(#.*)?$/); next }
    inp && /^[[:space:]]+[^[:space:]#]/ {
      match($0, /^[[:space:]]+/); if (!ind) ind = RLENGTH
      if (RLENGTH == ind && sub(/^[[:space:]]+adrFolder:[[:space:]]*/, "")) {
        sub(/[[:space:]]+#.*$/, ""); sub(/[[:space:]]+$/, ""); print; exit
      }
    }' "$1" 2>/dev/null || true
}

adr_dir() {
  local root; root=$(repo_root) || { echo ""; return; }
  _resolve_adr_dir "$root"
}

# Clean a stream of digit tokens -> bare integers (strip leading zeros to avoid
# printf octal parsing; drop 0 / empties / ADR-0 template).
_norm() { grep -oE '[0-9]{1,4}' 2>/dev/null | sed 's/^0*//' | grep -E '^[0-9]+$' || true; }

# From free PROSE (commit messages): only ADR/DEC-prefixed tokens. Tolerates the
# "ADR-12", "ADR 12", "ADR: 0012", "adr_7", "DEC-0003" forms (CLAUDE.md may refer to
# decisions as DEC-NNNN; /handoff commits use that prefix).
extract_nums() { grep -oiE '(adr|dec)[-_: ]*[0-9]{1,4}' 2>/dev/null | _norm || true; }

# From already ADR-scoped VALUES (a git note body line `ADR: 0012`, or a
# `%(trailers:valueonly)` output `0012`): just take the digits.
extract_vals() { _norm; }

# Does the repo carry a substantial decision-bearing doc (CLAUDE.md / AGENTS.md /
# README)? Such a doc with few/no ADRs means decisions live in prose, not ADRs, a
# retro candidate even when git history is shallow (e.g. a project git-tracked late).
_has_decision_docs() {
  local root="$1" f
  for f in CLAUDE.md AGENTS.md README.md docs/README.md; do
    [ -f "$root/$f" ] && [ "$(wc -l < "$root/$f" 2>/dev/null || echo 0)" -gt 40 ] && return 0
  done
  return 1
}

# Kind of a commit relative to ADR number-pad $2: "records" if it changed the ADR
# file itself (a /handoff recording/maintenance commit, NOT an implementation),
# else "implements". The records/implements split keeps recording commits out of
# the cross-validation signal: "is this decision implemented?" counts only implements.
_kind_for() {
  if git diff-tree --root --no-commit-id --name-only -r "$1" 2>/dev/null \
       | grep -qE "(^|/)${2}-[^/]*\.md$"; then echo records; else echo implements; fi
}

# Is commit $1 WORK (it changes something besides the decision layer)? $2 = the ADR
# dir relative to the repo root. Not work: a commit whose files are all ADRs,
# CLAUDE.md, HANDOFF.md or .gitignore (the index, the brief, /handoff's
# "chore: keep HANDOFF.md out of git"), and /handoff's own "chore: stop tracking
# generated <dir>" (it deletes the dir's files from the index). --root makes a repo's
# first commit list its files; without it the first commit listed none and was never
# linked.
_is_work() {
  git log -1 --format=%s "$1" 2>/dev/null | grep -q '^chore: stop tracking generated ' && return 1
  git diff-tree --root --no-commit-id --name-only -r "$1" 2>/dev/null \
    | grep -qvE "^(${2}/|CLAUDE\.md$|HANDOFF\.md$|\.gitignore$)"
}

# Does NNNN-*.md exist for integer $1 in the ADR dir? echoes the basename if so.
adr_file_for() {
  local n4 d; n4=$(printf '%04d' "$1"); d=$(adr_dir)
  [ -n "$d" ] || return 1
  local m; m=$(ls "$d"/"$n4"-*.md 2>/dev/null | head -1) || return 1
  [ -n "$m" ] && basename "$m"
}

# Last-modified time of file $1 in epoch seconds (GNU stat, else BSD stat).
_mtime() { stat -c %Y "$1" 2>/dev/null || stat -f %m "$1" 2>/dev/null || true; }

# Epoch seconds $1 as a local "YYYY-MM-DD HH:MM" (BSD date, else GNU date).
_when() { date -r "$1" '+%Y-%m-%d %H:%M' 2>/dev/null || date -d "@$1" '+%Y-%m-%d %H:%M' 2>/dev/null || echo "@$1"; }

# ---- subcommand: commit-msg (forward link enforcement) ----------------------
cmd_commit_msg() {
  local msgfile="${1:?commit-msg: message file required}"
  local mode="${ADR_LINK_ENFORCE:-require}"
  [ "$mode" = off ] && exit 0

  local root; root=$(repo_root) || exit 0           # not a repo -> don't block
  # Merge commits fire commit-msg too; don't demand a trailer on a merge.
  git -C "$root" rev-parse -q --verify MERGE_HEAD >/dev/null 2>&1 && exit 0
  [ -d "$(adr_dir)" ] || exit 0                      # no ADR dir -> nothing to enforce

  # Strip comments, then parse trailers.
  local trailers vals
  trailers=$(git -C "$root" interpret-trailers --parse "$msgfile" 2>/dev/null || true)
  vals=$(printf '%s\n' "$trailers" | sed -n 's/^[Aa][Dd][Rr]:[[:space:]]*//p')

  if [ -z "$vals" ]; then
    err "no 'ADR:' trailer. Add one, e.g.  ADR: 12   (or  ADR: none  for a non-architectural change)."
    [ "$mode" = warn ] && exit 0 || exit 1
  fi

  # Validate every value: 'none'/'n/a' allowed, else must resolve to an ADR file.
  local v low bad=0
  for v in $vals; do
    low=$(printf '%s' "$v" | tr '[:upper:]' '[:lower:]')
    case "$low" in
      none|n/a|na|-) continue ;;
    esac
    local n; n=$(printf '%s' "$v" | sed 's/^[Aa][Dd][Rr][-_ ]*//; s/^0*//')
    if ! printf '%s' "$n" | grep -qE '^[0-9]{1,4}$' || ! adr_file_for "$n" >/dev/null; then
      err "ADR trailer '$v' does not resolve to a file in $(adr_dir | sed "s#$root/##")."
      bad=1
    fi
  done
  if [ "$bad" = 1 ]; then
    [ "$mode" = warn ] && exit 0 || exit 1
  fi
  exit 0
}

# ---- subcommand: backfill (retroactive link via git notes) ------------------
cmd_backfill() {
  local root; root=$(repo_root) || { err "not in a git repo"; exit 1; }
  cd "$root"
  local added=0 skipped=0 touched=0
  local H nums n n4 have note
  while IFS= read -r H; do
    # Already noted? leave it (non-destructive; preserves manual/prior links).
    if git notes --ref="$NOTES_REF" show "$H" >/dev/null 2>&1; then
      skipped=$((skipped+1)); continue
    fi
    # Gather ADR numbers from the full commit message.
    nums=$(git log -1 --format='%B' "$H" | extract_nums | sort -un)
    [ -z "$nums" ] && continue
    note=""
    for n in $nums; do
      adr_file_for "$n" >/dev/null || continue        # ignore non-ADR numbers/typos
      n4=$(printf '%04d' "$n")
      note="${note}ADR: ${n4}"$'\n'
    done
    [ -z "$note" ] && continue
    git notes --ref="$NOTES_REF" add -m "${note%$'\n'}" "$H"
    added=$((added+1)); touched=$((touched+1))
  done < <(git log --all --no-merges --format='%H')

  # Second pass, RETRO links from ADR bodies. A reconstructed ADR's implementing
  # commit predates it and can't name it, so the loop above can't find these; but
  # the ADR's provenance line cites the hash, and that line is committed (durable).
  # Reading it back makes retro links REPRODUCIBLE from the ADR, not just the notes.
  local d f n4 h relinked=0
  d=$(adr_dir)
  for f in "$d"/[0-9][0-9][0-9][0-9]-*.md; do
    [ -e "$f" ] || continue; n4=$(basename "$f" | grep -oE '^[0-9]{4}'); [ "$n4" = 0000 ] && continue
    while IFS= read -r h; do
      git rev-parse -q --verify "${h}^{commit}" >/dev/null 2>&1 || continue   # only real commits
      ( cmd_link "$h" "$((10#$n4))" ) >/dev/null 2>&1 && relinked=$((relinked+1)) || true
    done < <(grep -iE 'implement|provenance' "$f" 2>/dev/null | grep -oiE '[0-9a-f]{7,40}' | sort -u)
  done

  err "backfill: $added commit(s) linked from messages, $skipped already noted, $relinked retro link(s) from ADR provenance bodies."
  err "undo:    git update-ref -d refs/notes/$NOTES_REF"
}

# Collect "H<TAB>NNNN" pairs from BOTH notes and ADR: trailers, across history.
_pairs() {
  local root; root=$(repo_root); cd "$root"
  # from notes
  git notes --ref="$NOTES_REF" list 2>/dev/null | awk '{print $2}' | while IFS= read -r H; do
    git notes --ref="$NOTES_REF" show "$H" 2>/dev/null | extract_vals | sort -un \
      | while IFS= read -r n; do printf '%s\t%04d\n' "$H" "$n"; done
  done
  # from trailers (future, enforced commits)
  git log --all --no-merges --format='%H%x1f%(trailers:key=ADR,valueonly,separator=%x20)' 2>/dev/null \
    | while IFS=$'\x1f' read -r H tv; do
        [ -z "$tv" ] && continue
        printf '%s' "$tv" | extract_vals | sort -un \
          | while IFS= read -r n; do printf '%s\t%04d\n' "$H" "$n"; done
      done
}

# ---- subcommand: backlinks (reverse index ADR -> commits) -------------------
cmd_backlinks() {
  local write="${1:-}"; local root; root=$(repo_root) || { err "not in a git repo"; exit 1; }
  cd "$root"; local d; d=$(adr_dir)
  local pairs; pairs=$(_pairs | sort -u)
  [ -z "$pairs" ] && { err "no links yet: run 'backfill' first (or add ADR: trailers)."; exit 0; }

  local f n4 block H subj short impl rec listed
  for f in "$d"/[0-9][0-9][0-9][0-9]-*.md; do
    [ -e "$f" ] || continue
    n4=$(basename "$f" | grep -oE '^[0-9]{4}'); [ "$n4" = 0000 ] && continue
    listed=$(printf '%s\n' "$pairs" | awk -F'\t' -v n="$n4" '$2==n{print $1}' | sort -u)
    [ -z "$listed" ] && continue
    impl=""; rec=""
    while IFS= read -r H; do
      subj=$(git log -1 --format='%s' "$H" | cut -c1-90); short=$(git rev-parse --short "$H")
      if [ "$(_kind_for "$H" "$n4")" = records ]; then rec="${rec}- \`$short\` ${subj}"$'\n'
      else impl="${impl}- \`$short\` ${subj}"$'\n'; fi
    done <<< "$listed"
    # Implemented-by is the cross-validation set; an empty one is surfaced, not hidden.
    block="## Implemented by"$'\n'
    if [ -n "$impl" ]; then block="${block}${impl}"
    else block="${block}_(none yet: decision recorded but no implementing commit linked)_"$'\n'; fi
    [ -n "$rec" ] && block="${block}## Recorded by"$'\n'"${rec}"
    block="<!-- adr-links:start (generated by adr-link.sh: do not hand-edit) -->
${block}<!-- adr-links:end -->"
    if [ "$write" = write ]; then
      _inject_block "$f" "$block"
      err "wrote links into $(basename "$f")"
    else
      printf '### %s\n%s\n\n' "$(basename "$f")" "$block"
    fi
  done
}

# Replace the marked block in $1 with $2, or append it (idempotent).
# awk-free: BSD awk rejects a multiline -v value. sed-delete any existing block
# (no-op first time), then re-append a fresh one at the end. The command sub reads
# the file fully before the redirection truncates it, so writing back to "$f" is safe.
_inject_block() {
  local f="$1" blk="$2" body
  body=$(sed '/<!-- adr-links:start/,/<!-- adr-links:end/d' "$f")
  printf '%s\n\n%s\n' "$body" "$blk" > "$f"
}

# ---- subcommand: query ------------------------------------------------------
cmd_query() {
  local arg="${1:?query: ADR number or commit hash required}"; shift || true
  local impl_only=0; [ "${1:-}" = --implemented ] && impl_only=1
  local root; root=$(repo_root) || { err "not in a git repo"; exit 1; }
  cd "$root"
  if printf '%s' "$arg" | grep -qiE '^(adr[-_ ]?)?[0-9]{1,4}$'; then
    local n n4 kind; n=$(printf '%s' "$arg" | sed 's/^[Aa][Dd][Rr][-_ ]*//; s/^0*//'); n4=$(printf '%04d' "$n")
    printf 'ADR-%s -> commits%s:\n' "$n4" "$( [ "$impl_only" = 1 ] && echo ' (implements only)')"
    _pairs | awk -F'\t' -v n="$n4" '$2==n{print $1}' | sort -u | while IFS= read -r H; do
      kind=$(_kind_for "$H" "$n4")
      [ "$impl_only" = 1 ] && [ "$kind" = records ] && continue
      printf '  %s  %-10s %s\n' "$(git rev-parse --short "$H")" "$kind" "$(git log -1 --format='%s' "$H" | cut -c1-76)"
    done
  else
    printf 'commit %s -> ADRs:\n' "$arg"
    { git notes --ref="$NOTES_REF" show "$arg" 2>/dev/null || true
      git show -s --format='%(trailers:key=ADR,valueonly)' "$arg" 2>/dev/null || true; } \
      | extract_vals | sort -un | while IFS= read -r n; do printf '  ADR-%04d  %s\n' "$n" "$(adr_file_for "$n" || echo '(no file)')"; done
  fi
}

# ---- subcommand: link (explicit commit<->ADR, for RETRO) --------------------
# Annotate a SPECIFIC historical commit with one or more ADR numbers via a note.
# This is the ONLY way to link a retro ADR to its implementing commit: that commit
# predates the ADR, so it can never name it (backfill/enforcer both fail). Union-
# merges with any existing note; idempotent; never rewrites the commit.
cmd_link() {
  local commit="${1:?link: <commit> required}"; shift
  [ "$#" -ge 1 ] || { err "link: at least one ADR number required"; exit 2; }
  local root; root=$(repo_root) || { err "link: not a git repo"; exit 1; }; cd "$root"
  local H; H=$(git rev-parse -q --verify "${commit}^{commit}" 2>/dev/null) \
    || { err "link: unknown commit '$commit'"; exit 1; }
  local have new n all note
  have=$(git notes --ref="$NOTES_REF" show "$H" 2>/dev/null | extract_vals || true)
  new=""
  for n in "$@"; do
    n=$(printf '%s' "$n" | sed 's/^[A-Za-z]*[-_: ]*//; s/^0*//')
    printf '%s' "$n" | grep -qE '^[0-9]{1,4}$' || { err "link: '$n' not a number: skipped"; continue; }
    adr_file_for "$n" >/dev/null || { err "link: ADR-$(printf '%04d' "$n") has no file: skipped"; continue; }
    new="${new}${n}"$'\n'
  done
  all=$(printf '%s\n%s\n' "$have" "$new" | _norm | sort -un)
  [ -z "$all" ] && { err "link: nothing valid to link"; exit 1; }
  note=""; for n in $all; do note="${note}ADR: $(printf '%04d' "$n")"$'\n'; done
  git notes --ref="$NOTES_REF" add -f -m "${note%$'\n'}" "$H"
  err "linked $(git rev-parse --short "$H") -> $(for n in $all; do printf 'ADR-%04d ' "$n"; done)"
}

# ---- subcommand: link-session (FORWARD harvest) -----------------------------
# Link every WORK commit since the last handoff to the new ADR number(s). Needed
# because a decision harvested at session end gets its ADR number only then, so its
# implementing commits (made earlier this session) can't carry an ADR: trailer.
# Boundary = the previous `docs(adr):` recording commit, plus a time bound: the later
# of `--since <git date>` and HANDOFF.md's last write (the previous brief), or, with
# neither and no previous record, 24 hours ago. Without it a first handoff in an old
# repo would tie its whole history to one new record. Commits older than the bound
# are never linked; the summary counts them and says how to include them. `--all`
# drops the HANDOFF.md and 24-hour bounds (an explicit --since still applies).
# Doc/maintenance commits (see _is_work: the ADR dir, CLAUDE.md, the brief, .gitignore)
# are skipped, they're not work. `--dry` prints the selection without writing notes.
# Options go before the ADR numbers; a number with no record file is reported and
# skipped before anything is linked.
cmd_link_session() {
  local dry=0 all=0 since=""
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --dry) dry=1; shift ;;
      --all) all=1; shift ;;
      --since|--since=*)
        if [ "$1" = --since ]; then since=${2:-}; shift; else since=${1#--since=}; fi; shift || true
        [ -n "$since" ] || { err "link-session: --since needs a date"; exit 2; } ;;
      *) break ;;
    esac
  done
  [ "$#" -ge 1 ] || { err "link-session: ADR number(s) required"; exit 2; }
  local root; root=$(repo_root) || { err "link-session: not a git repo"; exit 1; }; cd "$root"
  local d adrrel prev range H ct linked=0 skipped=0 older=0
  d=$(adr_dir); adrrel=${d#"$root"/}
  # check every number up front: a flag after the numbers would otherwise be read as
  # one, and cmd_link's own refusal would end the run before the summary
  local a n nums=""
  for a in "$@"; do
    case "$a" in -*) err "link-session: '$a' comes after the ADR numbers; put options first"; exit 2 ;; esac
    n=$(printf '%s' "$a" | sed 's/^[A-Za-z]*[-_: ]*//; s/^0*//')
    printf '%s' "$n" | grep -qE '^[0-9]{1,4}$' || { err "link-session: '$a' is not an ADR number"; exit 2; }
    adr_file_for "$n" >/dev/null || { err "link-session: ADR-$(printf '%04d' "$n") has no file in ${adrrel}; skipped"; continue; }
    nums="$nums $n"
  done
  [ -n "$nums" ] || { err "link-session: none of the ADR numbers has a file in ${adrrel}; nothing linked"; exit 1; }
  # shellcheck disable=SC2086
  set -- $nums
  prev=$(git log -F --grep='docs(adr):' --max-count=1 --format='%H' "HEAD^" 2>/dev/null || true)
  [ -n "$prev" ] && range="${prev}..HEAD" || range="HEAD"
  # the time bound (epoch seconds; empty = none) and what set it
  local bound="" why="" t
  if [ -n "$since" ]; then
    bound=$(git rev-parse --since="$since" 2>/dev/null | sed -n 's/^--max-age=//p')
    # git reads a date it cannot parse as "now", which would silently link nothing
    if [ -z "$bound" ] || [ "$bound" -ge $(( $(date +%s) - 1 )) ]; then
      err "link-session: --since '$since' does not read as a past date (try \"YYYY-MM-DD HH:MM\" or \"3 hours ago\")"; exit 2
    fi
    why="--since $since"
  fi
  if [ "$all" = 0 ] && [ -f "$root/HANDOFF.md" ]; then
    t=$(_mtime "$root/HANDOFF.md")
    if [ -n "$t" ] && { [ -z "$bound" ] || [ "$t" -gt "$bound" ]; }; then bound=$t; why="HANDOFF.md last written"; fi
  fi
  if [ "$all" = 0 ] && [ -z "$bound" ] && [ -z "$prev" ]; then
    bound=$(( $(date +%s) - 86400 )); why="the 24-hour default: no earlier record, no HANDOFF.md, no --since"
  fi
  local selfdecl=0
  while read -r ct H; do
    [ -n "$H" ] || continue
    if [ -n "$bound" ] && [ "$ct" -lt "$bound" ]; then older=$((older+1)); continue; fi
    # a commit that already declares its decision (ADR: <n> trailer, manually authored
    # or enforcer-prompted) is linked by backfill to ITS OWN ADR, blanket-linking it
    # to every newly harvested ADR would pollute both sides.
    if git log -1 --format='%(trailers:key=ADR,valueonly)' "$H" 2>/dev/null | grep -qE '[0-9]'; then
      selfdecl=$((selfdecl+1)); continue
    fi
    if _is_work "$H" "$adrrel"; then
      if [ "$dry" = 1 ]; then printf '  would link %s  %s\n' "$(git rev-parse --short "$H")" "$(git log -1 --format='%s' "$H" | cut -c1-70)"
        linked=$((linked+1))
      elif ( cmd_link "$H" "$@" ) >/dev/null 2>&1; then linked=$((linked+1))
      else err "link-session: could not link $(git rev-parse --short "$H")"; fi
    else skipped=$((skipped+1)); fi
  done < <(git rev-list --no-merges --timestamp "$range" 2>/dev/null)
  err "link-session: $( [ "$dry" = 1 ] && echo 'would link' || echo linked) $linked work-commit(s) over '$range'$( [ -n "$bound" ] && echo " since $(_when "$bound")") -> $(for n in "$@"; do printf 'ADR-%04d ' "$n"; done); skipped $skipped doc/maintenance + $selfdecl self-declared (own ADR: trailer) commit(s)."
  if [ "$older" -gt 0 ]; then
    err "link-session: left out $older older commit(s), from before $(_when "$bound") ($why). To include them, re-run with --all --since <earlier date>, or --all alone to drop the time bound."
    # the brief can be written during the session too (an interrupted /handoff notes
    # its drafts there; a hand edit), and then it hides the session's earlier commits
    if [ -n "$since" ] && [ "$why" = "HANDOFF.md last written" ]; then
      err "link-session: HANDOFF.md was written after --since $since. If this session wrote it, re-run with --all --since \"$since\" to link the session's earlier commits."
    fi
  fi
}

# ---- subcommand: state (what /handoff reads to pick its mode) ----------------------------
# Read-only. One place that classifies the repo so neither the commit hook nor
# /handoff needs a flag and the user never picks forward/retro or thinks "linkage".
# Emits parseable key=value lines on stdout + a human summary on stderr.
cmd_state() {
  local root; root=$(repo_root) || {
    # Not a git repo, but it may still HOLD decisions (e.g. a synced vault like
    # ~/Projects). Linkage is git-native (it links ADRs to commits), so it can't
    # apply here, but say so honestly instead of "no decisions".
    local d2 n=0 f
    d2=$(_resolve_adr_dir "$PWD")
    for f in "$d2"/[0-9][0-9][0-9][0-9]-*.md; do [ -e "$f" ] || continue; case "$f" in */0000-*) ;; *) n=$((n+1));; esac; done
    if [ "$n" -gt 0 ]; then echo "decision_repo=no_git"; err "$n ADRs here, but this isn't a git repo → linkage doesn't apply (no commits to link to)."
    else echo "decision_repo=no"; err "not a git repo and no ADRs: nothing to do."; fi
    return 0
  }
  cd "$root"; local d; d=$(adr_dir)
  if [ ! -d "$d" ]; then
    # Git repo, no ADR dir yet = GREENFIELD, NOT "nothing to do". /handoff can still
    # harvest this session's decisions and create the dir (1a). Compute dirty_work so
    # step 0.5 (commit code before the ADR) engages on the very first handoff too.
    local adrrel0 gdirty=0 line p
    adrrel0=${d#"$root"/}
    while IFS= read -r line; do
      [ -n "$line" ] || continue; p=${line:3}
      case "$p" in HANDOFF.md|CLAUDE.md) continue ;; "$adrrel0"/*) continue ;; esac
      gdirty=$((gdirty+1))
    done < <(git status --porcelain 2>/dev/null)
    # Greenfield still has a backlog if real history exists (more than an initial
    # commit) OR a substantial decision-doc does, both mean undocumented decisions.
    local gpre=no
    { [ "$(git rev-list --count HEAD 2>/dev/null || echo 0)" -ge 2 ] || _has_decision_docs "$root"; } && gpre=yes
    printf 'decision_repo=greenfield\nadrs=0\nenforcer=%s\nretro_backlog=%s\nretro_checkpoint=none\ndirty_work=%s\n' \
      "$( [ -x "$root/.git/hooks/commit-msg" ] && echo local || { ghp=$(git config --get core.hooksPath 2>/dev/null||true); [ -n "$ghp" ] && [ -e "$ghp/commit-msg" ] && echo global-gentle || echo off; } )" "$gpre" "$gdirty"
    err "greenfield, no ADRs yet; /handoff can create the first ones$([ "$gpre" = yes ] && echo ' (and reconstruct undocumented ones)'). dirty implementation files: $gdirty"
    return 0
  fi
  local adrrel nadr enforcer pairs total unlinked covered uncovered pre f n4 H has
  adrrel=${d#"$root"/}
  nadr=0; for f in "$d"/[0-9][0-9][0-9][0-9]-*.md; do [ -e "$f" ] || continue; case "$f" in */0000-*) ;; *) nadr=$((nadr+1));; esac; done
  # enforcer: local repo hook (strict, opt-in) > global gentle hook > none.
  local ghp; ghp=$(git config --get core.hooksPath 2>/dev/null || true)
  if [ -x "$root/.git/hooks/commit-msg" ]; then enforcer=local
  elif [ -n "$ghp" ] && [ -e "$ghp/commit-msg" ]; then enforcer=global-gentle
  else enforcer=off; fi
  pairs=$(_pairs | sort -u)
  total=0; unlinked=0
  while IFS= read -r H; do
    _is_work "$H" "$adrrel" || continue
    total=$((total+1))
    git notes --ref="$NOTES_REF" show "$H" >/dev/null 2>&1 && continue
    [ -n "$(git show -s --format='%(trailers:key=ADR,valueonly)' "$H" 2>/dev/null)" ] && continue
    unlinked=$((unlinked+1))
  done < <(git rev-list --no-merges HEAD 2>/dev/null)
  covered=0
  for f in "$d"/[0-9][0-9][0-9][0-9]-*.md; do
    [ -e "$f" ] || continue; n4=$(basename "$f" | grep -oE '^[0-9]{4}'); [ "$n4" = 0000 ] && continue
    has=0
    while IFS= read -r H; do [ -z "$H" ] && continue; [ "$(_kind_for "$H" "$n4")" = implements ] && { has=1; break; }; done \
      < <(printf '%s\n' "$pairs" | awk -F'\t' -v n="$n4" '$2==n{print $1}')
    [ "$has" = 1 ] && covered=$((covered+1))
  done
  uncovered=$((nadr - covered))
  # retro backlog: a ONE-TIME catch-up on decisions made before Organism, NOT an ongoing state. The raw
  # unlinked-ratio is a noisy proxy (most commits aren't decisions, so it stays high
  # forever). Once the user dispositions it, a `.retro-checkpoint` marker exists
  # pointing at a real commit, stay quiet: new decisions are forward-harvested, not
  # retro. `/handoff --retro` forces a re-scan; `retro-checkpoint clear` re-enables.
  local cp="$d/.retro-checkpoint" cpsha="" pre=no
  [ -f "$cp" ] && cpsha=$(grep -oE '[0-9a-f]{7,40}' "$cp" 2>/dev/null | head -1 || true)
  if [ -n "$cpsha" ] && git rev-parse -q --verify "${cpsha}^{commit}" >/dev/null 2>&1; then
    pre=no
  else
    # backlog if: lots of unlinked git history, OR few ADRs but a rich decision-doc
    # (the shallow-git/rich-docs case, e.g. a project git-tracked long after it began).
    cpsha=""
    { { [ "$total" -gt 5 ] && [ "$unlinked" -gt $((total/2)) ]; } || { [ "$nadr" -le 2 ] && _has_decision_docs "$root"; }; } && pre=yes
  fi
  # dirty_work = uncommitted files that are NOT the ADR layer or HANDOFF.md, i.e.
  # implementation that must be committed BEFORE the ADR (else the why precedes the what).
  local dirty=0 line p
  while IFS= read -r line; do
    [ -n "$line" ] || continue; p=${line:3}
    case "$p" in HANDOFF.md|CLAUDE.md) continue ;; "$adrrel"/*) continue ;; esac
    dirty=$((dirty+1))
  done < <(git status --porcelain 2>/dev/null)
  printf 'decision_repo=yes\nadrs=%s\nenforcer=%s\nwork_commits=%s\nunlinked=%s\ncovered=%s\nuncovered=%s\nretro_backlog=%s\nretro_checkpoint=%s\ndirty_work=%s\n' \
    "$nadr" "$enforcer" "$total" "$unlinked" "$covered" "$uncovered" "$pre" "${cpsha:-none}" "$dirty"
  err "state: $nadr ADRs (enforcer $enforcer) · $unlinked/$total work-commits unlinked → $([ "$unlinked" -gt 0 ] && echo 'catch-up link' || echo 'linked') · coverage $covered/$nadr$([ "$uncovered" -gt 0 ] && echo " ($uncovered decided-but-not-built)") · retro backlog: $pre$([ -n "$cpsha" ] && echo " (checkpointed $(git rev-parse --short "$cpsha" 2>/dev/null))") · dirty implementation files: $dirty"
}

# ---- subcommand: retro-checkpoint (disposition the one-time backlog) ---------
# set   -> mark retro reconciled at HEAD (stages the marker; /handoff commits it).
# clear -> remove the marker (re-enable retro offers).
# show  -> print current checkpoint.
cmd_retro_checkpoint() {
  local action="${1:-show}"
  local root; root=$(repo_root) || { err "not a git repo"; exit 1; }; cd "$root"
  local d cp; d=$(adr_dir); cp="$d/.retro-checkpoint"
  [ -d "$d" ] || { err "no ADR dir"; exit 1; }
  case "$action" in
    set)
      local sha; sha=$(git rev-parse HEAD 2>/dev/null) || { err "no HEAD yet"; exit 1; }
      { printf '# Retro backlog reconciled up to this commit: /handoff stops offering retro past here.\n'
        printf '# re-scan: /handoff --retro   ·   re-enable offers: adr-link.sh retro-checkpoint clear\n%s\n' "$sha"; } > "$cp"
      git add "$cp" 2>/dev/null || true
      err "retro-checkpoint set at $(git rev-parse --short "$sha") (staged; will commit with the handoff)"
      ;;
    clear) rm -f "$cp"; git add -A "$d" 2>/dev/null || true; err "retro-checkpoint cleared: retro offers re-enabled." ;;
    show|*) if [ -f "$cp" ]; then err "retro-checkpoint at $(grep -oE '[0-9a-f]{7,40}' "$cp" | head -1 || true)"; else err "no retro-checkpoint (offers active)"; fi ;;
  esac
}

# ---- subcommand: ignore-scan (flag generated byproduct in the dirty tree) -----
# Read-only. Lists directories of likely GENERATED files sitting uncommitted, dated
# daily outputs, or known junk dir names (logs/cache/build/...), so /handoff's step
# 0.5 can offer to gitignore them instead of bulk-committing data (the gap that let
# one project accumulate 132 tracked data files). Output: "<dir>\t<count>\t<reason>" per line;
# empty output = nothing suspicious. Conservative: only strong signals, to avoid
# flagging real source dirs.
cmd_ignore_scan() {
  local root; root=$(repo_root) || return 0; cd "$root"
  # -uall: list each untracked FILE, not the collapsed dir, so per-file dating works.
  git status --porcelain --untracked-files=all 2>/dev/null | sed 's/^...//; s/^.* -> //' | while IFS= read -r p; do
    [ -n "$p" ] || continue
    case "$p" in HANDOFF.md|CLAUDE.md|.gitignore) continue ;; esac
    dated=0; printf '%s' "$(basename "$p")" | grep -qE '[0-9]{4}-[0-9]{2}-[0-9]{2}' && dated=1
    printf '%s\t%s\n' "$(dirname "$p")" "$dated"
  done | awk -F'\t' '
    { total[$1]++; if ($2==1) dated[$1]++ }
    END {
      for (d in total) {
        r=""
        if (d ~ /(^|\/)(logs?|tmp|temp|cache|\.cache|dist|build|out|output|coverage|__pycache__|\.pytest_cache|node_modules)$/) r="junk-dir-name"
        else if (dated[d] >= 3) r="dated-output (" dated[d] "/" total[d] " dated)"
        if (r != "") printf "%s\t%d\t%s\n", d, total[d], r
      }
    }' | sort
}

case "${1:-}" in
  commit-msg)       shift; cmd_commit_msg "$@" ;;
  backfill)         cmd_backfill ;;
  backlinks)        shift; cmd_backlinks "${1:-}" ;;
  link)             shift; cmd_link "$@" ;;
  link-session)     shift; cmd_link_session "$@" ;;
  state)            cmd_state ;;
  retro-checkpoint) shift; cmd_retro_checkpoint "$@" ;;
  ignore-scan)      cmd_ignore_scan ;;
  query)            shift; cmd_query "$@" ;;
  *) err "usage: adr-link.sh {state|commit-msg <file>|backfill|backlinks [write]|link <commit> <N...>|link-session [--dry] [--since <date>] [--all] <N...>|retro-checkpoint [set|clear|show]|ignore-scan|query <N|hash>}"; exit 2 ;;
esac
