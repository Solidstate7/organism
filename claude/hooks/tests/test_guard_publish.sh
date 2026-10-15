#!/bin/bash
# tests/test_guard_publish.sh, asserting tests for guard-publish.js.
# Replaces the two manual eyeball probes (gp-probe.sh / gp-probe2.sh): same coverage , 
# publish-class classification, file-content scan (@file/--input), committed-content
# git-grep (spec §5), but every case now ASSERTS its expected exit code and the suite
# exits nonzero on any mismatch; and the guard under test is the REPO copy beside this
# script, never the deployed ~/.claude one (a fresh clone must be testable undeployed).
# Also covers --status, the empty-denylist allow, the
# non-Bash tool-name path, and the guard-publish.sh wrapper passthrough.
# --scan: the record check /handoff runs on ADR files before committing them.
# Also: push classification by git's subcommand (and inside bash -c, eval, $( ), here-docs
# fed to a shell), the newer publish paths (gh posts, docker, cargo, twine, scp/rsync,
# cloud storage copies, firebase/wrangler/fly/surge), web tools, exemptions that stay
# with their own push, fail-closed internal errors, and the wrapper's no-node fast path.
# Self-contained under mktemp; neutral fake terms only (no real
# codename may appear in committed code, tests included). Skips cleanly without node.
# Run:  bash claude/hooks/tests/test_guard_publish.sh
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
G="$HERE/../guard-publish.js"
command -v node >/dev/null 2>&1 || { echo "SKIP: node not available: guard-publish tests not run"; exit 0; }
export GUARD_PUBLISH_NO_ALERT=1   # a test block must never buzz the user's phone

REALNODE=$(command -v node)
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT
export HOME="$T/home"; mkdir -p "$HOME"   # no default path (paths.env, exemptions, legacy list) may reach a live ~/.claude
export GUARD_PUBLISH_LOG="$T/block.log"   # blocks are logged here, never into the live log
FAILS=0
say() { printf '%s\n' "$1"; }
assert_rc() { # name want got
  if [ "$3" -eq "$2" ]; then say "  ✓ $1 (rc=$3)"; else say "  ✗ $1: want rc=$2, got rc=$3"; FAILS=$((FAILS+1)); fi
}
run() { # bash-command cwd → prints the hook's exit code
  printf '{"tool_name":"Bash","tool_input":{"command":%s},"cwd":"%s"}' \
    "$(python3 -c 'import json,sys;print(json.dumps(sys.argv[1]))' "$1")" "$2" \
    | node "$G" >/dev/null 2>&1; echo $?
}
runjson() { printf '%s' "$1" | node "$G" >/dev/null 2>&1; echo $?; }

# Scratch denylist, same shape as the vault page's CONFIDENTIAL-TERMS block.
DL="$T/denylist.md"
printf '%s\n```\n%s\nwidget-co\nzeta-plan\n```\n%s\n' \
  "<!-- CONFIDENTIAL-TERMS:BEGIN -->" "AcmeCorp" "<!-- CONFIDENTIAL-TERMS:END -->" > "$DL"
export GUARD_PUBLISH_DENYLIST="$DL"

say "--status probe"
ST=$(node "$G" --status)
if [ "$ST" = "armed:3 source:$DL" ]; then say "  ✓ reports armed count + source"; else say "  ✗ --status: got '$ST'"; FAILS=$((FAILS+1)); fi

# fixtures: a repo with the term COMMITTED, a clean repo, files with/without the term
REPO="$T/repo"; mkdir -p "$REPO"; git -C "$REPO" init -q
printf 'notes about AcmeCorp app\n' > "$REPO/note.md"
git -C "$REPO" add -A; git -C "$REPO" -c user.email=t@test.invalid -c user.name=t commit -qm x
CLEAN="$T/clean"; mkdir -p "$CLEAN"; git -C "$CLEAN" init -q
printf 'nothing secret here\n' > "$CLEAN/a.md"
git -C "$CLEAN" add -A; git -C "$CLEAN" -c user.email=t@test.invalid -c user.name=t commit -qm x
PJ="$T/p.json";    printf '{"issue":"AcmeCorp details"}\n' > "$PJ"
PC="$T/clean.json"; printf '{"issue":"nothing here"}\n' > "$PC"

say "publish-class classification + content scan (Bash surface)"
assert_rc "git push, term in COMMITTED content (spec §5)"    2 "$(run 'git push origin main' "$REPO")"
assert_rc "git -C <path> push classified + scanned"          2 "$(run "git -C $REPO push origin main" "$T")"
assert_rc "git push, clean repo → allow"                     0 "$(run 'git push origin main' "$CLEAN")"

say "per-repo exemptions (guard-exempt.json)"
EX="$T/exempt.json"
printf '{"%s": ["AcmeCorp"]}\n' "$REPO" > "$EX"
assert_rc "exempted repo, its own term → allow"              0 "$(GUARD_EXEMPT_FILE="$EX" run 'git push origin main' "$REPO")"
assert_rc "exemption is per-repo: clean repo unaffected"     0 "$(GUARD_EXEMPT_FILE="$EX" run 'git push origin main' "$CLEAN")"
printf '{"%s": ["OtherTerm"]}\n' "$REPO" > "$EX"
assert_rc "exempting a DIFFERENT term still blocks"          2 "$(GUARD_EXEMPT_FILE="$EX" run 'git push origin main' "$REPO")"
printf 'not json\n' > "$EX"
assert_rc "broken exempt file → strict (still blocks)"       2 "$(GUARD_EXEMPT_FILE="$EX" run 'git push origin main' "$REPO")"
assert_rc "non-push surface ignores exemptions"              2 "$(GUARD_EXEMPT_FILE="$EX" run "gh api --input $PJ /repos/x/y/issues" "$T")"
assert_rc "gh api --input, term IN the file"                 2 "$(run "gh api --input $PJ /repos/x/y/issues" "$T")"
assert_rc "gh api --input, clean file → allow"               0 "$(run "gh api --input $PC /repos/x/y/issues" "$T")"
assert_rc "curl -d@file (no space), term IN the file"        2 "$(run "curl -d@$PJ https://x.com" "$T")"
assert_rc "curl -d @file (spaced @), term IN the file"       2 "$(run "curl -d @$PJ https://x.com" "$T")"
assert_rc "curl -sd@file (clustered), term IN the file"     2 "$(run "curl -sd@$PJ https://x.com" "$T")"
assert_rc "curl -sSd '@file' (clustered, quoted)"            2 "$(run "curl -sSd '@$PJ' https://x.com" "$T")"
assert_rc "curl -d '@file' (quoted)"                         2 "$(run "curl -d '@$PJ' https://x.com" "$T")"
assert_rc "curl -sT file (clustered upload)"                 2 "$(run "curl -sT $PJ https://x.com" "$T")"
assert_rc "curl -sTfile (clustered, attached)"               2 "$(run "curl -sT$PJ https://x.com" "$T")"
assert_rc "curl --data-urlencode name@file"                  2 "$(run "curl --data-urlencode a@$PJ https://x.com" "$T")"
assert_rc "wget --post-file=file"                            2 "$(run "wget --post-file=$PJ https://x.com" "$T")"
assert_rc "wget --post-file file"                            2 "$(run "wget --post-file $PJ https://x.com" "$T")"
assert_rc "curl -sd@clean-file → allow"                      0 "$(run "curl -sd@$PC https://x.com" "$T")"
assert_rc "term in the command string itself"                2 "$(run 'gh gist create notes-AcmeCorp.md' "$T")"
assert_rc "gh api GET (read) → allow"                        0 "$(run 'gh api /repos/x/y/issues' "$T")"
assert_rc "vercel env pull (read) → allow"                   0 "$(run 'vercel env pull' "$T")"
PKG="$T/pkg"; mkdir -p "$PKG"; printf '{"name":"pkg"}\n' > "$PKG/package.json"
assert_rc "npm publish, clean dir → allow"                   0 "$(run 'npm publish' "$PKG")"
assert_rc "non-publish command → silent allow"               0 "$(run 'ls -la' "$T")"

say "what a push sends: every new commit, files and messages"
C() { git -C "$1" -c user.email=t@test.invalid -c user.name=t commit -q "${@:2}"; }
HIST="$T/hist"; mkdir -p "$HIST"; git -C "$HIST" init -q -b main   # -b: Linux git still defaults to master
printf 'AcmeCorp launch notes\n' > "$HIST/n.md"; git -C "$HIST" add -A; C "$HIST" -m add
printf 'launch notes\n' > "$HIST/n.md"; C "$HIST" -am scrub
assert_rc "term only in an OLDER commit (gone at HEAD) → block"      2 "$(run 'git push origin main' "$HIST")"
MSG="$T/msg"; mkdir -p "$MSG"; git -C "$MSG" init -q -b main
printf 'plain\n' > "$MSG/a.md"; git -C "$MSG" add -A; C "$MSG" -m "start the zeta-plan rollout"
assert_rc "term only in a commit MESSAGE → block"                    2 "$(run 'git push' "$MSG")"
assert_rc "cd <repo> && git push scans that repo, not the session's" 2 "$(run "cd $MSG && git push" "$T")"
assert_rc "push --delete sends no commits → allow"                   0 "$(run 'git push origin --delete old' "$HIST")"
# history the remote already has is not sent again; a NEW remote gets all of it
PUSHED="$T/pushed"; git init -q --bare -b main "$T/pushed.git"; git clone -q "$HIST" "$PUSHED" 2>/dev/null
git -C "$PUSHED" remote set-url origin "$T/pushed.git"; git -C "$PUSHED" push -q origin HEAD:refs/heads/main 2>/dev/null
git -C "$PUSHED" fetch -q origin 2>/dev/null; git -C "$PUSHED" branch -q -u origin/main 2>/dev/null
printf 'more\n' >> "$PUSHED/n.md"; C "$PUSHED" -am more
assert_rc "old term already on that remote, new commit clean → allow" 0 "$(run 'git push' "$PUSHED")"
REMOVE="$T/remove"; git clone -q "$T/pushed.git" "$REMOVE" 2>/dev/null
git -C "$REMOVE" reset -q --hard HEAD~1 2>/dev/null; git -C "$REMOVE" push -q -f origin HEAD:refs/heads/main 2>/dev/null
git -C "$REMOVE" fetch -q origin 2>/dev/null; git -C "$REMOVE" branch -q -u origin/main 2>/dev/null
printf 'launch notes\n' > "$REMOVE/n.md"; C "$REMOVE" -am "drop the name"
assert_rc "a commit that REMOVES a term the remote has → allow"       0 "$(run 'git push' "$REMOVE")"
git init -q --bare -b main "$T/backup.git"; git -C "$PUSHED" remote add backup "$T/backup.git"
assert_rc "same branch to a NEW remote (whole history goes) → block" 2 "$(run 'git push backup HEAD' "$PUSHED")"
assert_rc "gh repo create --push sends the history → block"          2 "$(run 'gh repo create x --private --source=. --push' "$HIST")"
assert_rc "a scan that can't finish in time → block"                 2 "$(GUARD_PUBLISH_BUDGET_MS=1 run 'git push' "$CLEAN")"
if grep -q 'BLOCK-INCOMPLETE' "$GUARD_PUBLISH_LOG" 2>/dev/null; then say "  ✓ an unfinished scan is logged as such"; else say "  ✗ unfinished scan not logged"; FAILS=$((FAILS+1)); fi

say "what a deploy or upload sends: the folder or the files"
SITE="$T/site"; mkdir -p "$SITE/dist" "$SITE/node_modules/x"; printf '<h1>AcmeCorp</h1>\n' > "$SITE/dist/index.html"
printf 'AcmeCorp\n' > "$SITE/node_modules/x/readme.md"
OKSITE="$T/oksite"; mkdir -p "$OKSITE/node_modules/x"; printf '<h1>hello</h1>\n' > "$OKSITE/index.html"
printf 'AcmeCorp\n' > "$OKSITE/node_modules/x/readme.md"
assert_rc "vercel --prod, term in the folder → block"                2 "$(run 'vercel --prod' "$SITE")"
assert_rc "vercel --prod, clean folder (node_modules skipped) → allow" 0 "$(run 'npx vercel --prod' "$OKSITE")"
assert_rc "cd <site> && vercel deploy → that folder is read"         2 "$(run "cd $SITE && vercel deploy" "$T/pkg")"
assert_rc "netlify deploy --dir=dist, term in dist → block"          2 "$(run 'netlify deploy --prod --dir=dist' "$SITE")"
assert_rc "npm publish, term in the package → block"                 2 "$(run 'npm publish' "$SITE")"
assert_rc "gh release upload, term in the asset → block"             2 "$(run "gh release upload v1 $PJ" "$T/pkg")"
assert_rc "gh gist create <file>, term in the file → block"          2 "$(run "gh gist create $PJ" "$T/pkg")"
assert_rc "installing the vercel CLI is not a deploy → allow"        0 "$(run 'npm i -g vercel' "$SITE")"

say "file names count, not only contents"
NAMES="$T/names"; mkdir -p "$NAMES"; git -C "$NAMES" init -q
printf 'ok\n' > "$NAMES/AcmeCorp-notes.txt"
git -C "$NAMES" add -A; git -C "$NAMES" -c user.email=t@test.invalid -c user.name=t commit -qm x
assert_rc "term only in a tracked file NAME → block"                 2 "$(run 'git push' "$NAMES")"
OLDNAME="$T/oldname"; mkdir -p "$OLDNAME"; git -C "$OLDNAME" init -q
printf 'ok\n' > "$OLDNAME/acmecorp.txt"
git -C "$OLDNAME" add -A; git -C "$OLDNAME" -c user.email=t@test.invalid -c user.name=t commit -qm x
git -C "$OLDNAME" mv acmecorp.txt plain.txt; git -C "$OLDNAME" -c user.email=t@test.invalid -c user.name=t commit -qm y
assert_rc "term only in an OLDER commit's file name → block"          2 "$(run 'git push' "$OLDNAME")"
NSITE="$T/nsite"; mkdir -p "$NSITE/site/AcmeCorp-dir" "$NSITE/ok/node_modules/AcmeCorp"
printf 'ok\n' > "$NSITE/site/AcmeCorp-dir/index.html"; printf 'ok\n' > "$NSITE/ok/index.html"
assert_rc "netlify deploy --dir site, term in a folder name → block" 2 "$(run 'netlify deploy --dir site' "$NSITE")"
mkdir -p "$NSITE/site2"; printf 'ok\n' > "$NSITE/site2/acmecorp.html"
assert_rc "netlify deploy --dir site2, term in a file name → block"   2 "$(run 'netlify deploy --dir site2' "$NSITE")"
assert_rc "deploy, term only in a skipped folder's name → allow"     0 "$(run 'netlify deploy --dir ok' "$NSITE")"

say "a non-ASCII term matches in any case, whatever the caller's locale"
DLU="$T/denylist-u.md"
printf '%s\n```\n%s\n```\n%s\n' "<!-- CONFIDENTIAL-TERMS:BEGIN -->" "école" "<!-- CONFIDENTIAL-TERMS:END -->" > "$DLU"
UNI="$T/uni"; mkdir -p "$UNI"; git -C "$UNI" init -q
printf 'LA GRANDE ÉCOLE\n' > "$UNI/a.md"
git -C "$UNI" add -A; git -C "$UNI" -c user.email=t@test.invalid -c user.name=t commit -qm x
assert_rc "term école, content ÉCOLE, LC_ALL=C → block"               2 "$(LC_ALL=C GUARD_PUBLISH_DENYLIST="$DLU" run 'git push' "$UNI")"
UNAME="$T/uname"; mkdir -p "$UNAME"; git -C "$UNAME" init -q
printf 'ok\n' > "$UNAME/École notes.txt"
git -C "$UNAME" add -A; git -C "$UNAME" -c user.email=t@test.invalid -c user.name=t commit -qm x
assert_rc "term école, tracked file named École → block"             2 "$(GUARD_PUBLISH_DENYLIST="$DLU" run 'git push' "$UNAME")"
UOLD="$T/uold"; mkdir -p "$UOLD"; git -C "$UOLD" init -q
printf 'ok\n' > "$UOLD/école.txt"
git -C "$UOLD" add -A; git -C "$UOLD" -c user.email=t@test.invalid -c user.name=t commit -qm x
git -C "$UOLD" mv école.txt plain.txt; git -C "$UOLD" -c user.email=t@test.invalid -c user.name=t commit -qm y
assert_rc "term école, only an OLDER commit's file name → block"      2 "$(GUARD_PUBLISH_DENYLIST="$DLU" run 'git push' "$UOLD")"

say "git push is classified by git's subcommand, not by the word push"
assert_rc "git stash push is not a push"                             0 "$(run 'git stash push -m wip' "$REPO")"
assert_rc 'git commit -m "push it" is not a push'                    0 "$(run 'git commit -m "push it"' "$REPO")"
assert_rc 'echo "git push" is not a push'                            0 "$(run 'echo "git push"' "$REPO")"
assert_rc "a # comment saying git push is not a push"                0 "$(run 'ls # then git push' "$REPO")"
assert_rc "a here-doc body saying git push is not a push"            0 "$(run "$(printf "cat <<'EOF' > /dev/null\ngit push\nEOF")" "$REPO")"
assert_rc "echo git push (unquoted) is not a push"                   0 "$(run 'echo git push' "$REPO")"
assert_rc "a for-loop word list is not a push"                       0 "$(run 'for w in git push; do echo $w; done' "$REPO")"
assert_rc "git push behind an unknown runner → scanned"              2 "$(run 'stdbuf -oL git push origin main' "$REPO")"
assert_rc "bash -c behind an unknown runner → scanned"               2 "$(run "flock /tmp/x.lock bash -c 'git -C $REPO push'" "$T")"
assert_rc "git --no-pager push → scanned"                            2 "$(run 'git --no-pager push origin main' "$REPO")"
assert_rc "git -c k=v push → scanned"                                2 "$(run 'git -c push.default=current push' "$REPO")"
assert_rc "git -P --namespace <ns> push → scanned"                   2 "$(run 'git -P --namespace ns push' "$REPO")"
assert_rc "git --git-dir= --work-tree <dir> push reads that repo"    2 "$(run "git --git-dir=$REPO/.git --work-tree $REPO push" "$T")"
assert_rc "git -C a -C b push: each -C relative to the one before"   2 "$(run "git -C $T -C repo push" "$CLEAN")"
assert_rc 'bash -c "cd <repo> && git push" reads that repo'          2 "$(run "bash -c \"cd $REPO && git push\"" "$T")"
assert_rc "sh -c 'git push' → scanned"                               2 "$(run "sh -c 'git push origin main'" "$REPO")"
assert_rc 'eval "git push" → scanned'                                2 "$(run 'eval "git push origin main"' "$REPO")"
assert_rc 'git push inside $( ) → scanned'                           2 "$(run "out=\$(git -C $REPO push 2>&1)" "$T")"
assert_rc "a here-doc fed to bash → scanned"                         2 "$(run "$(printf "bash <<'EOF'\ncd $REPO && git push\nEOF")" "$T")"
assert_rc 'echo "git push" | sh → scanned'                           2 "$(run "echo \"git -C $REPO push\" | sh" "$T")"
assert_rc "a cd inside ( ) stays inside"                             2 "$(run "(cd $CLEAN && ls) && git push" "$REPO")"
assert_rc 'cd "$(git rev-parse ...)" && git push reads the repo'     2 "$(run 'cd "$(git rev-parse --show-toplevel)" && git push' "$REPO")"

say "more publish paths: posts, images, packages, copies, deploys"
BODY="$T/body.md"; printf 'Fixes for AcmeCorp\n' > "$BODY"
OKBODY="$T/okbody.md"; printf 'All fine\n' > "$OKBODY"
assert_rc "gh issue create, term in the title → block"               2 "$(run 'gh issue create --title "AcmeCorp crash" --body x' "$T")"
assert_rc "gh issue comment --body-file, term in the file → block"   2 "$(run "gh issue comment 12 --body-file $BODY" "$T")"
assert_rc "gh pr review -F, term in the file → block"                2 "$(run "gh pr review 3 --comment -F $BODY" "$T")"
assert_rc "gh pr edit, term in the body → block"                     2 "$(run 'gh pr edit 3 --body "zeta-plan notes"' "$T")"
assert_rc "gh issue create --body \"\$(cat FILE)\", term in the file → block" 2 "$(run "gh issue create --body \"\$(cat $BODY)\"" "$T")"
assert_rc "curl -d \"\$(< FILE)\", term in the file → block"         2 "$(run "curl -d \"\$(< $BODY)\" https://x.example" "$T")"
assert_rc "gh pr create --body \"\`cat FILE\`\", term in the file → block" 2 "$(run "gh pr create --body \"\`cat $BODY\`\"" "$T")"
assert_rc "gh issue create --body \"\$(cat FILE)\", clean file → allow" 0 "$(run "gh issue create --body \"\$(cat $OKBODY)\"" "$T")"
assert_rc "gh pr comment -F, clean file → allow"                     0 "$(run "gh pr comment 3 -F $OKBODY" "$T")"
assert_rc "docker push, term in the image name → block"              2 "$(run 'docker push registry.example/acmecorp:1.0' "$T")"
CRATE="$T/crate"; mkdir -p "$CRATE/src" "$CRATE/target/debug"
printf '[package]\nname = "crate"\n' > "$CRATE/Cargo.toml"; printf 'fn main() {}\n' > "$CRATE/src/main.rs"
printf 'AcmeCorp\n' > "$CRATE/target/debug/build.log"
assert_rc "cargo publish, term only in target/ (not packaged) → allow" 0 "$(run 'cargo publish' "$CRATE")"
printf '// AcmeCorp\n' >> "$CRATE/src/main.rs"
assert_rc "cargo publish, term in the crate → block"                 2 "$(run 'cargo publish' "$CRATE")"
assert_rc "cargo publish --manifest-path reads that crate"           2 "$(run "cargo publish --manifest-path $CRATE/Cargo.toml" "$T")"
PY="$T/py"; mkdir -p "$PY/dist"
printf 'PK\000\003AcmeCorp/__init__.py' > "$PY/dist/pkg-1.0-py3-none-any.whl"
assert_rc "twine upload dist/*, term only inside a binary → allow"   0 "$(run 'twine upload dist/*' "$PY")"
printf 'Name: AcmeCorp\n' > "$PY/dist/PKG-INFO"
assert_rc "python -m twine upload dist/*, term in a text file → block" 2 "$(run 'python3 -m twine upload --repository testpypi dist/*' "$PY")"
OKDIR="$T/okdir"; mkdir -p "$OKDIR"; printf 'hello\n' > "$OKDIR/a.txt"
assert_rc "scp <file> host:path, term in the file → block"           2 "$(run "scp -P 2222 $PJ deploy@example.com:/srv/" "$T")"
assert_rc "scp host:path <local> (a download) → allow"               0 "$(run "scp deploy@example.com:/srv/x.json $PJ" "$T")"
assert_rc "rsync -av <dir>/ host:path, term in the dir → block"      2 "$(run "rsync -av --exclude .cache $SITE/ deploy@example.com:/var/www" "$T")"
assert_rc "rsync host:path <dir> (a download) → allow"               0 "$(run "rsync -av example.com:/var/www/ $SITE" "$T")"
assert_rc "rsync <clean dir> rsync://host → allow"                   0 "$(run "rsync -a $OKDIR/ rsync://example.com/www" "$T")"
assert_rc "aws s3 cp <file> s3://, term in the file → block"         2 "$(run "aws s3 cp $PJ s3://bucket/p.json --acl private" "$T")"
assert_rc "aws s3 sync <dir> s3:// → block"                          2 "$(run "aws --profile x s3 sync $SITE s3://bucket/site" "$T")"
assert_rc "aws s3 cp s3:// <local> (a download) → allow"             0 "$(run "aws s3 cp s3://bucket/p.json $PJ" "$T")"
assert_rc "gsutil -m cp <file> gs:// → block"                        2 "$(run "gsutil -m cp $PJ gs://bucket/" "$T")"
assert_rc "gcloud storage rsync <dir> gs:// → block"                 2 "$(run "gcloud storage rsync $SITE gs://bucket/site" "$T")"
assert_rc "rclone copy <dir> remote: → block"                        2 "$(run "rclone copy $SITE backup:bucket --transfers 4" "$T")"
assert_rc "rclone copy remote: <dir> (a download) → allow"           0 "$(run "rclone copy backup:bucket $SITE" "$T")"
assert_rc "firebase deploy, term in the project → block"             2 "$(run 'firebase deploy --only hosting' "$SITE")"
assert_rc "firebase deploy, clean project → allow"                   0 "$(run 'firebase deploy' "$OKSITE")"
assert_rc "wrangler deploy, term in the project → block"             2 "$(run 'npx wrangler deploy' "$SITE")"
assert_rc "wrangler pages deploy <dir> reads that dir"               2 "$(run "wrangler pages deploy dist --project-name x" "$SITE")"
assert_rc "fly deploy, term in the project → block"                  2 "$(run 'fly deploy --remote-only' "$SITE")"
assert_rc "surge <dir> <domain> → block"                             2 "$(run 'surge ./dist example.surge.sh' "$SITE")"
assert_rc "surge login is not a deploy → allow"                      0 "$(run 'surge login' "$SITE")"

say "exemptions stay with the push they belong to"
printf '{"%s": ["AcmeCorp"]}\n' "$REPO" > "$EX"
assert_rc "exempted push && deploy of a term-bearing site → block"   2 "$(GUARD_EXEMPT_FILE="$EX" run "git push origin main && cd $SITE && vercel deploy" "$REPO")"
assert_rc "exempted push && a post carrying the term → block"        2 "$(GUARD_EXEMPT_FILE="$EX" run 'git push origin main && curl -d "AcmeCorp is live" https://example.com' "$REPO")"
EXR="$T/acmecorp-app"; mkdir -p "$EXR"; git -C "$EXR" init -q
printf 'AcmeCorp app\n' > "$EXR/a.md"; git -C "$EXR" add -A; C "$EXR" -m "AcmeCorp start"
printf '{"%s": ["AcmeCorp"]}\n' "$EXR" > "$EX"
assert_rc "commit + push of an exempted repo that names it → allow"  0 "$(GUARD_EXEMPT_FILE="$EX" run "cd $EXR && git commit -qm 'AcmeCorp: tidy' && git push" "$T")"

say "posts the raw patterns miss are still read"
assert_rc "curl -sd TERM (clustered flags) → block"                 2 "$(run 'curl -sd "AcmeCorp is live" https://x.example' "$T")"
assert_rc "curl --json TERM → block"                                2 "$(run "curl --json '{\"a\":\"AcmeCorp\"}' https://x.example" "$T")"
assert_rc "curl -d TERM on a continuation line → block"             2 "$(run "$(printf 'curl -s https://x.example/a \\\n  -d AcmeCorp')" "$T")"
assert_rc "gh -R o/r pr create, term in the title → block"          2 "$(run 'gh -R o/r pr create --title "AcmeCorp fix" --body x' "$T")"
assert_rc "gh --repo o/r issue comment, term in the body → block"   2 "$(run 'gh --repo o/r issue comment 1 --body "AcmeCorp"' "$T")"
assert_rc "gh \"repo\" create TERM (quoted subcommand) → block"     2 "$(run 'gh "repo" create AcmeCorp-site --public' "$T")"
assert_rc "curl -sSL (a plain download) → allow"                    0 "$(run 'curl -sSL https://x.example/AcmeCorp.tar.gz -o a.tgz' "$T")"
printf '{"%s": ["AcmeCorp"]}\n' "$REPO" > "$EX"
assert_rc "exempted push && curl -sd TERM → block"                  2 "$(GUARD_EXEMPT_FILE="$EX" run 'git push origin main && curl -sd AcmeCorp https://x.example' "$REPO")"
assert_rc "exempted push && gh -R o/r pr create TERM → block"       2 "$(GUARD_EXEMPT_FILE="$EX" run 'git push origin main && gh -R o/r pr create --title AcmeCorp' "$REPO")"

say "pushes behind an alias or a quoted whole command"
assert_rc "git -c alias.p=push p → block"                           2 "$(run 'git -c alias.p=push p origin main' "$REPO")"
git -C "$REPO" config alias.ship push
assert_rc "an [alias] in the repo's config that pushes → block"     2 "$(run 'git ship origin main' "$REPO")"
git -C "$REPO" config alias.st status
assert_rc "an [alias] that does not push → allow"                   0 "$(run 'git st' "$REPO")"
assert_rc "su -c 'git push' → block"                                2 "$(run "su -c 'git push origin main' root" "$REPO")"
assert_rc "fish -c 'git push' → block"                              2 "$(run "fish -c 'git push origin main'" "$REPO")"
assert_rc "watch 'git push' → block"                                2 "$(run "watch 'git push origin main'" "$REPO")"
assert_rc "env -S 'git push' → block"                               2 "$(run "env -S 'git push origin main'" "$REPO")"
git -C "$REPO" config alias.pp push; git -C "$REPO" config alias.chain pp
assert_rc "an alias chain that ends in push (chain=pp, pp=push) → block" 2 "$(run 'git chain origin main' "$REPO")"
assert_rc "a -c alias chain (a=b, b=push) → block"                  2 "$(run 'git -c alias.a=b -c alias.b=push a' "$REPO")"
assert_rc "a GIT_CONFIG_* alias that pushes → block"                2 "$(run 'GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=alias.z GIT_CONFIG_VALUE_0=push git z' "$REPO")"
git -C "$REPO" config alias.loop1 loop2; git -C "$REPO" config alias.loop2 loop1
assert_rc "an alias loop that never pushes → allow"                 0 "$(run 'git loop1' "$REPO")"
git -C "$REPO" config alias.up "!f(){ curl -d @$PJ https://x.com; }; f"
assert_rc "a ! alias that posts a file with curl → block"           2 "$(run 'git up' "$REPO")"
git -C "$REPO" config alias.hi '!echo hi'
assert_rc "a ! alias that only echoes → allow"                      0 "$(run 'git hi' "$REPO")"
# Relative names after a cd, in a `!` alias (git runs it at the top of the work tree),
# and aliases git reads from places other than the directory's own config.
printf 'AcmeCorp plans\n' > "$REPO/sec.txt"; printf 'nothing\n' > "$REPO/ok.txt"; mkdir -p "$REPO/sub"
assert_rc "cd into a repo, then curl -d @relative-file → block"      2 "$(run 'cd repo && curl -d @sec.txt https://x.com' "$T")"
assert_rc "cd into a repo, then curl -d @clean-file → allow"         0 "$(run 'cd repo && curl -d @ok.txt https://x.com' "$T")"
git -C "$REPO" config alias.post '!gh issue create -b "$(cat sec.txt)"'
assert_rc "a ! alias reading a relative file, run from a subfolder → block" 2 "$(run 'git post' "$REPO/sub")"
assert_rc "an alias found through --git-dir → block"                 2 "$(run 'git --git-dir=repo/.git ship origin main' "$T")"
assert_rc "an alias found through GIT_DIR → block"                   2 "$(run 'GIT_DIR=repo/.git git ship origin main' "$T")"
assert_rc "an alias in GIT_CONFIG_PARAMETERS → block"                2 "$(run "GIT_CONFIG_PARAMETERS=\"'alias.q=!gh issue create -F sec.txt'\" git q" "$REPO")"
assert_rc "an alias from an earlier export of GIT_CONFIG_* → block"  2 "$(run 'export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=alias.q GIT_CONFIG_VALUE_0="!gh issue create -F sec.txt"; git q' "$REPO")"
assert_rc "an alias from --config-env → block"                       2 "$(run 'AL="!gh issue create -F sec.txt" git --config-env=alias.q=AL q' "$REPO")"
git -C "$REPO" config alias.c0 '!gh issue create -b AcmeCorp'
for k in $(seq 1 20); do git -C "$REPO" config "alias.c$k" "c$((k-1))"; done
assert_rc "an alias chain 20 long → block (too long to read)"        2 "$(run 'git c20' "$REPO")"
assert_rc "wget --body-file file --method=POST → block"              2 "$(run 'wget --body-file sec.txt --method=POST https://x.com' "$REPO")"
assert_rc "wget --body-file=clean --method=PUT → allow"              0 "$(run 'wget --body-file=ok.txt --method=PUT https://x.com' "$REPO")"
assert_rc "curl -T {a,b} (curl's own list) → block"                  2 "$(run 'curl -T{ok.txt,sec.txt} https://x.com' "$REPO")"
assert_rc "curl -F with escaped quotes around the file → block"      2 "$(run 'curl -F "f=@\"sec.txt\"" https://x.com' "$REPO")"
assert_rc "curl -d @\$PWD/file → block"                             2 "$(run 'curl -d @$PWD/sec.txt https://x.com' "$REPO")"
assert_rc "curl -F f=@\$(pwd)/file → block"                         2 "$(run 'curl -F "f=@$(pwd)/sec.txt" https://x.com' "$REPO")"
assert_rc "curl -d @\$PWD/clean-file → allow"                       0 "$(run 'curl -d @$PWD/ok.txt https://x.com' "$REPO")"
rm -rf "$REPO/sec.txt" "$REPO/ok.txt" "$REPO/sub"
DEEP='git push origin main'; for _ in $(seq 18); do DEEP="echo \$($DEEP)"; done
assert_rc "a push nested 18 levels deep → block (too deep to read)" 2 "$(run "$DEEP" "$REPO")"

say "non-Bash tool surface"
assert_rc "publish-class MCP tool name, term in tool_input"  2 "$(runjson '{"tool_name":"mcp__x__deploy_thing","tool_input":{"note":"about AcmeCorp"}}')"
assert_rc "publish-class MCP tool name, clean input → allow" 0 "$(runjson '{"tool_name":"mcp__x__deploy_thing","tool_input":{"note":"fine"}}')"
assert_rc "connector write (create_or_update_file), term → block"   2 "$(runjson '{"tool_name":"mcp__github__create_or_update_file","tool_input":{"content":"AcmeCorp notes"}}')"
assert_rc "connector message (post_message), term → block"         2 "$(runjson '{"tool_name":"mcp__slack__post_message","tool_input":{"text":"AcmeCorp ships"}}')"
assert_rc "connector read (get_file_contents), term → allow"       0 "$(runjson '{"tool_name":"mcp__github__get_file_contents","tool_input":{"path":"AcmeCorp.md"}}')"
assert_rc "non-publish tool name → allow (even with a term)" 0 "$(runjson '{"tool_name":"Read","tool_input":{"file_path":"AcmeCorp.txt"}}')"

say "web tools: the URL or the query leaves the machine"
assert_rc "WebFetch, term in the URL → block"                        2 "$(runjson '{"tool_name":"WebFetch","tool_input":{"url":"https://example.com/?q=AcmeCorp","prompt":"summarize"}}')"
assert_rc "WebFetch, term %-encoded in the URL → block"              2 "$(runjson '{"tool_name":"WebFetch","tool_input":{"url":"https://example.com/search?q=zeta%2Dplan","prompt":"x"}}')"
assert_rc "WebFetch, term only in the prompt (stays with Claude) → allow" 0 "$(runjson '{"tool_name":"WebFetch","tool_input":{"url":"https://example.com/","prompt":"what does this say about AcmeCorp"}}')"
assert_rc "WebSearch, term in the query → block"                     2 "$(runjson '{"tool_name":"WebSearch","tool_input":{"query":"AcmeCorp launch date"}}')"
assert_rc "WebSearch, clean query → allow"                           0 "$(runjson '{"tool_name":"WebSearch","tool_input":{"query":"node release schedule"}}')"

say "fail closed: an internal error blocks while terms are armed"
assert_rc "unreadable hook payload → block"                          2 "$(runjson 'not json')"
assert_rc "an error inside the scan (cwd not a string) → block"      2 "$(runjson '{"tool_name":"Bash","tool_input":{"command":"cd sub && git push"},"cwd":42}')"
if grep -q 'BLOCK-ERROR' "$GUARD_PUBLISH_LOG" 2>/dev/null; then say "  ✓ an internal error is logged as such"; else say "  ✗ internal error not logged"; FAILS=$((FAILS+1)); fi

say "--scan: record check before /handoff commits"
scan() { node "$G" --scan "$@" >/dev/null 2>&1; echo $?; }
AR="$T/adr-repo"; mkdir -p "$AR/docs/adr"; git -C "$AR" init -q
printf '# 0001. Choose zeta-plan over the old scheme\n' > "$AR/docs/adr/0001-x.md"
printf '# 0002. Nothing confidential here\n' > "$AR/docs/adr/0002-y.md"
assert_rc "term in a record → block"                         2 "$(scan "$AR/docs/adr/0001-x.md" "$AR/docs/adr/0002-y.md")"
assert_rc "clean records → allow"                            0 "$(scan "$AR/docs/adr/0002-y.md")"
assert_rc "match is case-insensitive"                        2 "$(printf 'about ACMECORP\n' > "$AR/docs/adr/0003-z.md"; scan "$AR/docs/adr/0003-z.md")"
printf '{"%s": ["zeta-plan"]}\n' "$AR" > "$EX"
assert_rc "repo's own codename exempted → allow"             0 "$(GUARD_EXEMPT_FILE="$EX" scan "$AR/docs/adr/0001-x.md")"
assert_rc "unreadable file → fail-open allow"                0 "$(scan "$T/does-not-exist.md")"
assert_rc "no files → allow"                                 0 "$(scan)"
SL="$T/scan.log"
GUARD_PUBLISH_LOG="$SL" node "$G" --scan "$AR/docs/adr/0001-x.md" >/dev/null 2>&1
if grep -q 'SCAN-BLOCK hits=\[zeta-plan' "$SL" 2>/dev/null; then say "  ✓ hit is logged unmasked locally"; else say "  ✗ scan hit not logged"; FAILS=$((FAILS+1)); fi

say "empty denylist = disarmed by design (armed:0, blocks nothing)"
printf '%s\n```\n# emptied\n```\n%s\n' "<!-- CONFIDENTIAL-TERMS:BEGIN -->" "<!-- CONFIDENTIAL-TERMS:END -->" > "$DL"
ST=$(node "$G" --status)
case "$ST" in "armed:0 source:$DL") say "  ✓ --status reports armed:0";; *) say "  ✗ --status(empty): got '$ST'"; FAILS=$((FAILS+1));; esac
assert_rc "term-bearing push allowed when denylist is empty" 0 "$(run 'git push origin main' "$REPO")"
assert_rc "--scan with an empty denylist → allow"            0 "$(scan "$AR/docs/adr/0001-x.md")"
assert_rc "unreadable payload with an empty denylist → allow" 0 "$(runjson 'not json')"

say "wrapper passthrough (guard-publish.sh resolves node, forwards args + exit code)"
ST=$(sh "$HERE/../guard-publish.sh" --status)
case "$ST" in "armed:0 source:$DL") say "  ✓ wrapper --status matches direct invocation";; *) say "  ✗ wrapper --status: got '$ST'"; FAILS=$((FAILS+1));; esac

say "wrapper: no node until the list has a term, fail closed once it has"
W="$HERE/../guard-publish.sh"
NONODE="$T/bin-nonode"; FAKE="$T/bin-fakenode"; mkdir -p "$NONODE" "$FAKE"
for t in sh awk tr cmp dirname date cat mkdir; do
  ln -sf "$(command -v "$t")" "$NONODE/$t"; ln -sf "$(command -v "$t")" "$FAKE/$t"
done
printf '#!/bin/sh\necho started >> "$NODE_MARK"\nexit "${FAKE_RC:-0}"\n' > "$FAKE/node"; chmod +x "$FAKE/node"
MARK="$T/node.mark"; WH="$T/whome"; mkdir -p "$WH/.claude/state" "$WH/v"
# wrap <bin dir> [VAR=value...] [-- args...] → the wrapper's exit code; stderr in $T/wrap.err,
# and $MARK exists when it started node. Nothing on PATH but the bin dir; no node fallback.
wrap() {
  local bin=$1 vars=(); shift
  while [ $# -gt 0 ] && [ "$1" != "--" ]; do vars+=("$1"); shift; done
  [ $# -gt 0 ] && shift
  rm -f "$MARK"
  printf '{"tool_name":"Bash","tool_input":{"command":"ls"}}' \
    | env -u GUARD_PUBLISH_DENYLIST -u CLAUDE_PATHS_ENV HOME="$WH" PATH="$bin" GUARD_PUBLISH_NODE_FALLBACK= \
        NODE_MARK="$MARK" ${vars[@]+"${vars[@]}"} sh "$W" "$@" >"$T/wrap.out" 2>"$T/wrap.err"
  echo $?
}
started() { [ -f "$MARK" ] && echo yes || echo no; }
assert_eq() { if [ "$3" = "$2" ]; then say "  ✓ $1"; else say "  ✗ $1: want '$2', got '$3'"; FAILS=$((FAILS+1)); fi; }
page() { printf '%s\n```\n%b\n```\n%s\n' "<!-- CONFIDENTIAL-TERMS:BEGIN -->" "$2" "<!-- CONFIDENTIAL-TERMS:END -->" > "$1"; }
EMPTY="$T/empty-terms.md"; page "$EMPTY" '# AcmeCorp (commented out)\n- # zeta-plan\n<!-- note -->\n'
ARMED="$T/armed-terms.md"; page "$ARMED" '# a comment\n- AcmeCorp\n'

assert_rc "disarmed, no node anywhere → allow"                       0 "$(wrap "$NONODE" GUARD_PUBLISH_DENYLIST="$EMPTY")"
rc=$(wrap "$FAKE" GUARD_PUBLISH_DENYLIST="$EMPTY"); assert_eq "disarmed → node is never started" "0 no" "$rc $(started)"
rc=$(wrap "$FAKE"); assert_eq "no list anywhere → node is never started" "0 no" "$rc $(started)"
printf 'GUARD_PUBLISH_DENYLIST="$HOME/v/Terms.md"  # the vault page\n' > "$WH/.claude/state/paths.env"
cp "$EMPTY" "$WH/v/Terms.md"
rc=$(wrap "$FAKE"); assert_eq "disarmed page named in paths.env → node is never started" "0 no" "$rc $(started)"
cp "$ARMED" "$WH/v/Terms.md"
rc=$(wrap "$FAKE"); assert_eq "armed page named in paths.env → node runs" "0 yes" "$rc $(started)"
rm -f "$WH/.claude/state/paths.env" "$WH/v/Terms.md"
printf 'AcmeCorp\n' > "$WH/.claude/confidential-terms.txt"
rc=$(wrap "$FAKE"); assert_eq "armed legacy list → node runs" "0 yes" "$rc $(started)"
rm -f "$WH/.claude/confidential-terms.txt"
assert_rc "armed, node missing → block"                              2 "$(wrap "$NONODE" GUARD_PUBLISH_DENYLIST="$ARMED")"
if grep -q 'Install node' "$T/wrap.err"; then say "  ✓ the block says how to fix it"; else say "  ✗ node-missing block message: $(cat "$T/wrap.err")"; FAILS=$((FAILS+1)); fi
assert_rc "armed, node crashes (exit 1) → block"                     2 "$(wrap "$FAKE" GUARD_PUBLISH_DENYLIST="$ARMED" FAKE_RC=1)"
assert_rc "armed, node allows → allow"                               0 "$(wrap "$FAKE" GUARD_PUBLISH_DENYLIST="$ARMED" FAKE_RC=0)"
assert_rc "armed, node blocks → block"                               2 "$(wrap "$FAKE" GUARD_PUBLISH_DENYLIST="$ARMED" FAKE_RC=2)"
rc=$(wrap "$FAKE" GUARD_PUBLISH_DENYLIST="$EMPTY" -- --status); assert_eq "--status runs node even when disarmed" "0 yes" "$rc $(started)"
assert_rc "--scan with node missing keeps failing open"              0 "$(wrap "$NONODE" GUARD_PUBLISH_DENYLIST="$ARMED" -- --scan "$BODY")"
hook() { printf '{"tool_name":"Bash","tool_input":{"command":%s},"cwd":"%s"}' \
  "$(python3 -c 'import json,sys;print(json.dumps(sys.argv[1]))' "$1")" "$2" \
  | GUARD_PUBLISH_DENYLIST="$ARMED" sh "$W" >/dev/null 2>&1; echo $?; }
assert_rc "end to end with real node: term-bearing push → block"     2 "$(hook 'git push origin main' "$REPO")"
assert_rc "end to end with real node: clean push → allow"            0 "$(hook 'git push origin main' "$CLEAN")"

say "wrapper fast path agrees with the guard's own list reading"
# The fast path may start node for a list the guard reads as empty (that only costs a
# node start), never the reverse. The plain cases must take the fast path.
fix() { printf '%b' "$2" > "$T/fx-$1"; echo "$T/fx-$1"; }
check_fixture() { # name file exact(1|0)
  local js sh_armed
  js=$(GUARD_PUBLISH_DENYLIST="$2" "$REALNODE" "$G" --status | sed 's/^armed:\([0-9]*\).*/\1/')
  wrap "$FAKE" GUARD_PUBLISH_DENYLIST="$2" >/dev/null; sh_armed=$(started)
  if [ "$sh_armed" = no ] && [ "$js" -gt 0 ]; then say "  ✗ $1: fast path skipped node, guard reads $js term(s)"; FAILS=$((FAILS+1)); return; fi
  if [ "$3" = 1 ] && [ "$js" -eq 0 ] && [ "$sh_armed" = yes ]; then
    say "  ✗ $1: guard reads no term, yet the fast path started node"; FAILS=$((FAILS+1)); return
  fi
  say "  ✓ $1 (guard: $js term(s), node started: $sh_armed)"
}
B='<!-- CONFIDENTIAL-TERMS:BEGIN -->'; E='<!-- CONFIDENTIAL-TERMS:END -->'
check_fixture "comments, fences, blank lines only"     "$(fix a "$B\n\`\`\`\n# x\n\n\`\`\`\n$E\n")" 1
check_fixture "a bullet commented out (- # term)"      "$(fix b "$B\n- # AcmeCorp\n$E\n")" 1
check_fixture "a backticked term with a comment"       "$(fix c "$B\n<!-- n -->\n  \`AcmeCorp\`  # note\n$E\n")" 1
check_fixture "a lone backticked # (- \`#\`)"          "$(fix d "$B\n- \`#\`\n$E\n")" 1
check_fixture "markers on one line, prose after"       "$(fix e "$B x $E\nprose that is not a term\n")" 1
check_fixture "END before BEGIN: the whole file counts" "$(fix f "$E\nprose\n$B\n")" 1
check_fixture "no markers: a flat list"                "$(fix g "# list\n\nAcmeCorp\n")" 1
check_fixture "CRLF lines, comments only"              "$(fix h "$B\r\n# x\r\n$E\r\n")" 1
check_fixture "CRLF lines, one term"                   "$(fix i "$B\r\nAcmeCorp\r\n$E\r\n")" 1
check_fixture "a lone dash is a term to the guard"     "$(fix j "$B\n-\n$E\n")" 1
check_fixture "a non-ASCII term"                       "$(fix k "$B\n- Z\xc3\xbcrich-plan\n$E\n")" 1
check_fixture "a no-break space alone (doubt → node)"  "$(fix l "$B\n\xc2\xa0\n$E\n")" 0
check_fixture "a NUL byte before a term"               "$(fix m "$B\n\0AcmeCorp\n$E\n")" 0
check_fixture "a carriage return inside a commented bullet" "$(fix n "$B\n- #a\rb\n$E\n")" 0
for pe in 'GUARD_PUBLISH_DENYLIST="$HOME/v/Terms.md"  # c' "GUARD_PUBLISH_DENYLIST=~/v/Terms.md # c" \
          "  GUARD_PUBLISH_DENYLIST = '\$HOME/v/Terms.md'" 'export GUARD_PUBLISH_DENYLIST=$HOME/v/Terms.md' \
          '# GUARD_PUBLISH_DENYLIST=$HOME/v/Terms.md' 'GUARD_PUBLISH_DENYLIST=""'; do
  printf '%s\n' "$pe" > "$WH/.claude/state/paths.env"
  for state in armed empty; do
    if [ $state = armed ]; then cp "$ARMED" "$WH/v/Terms.md"; rm -f "$WH/.claude/confidential-terms.txt"
    else cp "$EMPTY" "$WH/v/Terms.md"; printf 'AcmeCorp\n' > "$WH/.claude/confidential-terms.txt"; fi
    js=$(env -u GUARD_PUBLISH_DENYLIST -u CLAUDE_PATHS_ENV HOME="$WH" "$REALNODE" "$G" --status | sed 's/^armed:\([0-9]*\).*/\1/')
    wrap "$FAKE" >/dev/null; s=$(started)
    if { [ "$js" -gt 0 ] && [ "$s" = yes ]; } || { [ "$js" -eq 0 ] && [ "$s" = no ]; }; then say "  ✓ paths.env [$pe], page $state: same source as the guard"
    else say "  ✗ paths.env [$pe], page $state: guard reads $js term(s), node started: $s"; FAILS=$((FAILS+1)); fi
  done
done
rm -f "$WH/.claude/state/paths.env" "$WH/v/Terms.md" "$WH/.claude/confidential-terms.txt"

if [ "$FAILS" -eq 0 ]; then say "ALL PASS"; exit 0; else say "FAILED: $FAILS case(s)"; exit 1; fi
