# guard-publish: Specification

**File:** `claude/hooks/guard-publish.js`, invoked via the `claude/hooks/guard-publish.sh` wrapper  
**Hook event:** `PreToolUse`: matcher `Bash` is the primary surface; the same hook handles any additionally wired matcher, `WebFetch|WebSearch` included (see §Non-Bash surfaces)  
**Wired in:** `hooks/hooks.json` (`PreToolUse`, matchers `Bash`, `mcp__.*` and `WebFetch|WebSearch`), loaded by the plugin or rendered into `~/.claude/settings.json` by a clone install without `--plugin`  
**Timeout:** 15 s (the hook's own; the content scans stop at 10 s, see §Time limit)  
**Runtime:** node, resolved by the wrapper: `command -v node` → `/opt/homebrew/bin/node` → `/usr/local/bin/node` (`GUARD_PUBLISH_NODE_FALLBACK` replaces the last two, for the tests). See §Wrapper: with an empty denylist node is never started; with terms armed the guard fails closed.

---

## Purpose

Prevent Claude from inadvertently publishing confidential content (unreleased project names, private identifiers) to any public surface. The hook runs before every Bash tool call and intercepts commands that would push data outside the local machine.

## Threat model (what this does and does NOT defend against)

**In scope, inadvertent plaintext leaks.** The guard catches a denylisted term appearing *as written* in: the command text, a referenced file, what a push sends (every commit the destination lacks: added lines, file names, messages), the files at HEAD and in the working tree, the folder or files a deploy or upload sends, or a base64-encoded argument (≥12 chars, so even a short term encoded alone is decoded). This is the real risk: Claude or the user pushing a repo that literally contains a denylisted codename, or curling a file that mentions it.

**Out of scope, deliberate obfuscation (unwinnable for a string scanner).** An actor who *intends* to exfiltrate can always defeat a substring scan: shell concatenation (`ZZ""QTOP`), backslash/ANSI-C escapes (`$'…\x54'`), encryption, or simply `git push` to a remote the guard never inspects. No PreToolUse scanner can stop this, and chasing it would add fragile regex that breaks normal commands. These are **accepted non-goals**, confirmed by adversarial testing. The guard is a *safety net against accidents*, not a DLP system against a hostile user, the **confirm-before-publish habit is the primary control** (see global `~/.claude/CLAUDE.md`).

---

## Decision table

| Condition | Outcome | Exit code |
|-----------|---------|-----------|
| Hook call, **empty denylist** (no automatic block, see §Denylist management) | Silent allow; the wrapper decides this without starting node | 0 |
| Hook call, terms armed, node not resolvable (wrapper) | **Block** + stderr (install node, or empty the list) + once/day alert (fail-closed) | **2** |
| Hook call, terms armed, node exits with anything but 0 or 2 (a crash, a missing script) | **Block** + stderr naming the exit code (fail-closed) | **2** |
| `WebFetch` / `WebSearch`, no term in the URL / query | Silent allow | 0 |
| `WebFetch` / `WebSearch`, term in the URL (decoded too) or the query | **Hard block** | **2** |
| Other non-Bash tool, name not publish-class | Silent allow | 0 |
| Other non-Bash tool, publish-class name, no term in `tool_input` | Silent allow | 0 |
| Other non-Bash tool, publish-class name, term in `tool_input` | **Hard block** | **2** |
| Empty command | Silent allow | 0 |
| Command is not publish-class | Silent allow | 0 |
| Publish-class, no confidential term found | Silent allow | 0 |
| Publish-class, confidential term found | **Hard block** | **2** |
| Publish-class, a content scan could not finish within 10 s | **Hard block** (`BLOCK-INCOMPLETE` in the log, no alert) | **2** |
| Internal error (an unreadable payload included), terms armed | **Block** + stderr naming the error, `BLOCK-ERROR` in the log, no alert (fail-closed) | **2** |
| Internal error, empty denylist | Allow with a stderr breadcrumb (nothing to protect) | 0 |
| `--status` / `--scan`, node not resolvable (wrapper) | Allow + stderr + once/day alert (fail-open: they guard nothing by themselves) | 0 |
| `--scan <file>...`, term in a file (after per-repo exemptions) | **Hard block**: term + file on stderr, local log, no alert | **2** |
| `--scan`, clean / empty denylist / no readable file / internal error | Allow (stderr names an empty list, an unreadable file or the error) | 0 |

Exit code 2 is an unconditional block, the harness honors it in every permission mode, including `bypassPermissions`. Exit 0 defers to normal permission resolution.

The `ask` path (permissionDecision: "ask") has been removed. Routine publish confirmation is handled by Claude's own pre-action workflow. (The block-or-allow table above is authoritative.)

---

## Publish-class patterns

A command is publish-class when one of the commands it runs (see §How a command is read) is a `git push` or one of the deploys, uploads and posts in the second table, or when its text matches one of these patterns:

| Pattern | Matches |
|---------|---------|
| `gh repo create` | Create GitHub repo |
| `gh release create` | Create GitHub release |
| `gh gist create` | Create GitHub gist |
| `gh pr create` | Create pull request |
| `gh repo edit` | Edit repo metadata, `--visibility public` flips a private repo public; description/homepage edits publish their text |
| `gh secret set` | Upload a secret value to GitHub |
| `gh release upload` | Upload assets to a release |
| `gh gist edit` | Edit a (possibly public) gist |
| `gh workflow run` | Dispatch a workflow, `-f` inputs travel to GitHub |
| `gh -R <repo> …` / `gh --repo <repo> …` | The `gh` rows above match with the global repository flag before the subcommand too |
| `gh api` + write method | `gh api` with PUT/POST/PATCH/DELETE, `--field`/`-f`/`-F`/`--raw-field`, or `--input` (POST-by-default) |
| `npm publish` / `pnpm publish` / `yarn publish` | Package registry publish |
| `vercel` (not read-only subcommands) | Deploy/configure on Vercel; excludes `env pull`, `ls`, `list`, `inspect`, `whoami`, `logs`, `link`, `--help` |
| `netlify deploy` | Netlify deploy |
| `curl` with upload flags | `-X POST/PUT/PATCH` (or `--request`), `--data`, `--json`, `-d`, `-T` or `-F` (alone or in a flag cluster such as `-sd`), `--upload-file`, `--form` |
| `wget` with upload flags | `--post-data`, `--post-file`, `--body-data`, `--body-file`, `--method POST/PUT/PATCH` |

**`git push`** is a push only when `push` is git's subcommand: git's global options are skipped first (`-C <path>`, `-c <k=v>`, `--git-dir`, `--work-tree` and `--namespace` with `=` or a separate value, `--no-pager`, `-P`, `-p`/`--paginate`, `--bare`, `--literal-pathspecs` and the other flag options). A git alias whose expansion starts with `push`, after following a chain of aliases as git does, is a push too, and so is a `!` alias whose text names push. `git stash push`, `git commit -m "push it"`, `echo "git push"` and `echo git push` are not pushes. The same parsed command drives the content scan below, so classification and scanning never disagree.

**Deploys, uploads and posts** recognised from the parsed command (what each sends is read, see haystack 6):

| Command | Publish-class when |
|---------|--------------------|
| `vercel`, `netlify deploy`, `npm`/`pnpm`/`yarn publish`, `gh release create\|upload`, `gh gist create\|edit` | as in the table above |
| `gh issue create\|comment\|edit`, `gh pr create\|comment\|review\|edit` | always (the text, plus files named by `--body-file`/`-F`) |
| `docker push`, `docker image push` (and `podman`) | always (the text: an image name can carry a term) |
| `cargo publish` | always |
| `twine upload`, `python -m twine upload` | always |
| `scp`, `rsync` | a later operand is remote (`host:path`, `host::module`, `scp://`, `rsync://`) and a local source comes before it |
| `aws s3 cp\|sync\|mv`, `gsutil cp\|rsync\|mv`, `gcloud storage cp\|rsync\|mv`, `rclone copy\|sync\|move\|copyto\|moveto` | a later operand is remote (`s3://`, `gs://`, an rclone `remote:`) and a local source comes before it |
| `firebase deploy`, `wrangler deploy\|publish`, `wrangler pages deploy`, `fly deploy` / `flyctl deploy`, `surge` (not `surge login` and its other account subcommands) | always |

### How a command is read

A small shell lexer splits the command into statements on `;`, `&`, `&&`, `|`, `||`, `|&`, newlines and `( )`, with quotes removed and nothing expanded. Each statement's program is the first word after `NAME=value` prefixes, shell keywords (`if`, `then`, `do`, `!`, `{` ...) and wrappers (`env`, `sudo`, `doas`, `command`, `exec`, `nohup`, `nice`, `time`, `timeout`, `xargs`, `caffeinate`, `npx`, `bunx`, `pnpm dlx`, `yarn dlx`). Behind any other program the guard does not know (`stdbuf -oL git push`, `flock f bash -c …`), the first later word naming `git`, a shell or `eval` is read as a command too, and so is any later word that holds a whole command (`su -c 'git push'`, `fish -c '…'`, `watch '…'`, `env -S '…'`), unless the program never runs its arguments locally (`echo`, `printf`, `grep`, `cat`, `ls`, `ssh`, `for`, `export` and the like). A git alias that pushes counts as a push: one given with `git -c alias.NAME=…`, with `--config-env`, with `GIT_CONFIG_COUNT`/`GIT_CONFIG_KEY_n`/`GIT_CONFIG_VALUE_n` or `GIT_CONFIG_PARAMETERS` set anywhere in the command, or an `[alias]` entry in the git config git would read there (the directory the command runs in, `--git-dir` or `GIT_DIR`), chains of aliases included. An alias loop is not a push (git refuses it too); a chain longer than 16 is not read and blocks while armed. A `!` alias runs a shell command at the top of the work tree, so its text is read like the command itself, from there (a `git up` whose alias runs `curl -d @notes.md …` is a post of that file). Nesting deeper than 16 levels is not read; it blocks while armed. Commands are also read inside `$( )`, backticks, `<( )` and `>( )`, inside the quoted argument of `bash -c`, `sh -c`, `zsh -c` (and `dash`, `ksh`) and `eval`, and in a here-doc or here-string fed to a shell, or `echo`/`printf` output piped into one. A here-doc body that is only data (`cat <<EOF`) and a `#` comment are never read as commands. A `cd <dir>` (or `pushd`) moves the statements after it; a `cd` inside `( )` or a substitution stays there; `cd -` and a `cd` whose target only the shell can work out (`cd "$(git rev-parse --show-toplevel)"`) leave the directory as it was.

---

## Confidential-term scan

**Denylist source order** (first readable wins):
1. `$GUARD_PUBLISH_DENYLIST`: env override (used by the test harness)
2. **the vault denylist page** (path from `paths.env` / `GUARD_PUBLISH_DENYLIST`), the single source of truth; an Obsidian page the user edits in-app
3. `~/.claude/confidential-terms.txt`: legacy file, retired to a term-free pointer (resilience fallback only)

**Format:** If the source contains a `CONFIDENTIAL-TERMS:BEGIN … CONFIDENTIAL-TERMS:END` marker pair (the vault page), only lines **between** the markers are read, so the page's own frontmatter/prose (which names the terms in explanation) can never become accidental terms. Otherwise (legacy flat `.txt`) the whole file is read. Within the read region: one term per line, `#` begins a comment, blank lines ignored, leading `- `/`* ` bullets and surrounding `` ` `` backticks tolerated.  
Matching: case-insensitive substring match across all haystacks.

Edits to the vault page take effect on the **next** publish-class command, the hook re-reads the source every invocation; no restart.

### Haystacks scanned (in order)

**1. Raw command string**  
The literal text of the Bash command.

**2. Decoded base64 blobs**  
Any token matching `[A-Za-z0-9+/]{12,}={0,2}` is base64-decoded. If the result contains printable ASCII it is added as a haystack. Catches secrets/terms passed as encoded arguments. The threshold is 12 so even a short term encoded alone is still decoded and caught.

**3. Shell variable expansions**  
`VAR=value` assignments in the command are parsed. `$VAR` and `${VAR}` references are expanded before scanning. `~` is expanded to the home directory.

**4. Referenced files (up to 5 MB each)**  
Files the command reads are extracted by these patterns and their contents scanned:
- `< FILE`: stdin redirect
- `--input FILE`, `-T FILE`, `--upload-file FILE`, `--data-binary @FILE`, `--data @FILE`
- `@FILE`: curl-style file reference, quoted or not, also attached to a flag or a flag cluster (`-d@FILE`, `-sd@FILE`, `-d '@FILE'`)
- curl's `-T FILE` in a cluster (`-sT FILE`, `-sTFILE`) and with curl's `{a,b}` list, `--data-urlencode NAME@FILE`, wget's `--post-file FILE` and `--body-file FILE`
- a relative name is read in every directory the command's parts run in (after a `cd`, under `git -C`, at the top of the work tree for a `!` alias); `$PWD` and `$(pwd)` are that directory
- `cat FILE`, also inside a substitution: `"$(cat FILE)"`, `"$(< FILE)"`, `` `cat FILE` `` (a bare path stops at `(`, `)` and a backtick)
- `base64 FILE`

**5. What a `git push` sends**  
Each push runs in the directory an earlier `cd <dir>` moved it to (else the session's), so `cd ~/app && git push` reads `~/app`. `git -C <path>` is honoured (each one relative to the one before), and so are `--git-dir` and `--work-tree` (or `GIT_DIR` / `GIT_WORK_TREE` before the command); a bare repository is read too. For each `git push`, in that repository:

- **The commits it sends.** The refs are read from the arguments: none = `HEAD`; a refspec's source side (`HEAD`, a branch, a tag, a sha); a glob, `--all`, `--branches`, `--mirror`, `--tags`, `--follow-tags` widen it; `--delete` and `:dst` send nothing. The destination is the named remote, else the branch's push remote, else `origin`. Commits already on that remote's remote-tracking refs are skipped; a URL, a path, or a remote with no tracking refs gets the whole history. For the rest, git reads every commit message (`git log -i -E --grep`) and every line the commits add, file names included (`git log -p -m -U0 -i -G`, removed lines dropped). Reading only HEAD and the working tree would miss a term removed at HEAD but still in an older commit, or one that lived only in a commit message.
- **HEAD and the working tree** of tracked files, as before:
```
git -C <root> grep -I -i -h -E <terms> HEAD -- .
git -C <root> grep -I -i -h -E <terms>      -- .
```
It uses `-h` (matching **lines**, not `-l` filenames) so the matched text, which contains the term, enters the haystack and the term-filter identifies exactly which term leaked. Output is capped at 200 KB.

`gh repo create … --push` is read the same way, as a push of `HEAD`'s whole history from `--source` (default `.`).

**6. What a deploy or upload sends**  
When a statement's program is `vercel` (any subcommand that uploads: none, `deploy`, or a path), `netlify deploy`, or `npm`/`pnpm`/`yarn publish`, the folder it sends is read: a path argument, `--cwd`, `--dir`/`-d` and `--functions`, else the statement's directory. Every file under it is read except `.git/`, `node_modules/`, binary files and files over 5 MB, without following links. A tarball given to `npm publish` is read as a file. `gh release create|upload` and `gh gist create|edit --add` have the files they upload read.

The other paths in the second table of §Publish-class patterns:

- **gh issue / pr posts:** the command text, plus the files named by `--body-file` / `-F` (`-` is stdin: its here-doc is in the command text).
- **docker push:** the command text only.
- **cargo publish:** the crate folder, the statement's directory or `--manifest-path`'s folder, with `target/` left out (cargo never packages it).
- **twine upload:** the files it names, globs expanded (`dist/*`), each read only when it is text; wheels and tarballs are binary and skipped.
- **scp, rsync and the cloud copies:** the local sources before the remote destination, globs expanded; a folder is walked like a deploy folder, a file is read when it is text.
- **firebase, wrangler, fly, surge:** the project folder: the statement's directory, a named folder (`wrangler pages deploy <dir>`, `fly deploy <dir>`, `surge <dir>`, `surge --project`), the folder of `--config`, plus wrangler's `--assets` and `--site`.

Exemptions (§Per-repo exemptions) never apply to what a deploy, upload or post sends: it is public.

**Time limit.** The content scans (sections 5 and 6) share a 10 s budget (`GUARD_PUBLISH_BUDGET_MS` overrides, for the tests). A scan that runs out of time blocks, with a message saying the check is incomplete and a `BLOCK-INCOMPLETE` line in the log: the guard cannot vouch for content it did not read, and the hook's own 15 s timeout would otherwise end in an allow.

The pattern and repo root are passed to the shell **via environment variables** (`"$GP_PAT"`/`"$GP_ROOT"`), never interpolated into the command string: a denylist term containing a shell metacharacter (backtick, `$`) could otherwise run as command substitution or break the pattern via expansion. This way it can never reach `sh` as code, and a `$`-bearing term matches correctly.

> **Note:** this check must use `git grep -h` (matching lines), not `-l`. Feeding the *filename* into the haystack would only block when a matching file was itself *named* with a denied term (essentially never). Covered by `tests/test_guard_publish.sh`.

---

## Non-Bash surfaces (MCP deploy/publish tools)

A `PreToolUse` hook with matcher `Bash` never observes MCP tool calls (`mcp__claude_ai_Vercel__deploy_to_vercel`, Canva publish-brand-template, …). Coverage exists **only where a matcher is wired in settings.json**, that wiring, not this hook, decides which tools are seen. When this hook receives a non-Bash payload:

- `WebFetch` and `WebSearch` are always scanned: a URL or a search query goes to a third party, so a term in it is exposure. For `WebFetch` the URL is read as written and with its `%`-encoded runs decoded, and its `prompt` is left out (it stays with Claude, the site never sees it). For `WebSearch` the whole `tool_input` is read (the query, the domain lists). They need a `PreToolUse` matcher `WebFetch|WebSearch` wired to this hook.
- If the tool **name** matches `deploy|publish|push|release|gist|upload|create|update|post|send|comment|issue|message|commit|merge|share` (case-insensitive, covers `mcp__<server>__deploy_*`, `create_pull_request`, `post_message` style), the JSON-serialized `tool_input` is scanned for active terms; a hit hard-blocks (exit 2), otherwise silent allow. Read-shaped names that happen to match (e.g. `get_deployment`) cost only a scan, they block only on a term hit, and a term in an outbound MCP query is itself exposure.
- Any other tool name passes silently.

Plainly: **an MCP publish-capable tool with no wired matcher is not covered at all.** Disable publish-capable MCP tools in unattended contexts, or wire matchers for them.

## Status probe (`--status`)

`node guard-publish.js --status` (or via the wrapper: `sh guard-publish.sh --status`) reads no stdin, prints one machine-readable line, and exits 0:

```
armed:<N> source:<path>
```

`N` = count of active (non-comment) terms; `source` = the denylist file actually read (`(none)` if no source was readable). **`armed:0` means the guard blocks nothing on confidential grounds, allowed-by-design, but visible**: a reconcile probe row consumes this so a disarmed guard reads as DISARMED instead of the self-watch's old "hook string is wired = healthy".

## Record scan (`--scan`)

`guard-publish.sh --scan <file>...` checks files that are *about to be committed* against the same denylist, before any publish exists to block. Its one caller is `/handoff` step 1g, on the ADR files it just wrote plus `CLAUDE.md`: a decision record is filled from a session, sessions wander into other projects and private matters, and a committed record travels with every later push. Same match (case-insensitive substring), same per-repo exemptions (root = the first file's git toplevel, so a private repo named after its own codename still passes). A hit exits 2 with the term and file on stderr and an unmasked `SCAN-BLOCK` line in the local log; no phone alert, because nothing has left the machine and the user is at the terminal. An empty denylist, no files, an unreadable file or an internal error exits 0 with a stderr line saying what was not checked: fail-open, because a record check that cannot run must not stop `/handoff`, which reports it and continues. `--scan` carries no stdin payload and is checked before the hook input is read.

## Hard block message

When a confidential term is found, the hook writes to stderr and exits 2:

```
🚫 BLOCKED by guard-publish: this publish-class command would expose confidential term(s): <term>, ...
These are on your private "Confidential Terms" denylist.
Do NOT publish this. If it's a false positive, edit the term out of that vault page; if the content is genuinely confidential, remove it before publishing.
```

(The non-Bash variant opens with `tool call <name> would expose …` instead of `this publish-class command would expose …`.)

**Alert side-effect:** every hard block on a term also pushes an alert to the user via `alert.py` (title `🚫 Publish blocked`), started detached so the exit 2 never waits on the phone channel (a slow send could outlast the hook's timeout, and a killed hook lets the command run). The alert deliberately **never contains the term itself**, that would route the confidential string to Telegram, defeating the guard. Set `GUARD_PUBLISH_NO_ALERT` (any non-empty value) to suppress the push; the test probes set it so exercising blocks doesn't buzz the user's phone, and the wrapper's node-missing alert honors the same variable.

---

## Denylist management

**Edit the list in your vault:** open the page `GUARD_PUBLISH_DENYLIST` names (setup finds a page called `Confidential Terms.md`) and add or remove lines between the `CONFIDENTIAL-TERMS` markers. That is the only place to manage terms.

**An empty denylist means NO automatic block**: the guard allows every publish, and the confirm-before-publish habit is the entire control. Commented-out terms (`#`) stay in the block, ready to re-arm. `--status` makes this state visible (`armed:0`).

**`EOF` is intentionally excluded**, it is the most common heredoc delimiter (`<<'EOF'`) and would false-block virtually every publish that uses a heredoc. A project-name term already catches meaningful leaks of that project without the false-blocks.

**Adding/removing a term:** edit the fenced block in the vault page (uncomment to arm, comment/delete to disarm). Takes effect on the next publish-class command.

---

## Known limitations

- **`git push`** reads the commits the destination lacks, judged by its remote-tracking refs: if those are stale (someone else pushed and you never fetched), commits the remote already has are read again, which can only block more, never less. Untracked files are not read (they are not pushed). Tags pushed by an explicit refspec are read through the commit they point at; an annotated tag's own message is not read.
- **Deploys** read the folder the tool would send, not the tool's own ignore rules (`.vercelignore`, `files` in `package.json`): a term in a file the tool would skip still blocks. Build output made remotely is not read.
- **Variable expansion** is best-effort: literal `VAR=value` assignments in the same command string are resolved first, then the hook's own environment.
- **Base64 detection** requires blobs ≥ 12 chars; shorter encoded values are not decoded.
- **Deliberate obfuscation is not defended** (concatenation, escaping, encryption, unknown remotes), see Threat model. Out of scope by design.
- **File scanning** is limited to 5 MB per file to avoid I/O stalls.
- **Command reading** (§How a command is read) follows what a person or Claude normally writes, not every corner of the shell grammar: a `case` pattern's `)` inside `$( )`, loops (`for r in a b; do git -C $r push; done` reads `$r` as empty), shell functions and shell aliases are not followed, and a command a shell reads from a file (`sh script.sh`, `bash < cmds.txt`) or from `ssh host "…"` is not read. The plain-pattern table is still matched over the whole text, so it can catch more than it should (`echo "npm publish"`), which only costs a scan.
- **Copies** (scp, rsync, cloud storage) leave `.git/` and `node_modules/` out of a folder, like deploys, and do not read the tool's own include/exclude rules. An option the guard does not know to take a value can make it read one more path, never one fewer source.
- **`gh api` read calls** using `--method GET` (or no method, and no `--input`/`--field`) are not publish-class and pass through silently.
- **Non-Bash surfaces are covered only via wired matchers** (see §Non-Bash surfaces): an MCP publish/deploy tool whose matcher is not wired in settings.json bypasses the guard entirely.
- **node absent with terms armed = every call blocked**: the wrapper cannot classify a command without node, so it blocks them all (exit 2) until node is installed or the list is emptied (see §Wrapper). `--status` and `--scan` keep failing open.
- **Empty denylist = no automatic block** (see §Denylist management), `armed:0` in `--status`.

## Test harness

`claude/hooks/tests/test_guard_publish.sh` feeds synthetic `PreToolUse` payloads to the repo copy of the hook and asserts each exit code: classification (git's subcommand, `bash -c`, `eval`, `$( )`, here-docs), the file and git-content scans, what a push sends (older commits, messages, a remote that already has the history, a new remote, `cd`, `--git-dir`), deploys, uploads, copies and posts, the time limit, exemptions per push, the non-Bash and web surfaces, fail-closed internal errors, `--scan`, `--status`, and the wrapper: its no-node fast path (checked against the guard's own list reading on tricky lists and `paths.env` lines) and its fail-closed paths. It sandboxes `HOME`, uses neutral fake terms, logs blocks to a temp file and sends no alerts. Run after any edit: `bash claude/hooks/tests/test_guard_publish.sh` (or the whole suite, `run-all.sh`).

---

## Error handling

A content scan that runs out of time blocks (see §Time limit). Any other exception in the hook, an unreadable payload included, **blocks while terms are armed** (exit 2): the guard cannot vouch for a call it did not check. The stderr line names the error, and a `BLOCK-ERROR` line goes to the local log. With an empty denylist there is nothing to protect, so an error allows with a stderr breadcrumb. `--scan` keeps failing open on an internal error (its caller, `/handoff`, reports and continues). If no denylist file is readable, the list is empty and every call is allowed.

## Wrapper (`guard-publish.sh`)

As a hook (no arguments) the wrapper first decides, in sh and awk, whether the denylist has an active term: the same sources in the same order as the guard (`$GUARD_PUBLISH_DENYLIST`, `GUARD_PUBLISH_DENYLIST` in `paths.env`, the legacy file; the first readable one wins), only the lines between the `CONFIDENTIAL-TERMS` markers when both are there, and the same line rules. With no active term it exits 0 without starting node, so the guard costs nothing until it has terms. Whenever it cannot tell for sure (a byte outside plain ASCII on a list line or on the `paths.env` line, a NUL byte, an unreadable source it cannot parse), it counts the list as armed and lets node decide: a doubt costs a node start, never an unguarded call.

With at least one term it fails closed. node is resolved (see Runtime above); its exit 0 or 2 stands, and any other exit (a crash, a missing `guard-publish.js`) blocks with exit 2 and a stderr line. If node cannot be found the call is blocked (exit 2) with a stderr line saying to install node or empty the list, plus an `alert.py` push at most once per day (marker `~/.claude/state/guard-publish-node-missing.alerted`, written before the push, which runs in the background so it cannot outlast the hook's timeout; `GUARD_PUBLISH_NO_ALERT` suppresses it). `--status` and `--scan` always go to node and, without node, still fail open with the stderr line and the same alert. stdin passes through untouched.

## Per-repo exemptions

Pushing a private repo that legitimately contains its own codename is not a leak, but the
push-scan is visibility-blind, so armed repo-name terms hard-blocked every push of those
repos (a case the denylist's own notes anticipated).

`~/.claude/state/guard-exempt.json` ($GUARD_EXEMPT_FILE overrides; see
`config/guard-exempt.example.json`) maps absolute repo roots to the terms exempt for
pushes OF that repo. The term list is computed per push: each push reads its own
repository (what it sends, HEAD, the working tree) without that repository's exempted
terms. The command's own text and the files it names drop only the terms every publish
in the command exempts, so `cd ~/dev/<codename> && git commit -m "<codename>: …" && git
push` passes, while a deploy, upload or post in the same command (`git push && vercel
deploy`, `git push && curl -d …`) gets no exemption at all, and neither does the command
text beside it. Other terms still block, other repos are unaffected, non-push surfaces
(gh/npm/curl/MCP/web) ignore the map entirely, and a missing or unparseable file means no
exemptions: the guard degrades toward blocking, never toward leaking. Entries should be
MEASURED (git grep the repo for the armed terms), not guessed. Covered by
test_guard_publish.sh §per-repo exemptions and §exemptions stay with the push.

## Block observability

Every block leaves a full local record and an actionable alert. Blocks append one
line to `~/.claude/logs/guard-publish.log` ($GUARD_PUBLISH_LOG overrides): timestamp,
the unmasked term(s), cwd, repo root, the push remote, and the first 200 chars of the
command. The phone alert names the trigger masked (`s******k`) and the destination
(repo → remote), enough to triage from the phone; the exact string stays local. The
session stderr shows terms and destination in full.
