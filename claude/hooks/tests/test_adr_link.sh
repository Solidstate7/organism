#!/bin/bash
# tests/test_adr_link.sh: which commits link-session joins to a new decision record,
# and where every subcommand looks for the records.
# Work commits since the last `docs(adr):` commit are linked, a repo's FIRST commit
# included; /handoff's own housekeeping (`chore: keep HANDOFF.md out of git`,
# `chore: stop tracking generated <dir>`) and the record commit itself are not.
# A time bound keeps the links within the session: the later of --since and HANDOFF.md's
# last write, else 24 hours when there is no earlier record; older commits are counted,
# never linked, and --all lifts the bound. Options go before the ADR numbers, and a
# number with no record is reported, not linked. The ADR dir resolves like /handoff
# step 1a: .adr-dir, a log4brains config, the usual folder names in order, docs/adr.
# Self-contained under mktemp; never touches a real repo.
# Run:  bash claude/hooks/tests/test_adr_link.sh
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
AL="$HERE/../adr-link.sh"
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT
FAILS=0
say() { printf '%s\n' "$1"; }
check() { if [ "$2" = 1 ]; then say "  ✓ $1"; else say "  ✗ $1"; FAILS=$((FAILS+1)); fi; }

R="$T/repo"; mkdir -p "$R"; cd "$R" || exit 1
git init -q; git config user.email t@test.invalid; git config user.name t
c() { git add -A; git commit -q -m "$1" -m "ADR: none"; git rev-parse --short HEAD; }
echo app > app.py;                          ROOT=$(c "feat: first version of the app")
echo HANDOFF.md > .gitignore;               CHORE=$(c "chore: keep HANDOFF.md out of git")
mkdir logs; echo x > logs/2026-10-01.log; git add -A; git commit -q -m "wip: logs slipped in" -m "ADR: none"; SLIP=$(git rev-parse --short HEAD)
echo logs/ >> .gitignore; git rm -r -q --cached logs; GEN=$(c "chore: stop tracking generated logs")
echo more >> app.py;                        WORK=$(c "feat: the change the decision is about")
mkdir -p docs/adr; printf '# 0001. Keep it simple\n' > docs/adr/0001-keep-it-simple.md
printf '# Decisions\n- 0001\n' > CLAUDE.md; REC=$(c "docs(adr): record 0001")

say "link-session after a first /handoff"
bash "$AL" link-session 1 2>/dev/null
q=$(bash "$AL" query 1 2>/dev/null)
has() { printf '%s\n' "$q" | grep -q "$1"; }
check "the repo's first commit is linked" "$( has "$ROOT" && echo 1 || echo 0)"
check "the work commit is linked"                         "$( has "$WORK" && echo 1 || echo 0)"
check "any other commit is work by its files, even one that only added data" "$( has "$SLIP" && echo 1 || echo 0)"
check "'chore: keep HANDOFF.md out of git' is not"        "$( has "$CHORE" && echo 0 || echo 1)"
check "'chore: stop tracking generated logs' is not"      "$( has "$GEN" && echo 0 || echo 1)"
check "the record commit is not"                          "$( has "$REC" && echo 0 || echo 1)"
k=$(bash "$AL" query "$ROOT" 2>/dev/null | grep -c 'ADR-0001')
check "the first commit answers 'which decision?'"        "$( [ "$k" -ge 1 ] && echo 1 || echo 0)"

say "state counts the same commits as work"
st=$(bash "$AL" state 2>/dev/null)
check "work_commits=3 (first, slipped, change)"           "$(printf '%s\n' "$st" | grep -qx 'work_commits=3' && echo 1 || echo 0)"
check "unlinked=0 after the run"                          "$(printf '%s\n' "$st" | grep -qx 'unlinked=0' && echo 1 || echo 0)"

NOW=$(date +%s)
DAY=86400
# commit everything staged, dated $1 seconds ago (author and committer alike)
ca() { git add -A; GIT_AUTHOR_DATE="$((NOW - $1)) +0000" GIT_COMMITTER_DATE="$((NOW - $1)) +0000" \
         git commit -q -m "$2" -m "ADR: none"; git rev-parse --short HEAD; }
# set a file's last-modified time to $2 seconds ago
age() { python3 -c 'import os,sys,time; t=time.time()-int(sys.argv[2]); os.utime(sys.argv[1],(t,t))' "$1" "$2"; }
inq() { printf '%s\n' "$1" | grep -q -e "$2"; }
fresh() { mkdir -p "$1"; cd "$1" || exit 1; git init -q; git config user.email t@test.invalid; git config user.name t; }

say "first /handoff in a repo with years of history"
fresh "$T/old"
echo a > a.py;  OLD1=$(ca $((3*365*DAY)) "feat: the original app")
echo b >> a.py; OLD2=$(ca $((400*DAY))   "feat: a feature from last year")
echo c >> a.py; OLD3=$(ca $((30*DAY))    "fix: last month's fix")
echo d >> a.py; NEW1=$(ca $((2*3600))    "feat: this session's first change")
echo e >> a.py; NEW2=$(ca 60             "feat: this session's second change")
mkdir -p docs/adr; printf '# 0001. A new decision\n' > docs/adr/0001-new.md; ca 0 "docs(adr): record 0001" >/dev/null
msg=$(bash "$AL" link-session 1 2>&1 >/dev/null)
q=$(bash "$AL" query 1 2>/dev/null)
check "this session's commits are linked"                 "$( inq "$q" "$NEW1" && inq "$q" "$NEW2" && echo 1 || echo 0)"
check "a commit from three years ago is not"              "$( inq "$q" "$OLD1" && echo 0 || echo 1)"
check "nor last year's or last month's"                   "$( inq "$q" "$OLD2" || inq "$q" "$OLD3" && echo 0 || echo 1)"
check "the summary counts the 3 older commits"            "$( inq "$msg" 'left out 3 older commit(s)' && echo 1 || echo 0)"
check "it names the 24-hour default as the bound"         "$( inq "$msg" '24-hour default' && echo 1 || echo 0)"
check "it says how to include them"                       "$( inq "$msg" '--all' && echo 1 || echo 0)"

say "--since sets the bound"
d=$(bash "$AL" link-session --dry --since "40 days ago" 1 2>/dev/null)
check "--since reaches past the 24-hour default"          "$( inq "$d" "$OLD3" && inq "$d" "$NEW1" && echo 1 || echo 0)"
check "but not past its own date"                         "$( inq "$d" "$OLD2" || inq "$d" "$OLD1" && echo 0 || echo 1)"
d=$(bash "$AL" link-session --dry --since="1 hour ago" 1 2>/dev/null)
check "--since=<date> narrows to the session"             "$( inq "$d" "$NEW2" && ! inq "$d" "$NEW1" && echo 1 || echo 0)"
msg=$(bash "$AL" link-session --dry --since "1 hour ago" 1 2>&1 >/dev/null)
check "the skipped count follows the bound"               "$( inq "$msg" 'left out 4 older commit(s)' && echo 1 || echo 0)"
d=$(bash "$AL" link-session --dry --all 1 2>/dev/null)
check "--all lifts the time bound"                        "$( inq "$d" "$OLD1" && inq "$d" "$NEW2" && echo 1 || echo 0)"
bash "$AL" link-session --dry --since "" 1 >/dev/null 2>&1; rc=$?
check "--since with an empty date is refused"             "$( [ "$rc" = 2 ] && echo 1 || echo 0)"
bash "$AL" link-session --dry --since= 1 >/dev/null 2>&1; rc=$?
check "--since= with an empty date is refused"            "$( [ "$rc" = 2 ] && echo 1 || echo 0)"
bash "$AL" link-session --since "not a date" 1 2>/dev/null; rc=$?
check "--since that git reads as 'now' is refused"        "$( [ "$rc" = 2 ] && echo 1 || echo 0)"

say "options go before the ADR numbers"
notes() { git notes --ref=adr list 2>/dev/null | wc -l | tr -d ' '; }
git update-ref -d refs/notes/adr; n0=$(notes)
bash "$AL" link-session 1 --dry >/dev/null 2>&1; rc=$?
check "'1 --dry' is refused, not read as ADR '--dry'"     "$( [ "$rc" = 2 ] && echo 1 || echo 0)"
check "and writes no notes"                               "$( [ "$(notes)" = "$n0" ] && echo 1 || echo 0)"
bash "$AL" link-session --dry 1 --since "2 hours ago" >/dev/null 2>&1; rc=$?
check "'--dry 1 --since <date>' is refused too"           "$( [ "$rc" = 2 ] && echo 1 || echo 0)"

say "an ADR number with no record file"
msg=$(bash "$AL" link-session 1 99 2>&1 >/dev/null); rc=$?
q=$(bash "$AL" query 1 2>/dev/null)
check "the other number is still linked, summary printed" "$( [ "$rc" = 0 ] && inq "$q" "$NEW2" && inq "$msg" 'linked 2 work-commit' && echo 1 || echo 0)"
check "the missing one is named, not hidden"              "$( inq "$msg" 'ADR-0099 has no file' && echo 1 || echo 0)"
msg=$(bash "$AL" link-session 99 2>&1 >/dev/null); rc=$?
check "with no valid number it fails and says so"         "$( [ "$rc" = 1 ] && inq "$msg" 'nothing linked' && echo 1 || echo 0)"

say "a later /handoff: the previous brief bounds the range"
fresh "$T/later"
echo a > a.py; BEFORE=$(ca $((5*DAY)) "feat: work the first record covers")
mkdir -p docs/adr; printf '# 0001\n' > docs/adr/0001-first.md; ca $((5*DAY)) "docs(adr): record 0001" >/dev/null
echo b >> a.py; GAP=$(ca $((3*DAY)) "feat: committed before the last brief")
echo HANDOFF > HANDOFF.md; echo HANDOFF.md > .gitignore; age HANDOFF.md $((2*DAY))
echo c >> a.py; SESS=$(ca 3600 "feat: this session")
printf '# 0002\n' > docs/adr/0002-second.md; ca 0 "docs(adr): record 0002" >/dev/null
msg=$(bash "$AL" link-session 2 2>&1 >/dev/null)
q=$(bash "$AL" query 2 2>/dev/null)
check "the session's commit is linked"                    "$( inq "$q" "$SESS" && echo 1 || echo 0)"
check "a commit older than HANDOFF.md is not"             "$( inq "$q" "$GAP" && echo 0 || echo 1)"
check "the summary names HANDOFF.md as the bound"         "$( inq "$msg" 'left out 1 older commit(s)' && inq "$msg" 'HANDOFF.md' && echo 1 || echo 0)"
d=$(bash "$AL" link-session --dry --since "4 days ago" 2 2>/dev/null)
check "an earlier --since does not override HANDOFF.md"   "$( inq "$d" "$GAP" && echo 0 || echo 1)"
d=$(bash "$AL" link-session --dry --all 2 2>/dev/null)
check "--all drops HANDOFF.md but keeps the previous record" "$( inq "$d" "$GAP" && ! inq "$d" "$BEFORE" && echo 1 || echo 0)"
mv HANDOFF.md "$T/brief.md"
d=$(bash "$AL" link-session --dry 2 2>/dev/null)
check "no brief, no --since, an earlier record: no 24-hour default" "$( inq "$d" "$GAP" && ! inq "$d" "$BEFORE" && echo 1 || echo 0)"
mv "$T/brief.md" HANDOFF.md

say "a brief written during the session hides its earlier commits"
fresh "$T/midway"
echo a > a.py; ca $((10*DAY)) "feat: old" >/dev/null
mkdir -p docs/adr; printf '# 0001\n' > docs/adr/0001-x.md; ca $((10*DAY)) "docs(adr): record 0001" >/dev/null
echo b >> a.py; S1=$(ca $((3*3600)) "feat: session work 1")
echo c >> a.py; S2=$(ca $((2*3600)) "feat: session work 2")
echo brief > HANDOFF.md; age HANDOFF.md 3600
printf '# 0002\n' > docs/adr/0002-y.md; ca 0 "docs(adr): record 0002" >/dev/null
START=$(t=$((NOW - 4*3600)); date -r "$t" '+%Y-%m-%d %H:%M' 2>/dev/null || date -d "@$t" '+%Y-%m-%d %H:%M')
msg=$(bash "$AL" link-session --dry --since "$START" 2 2>&1 >/dev/null)
check "the bound is the brief's write, as specified"       "$( inq "$msg" 'would link 0 work-commit' && echo 1 || echo 0)"
check "the summary says how to reach back to --since"      "$( inq "$msg" "re-run with --all --since \"$START\"" && echo 1 || echo 0)"
d=$(bash "$AL" link-session --dry --all --since "$START" 2 2>/dev/null)
check "and that re-run links the session's commits"        "$( inq "$d" "$S1" && inq "$d" "$S2" && echo 1 || echo 0)"
msg=$(bash "$AL" link-session --dry 2 2>&1 >/dev/null)
check "without --since there is no such hint"              "$( inq "$msg" 'written after --since' && echo 0 || echo 1)"

say "the ADR dir resolves like /handoff step 1a"
fresh "$T/decisions"
echo a > a.py; W=$(ca 60 "feat: work")
mkdir -p docs/decisions; printf '# 0001\n' > docs/decisions/0001-x.md; R=$(ca 0 "docs(adr): record 0001")
bash "$AL" link-session 1 2>/dev/null
q=$(bash "$AL" query 1 2>/dev/null)
check "docs/decisions: the work commit is linked"         "$( inq "$q" "$W" && echo 1 || echo 0)"
check "docs/decisions: its record commit is not work"     "$( inq "$q" "$R" && echo 0 || echo 1)"
check "docs/decisions: state counts the record"           "$(bash "$AL" state 2>/dev/null | grep -qx 'adrs=1' && echo 1 || echo 0)"

fresh "$T/l4b"
mkdir -p docs/adr records packages/web/adr
printf '# 0001\n' > docs/adr/0001-decoy.md
printf '# 0001\n' > records/0001-from-config.md
printf '# 0001\n' > packages/web/adr/0001-package.md
cat > .log4brains.yml <<'YML'
---
project:
  name: demo
  tz: UTC
  packages:
    - name: web
      path: ./packages/web
      adrFolder: ./packages/web/adr
  adrFolder: ./records  # the project's own records
YML
echo a > a.py; ca 0 "feat: work" >/dev/null
bash "$AL" link HEAD 1 2>/dev/null
check "a log4brains config's project.adrFolder wins over docs/adr" "$(bash "$AL" query HEAD 2>/dev/null | grep -q '0001-from-config.md' && echo 1 || echo 0)"
mv .log4brains.yml log4brains.yml; sed -i.bak 's#adrFolder: ./records .*#adrFolder: "./records"#' log4brains.yml
check "log4brains.yml without the dot, quoted value"      "$(bash "$AL" query HEAD 2>/dev/null | grep -q '0001-from-config.md' && echo 1 || echo 0)"
sed -i.bak 's#adrFolder: "./records"#adrFolder: .//records/#' log4brains.yml
check "'.//records/' stays under the repo"                "$(bash "$AL" query HEAD 2>/dev/null | grep -q '0001-from-config.md' && echo 1 || echo 0)"
echo docs/adr > .adr-dir
check "a .adr-dir file comes first"                       "$(bash "$AL" query HEAD 2>/dev/null | grep -q '0001-decoy.md' && echo 1 || echo 0)"

fresh "$T/order"
ORDER="docs/adr docs/decisions docs/architecture/decisions doc/adr adr architecture/decisions"
for f in $ORDER; do mkdir -p "$f"; printf '# 0001\n' > "$f/0001-$(printf '%s' "$f" | tr / -).md"; done
echo a > a.py; ca 0 "feat: work" >/dev/null
bash "$AL" link HEAD 1 2>/dev/null
ok=1
for f in $ORDER; do
  bash "$AL" query HEAD 2>/dev/null | grep -q "0001-$(printf '%s' "$f" | tr / -)\.md" || { ok=0; say "    (expected $f)"; }
  rm -rf "$f"
done
check "the folder names are tried in order, docs/adr first" "$ok"

mkdir -p "$T/nogit/docs/decisions"; printf '# 0001\n' > "$T/nogit/docs/decisions/0001-x.md"
st=$(cd "$T/nogit" && GIT_CEILING_DIRECTORIES="$T" bash "$AL" state 2>/dev/null)
check "outside git, state finds docs/decisions too"       "$(printf '%s\n' "$st" | grep -qx 'decision_repo=no_git' && echo 1 || echo 0)"

if [ "$FAILS" -eq 0 ]; then say "ALL PASS"; exit 0; else say "FAILED: $FAILS case(s)"; exit 1; fi
