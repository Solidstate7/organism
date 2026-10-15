# Advanced: the parts most users never need

Everything here is optional, opt-in, or only relevant once the basics have earned trust.
The README covers the core; this page covers the rest. Each optional extra the README
lists has a section here: [the publish guard](#the-publish-guard), [more than one
Mac](#more-than-one-mac), [alerts](#alerts) and [the doc check](#the-doc-check-experimental).

## Decision records for old repos, and commits that explain themselves

A repo with history but no records still holds its decisions: in its commits, its old
session transcripts and its docs. `/handoff` notices that history and offers, once, to
**reconstruct** the missing records from it. Each rebuilt record is marked
*reconstructed* and names its sources; none is passed off as written at the time. A
brand-new repo just starts at record 0001; the tool detects which case it is in. Either
way, every record is **linked to the commits that carry it out, both directions**: ask a
commit "why do you exist", ask a decision "what did you become".

## Nothing here is a prerequisite for anything else

A natural worry: "do I need decision records before the doc check works?" No. Each part
works on its own:

| part | writes | needs |
|---|---|---|
| doc check | the fenced status table on each repo's vault page (branch, commit, uncommitted files, record count) | saying yes to it in setup |
| `/handoff` | the decision records and the brief | a session worth recording, or a past worth reconstructing |
| decision links | record ↔ commit, both directions, as git notes | nothing; `/handoff` links as it records |

**New repo:** its page starts with a table reading `0 ADR(s)`. That's honest, not broken:
the first `/handoff` creates `docs/adr/`, and the next doc check run shows the count as 1
with no hand-editing.

**Existing repo:** pairing works the same day (the status table doesn't care about the
past). `/handoff` then notices the history no record covers and offers to rebuild it. In
one sentence: the doc check tells the truth about *now* and never needs the past;
`/handoff` recovers the past and never blocks the present.

## The doc check (experimental)

Off until you turn it on: setup asks "Set up the doc check for N repo(s)?" (default no), and
`organism setup --section pairings` asks again later. Saying yes creates, in your vault's
`wiki` folder (or the folder in `CLAUDE_VAULT_WIKI`):

- one page per repo under `wiki/Repos/`. The top of the page is yours; below it, between
  two marker comments, is a status table (latest commit, branch, uncommitted files,
  record count) that the check rewrites. It never touches anything outside the markers.
- `wiki/Reconcile Queue.md`, where open warnings are listed.
- `~/.claude/state/reconcile-pairings.json`, which pairs each repo with its page (an older
  install may still have it in `~/.claude/hooks/`, which is read too).

It runs when a Claude Code session in a paired repo ends, by hand with `organism check`
on any Mac, and daily at 07:45 on the one Mac that owns the shared jobs (see [More than
one Mac](#more-than-one-mac)). Each run logs to `~/.claude/logs/reconcile.log`.

The status rows only display; they never warn. A warning comes when a check you add
yourself fails (a `guards` entry for that repo in `reconcile-pairings.json`), or when
something the check needs can't be read: a paired repo, its page, the markers on the
page, or the check's own state file. A warning reaches you three ways: as an alert (see
[Alerts](#alerts)), in the Reconcile Queue page, and as one line at the start of every
session until you clear it with `organism ack`. To drop
that session-start line on one Mac, create `~/.claude/state/reconcile-reminder.off`.

It does not read your prose. The table says what state the repo is in; whether the
words above it still hold is yours to judge.

## More than one Mac

**The problem:** you work from two Macs. "Doesn't GitHub already solve that?" For the
code, yes, and for nothing else. Three things never make it into a git remote:

1. **The AI sessions themselves.** What you and the model worked through on the desktop
   doesn't exist on the laptop. The code arrives; the conversation that explains it
   doesn't.
2. **The scheduled jobs.** Which machine runs the daily doc check: both? Neither?
   Whichever you set up last?
3. **The machine-local config** (paths, credentials, roles), which *shouldn't* travel.

The answers, in the same order. Sessions are saved into the vault, and the vault syncs
(Obsidian Sync, iCloud, anything), so **your conversation history merges across
machines the way code merges in git**, and any machine can pick up a thread another
machine started. The shared job runs only on the machine that claimed it. Config stays
local by a deny-by-default gitignore. In plain terms:

- **Identity is hardware, not config.** Each machine derives its identity from its
  hardware UUID. Roles live in a local, gitignored `~/.claude/state/host.json`, and the
  file pins that UUID, so *copying* your config to another Mac transfers nothing: the
  copy's UUID doesn't match, and its shared jobs switch themselves off.
- **Two kinds of jobs.** Per-machine jobs (the daily sync with the repo, the weekly log
  cleanup) run on every machine, each on its own files. The shared job (the daily doc
  check, which writes your vault) runs only on the one machine that claimed it. A new
  machine claims nothing: setup's role question defaults to keeping the current role, and
  `--yes` keeps it too. On your only Mac, answer `claim owner` to run the shared job there.
- **Deploying to N machines is `git pull` on each.** The post-merge hook brings each
  machine in line with the repo on its own. No deploy scripts, no drift between Macs.
- **Adding a machine:** clone, `bash bootstrap.sh`, `organism setup` (keep the default
  role), then follow `organism link` or [LINK-A-DEVICE.md](LINK-A-DEVICE.md).
- **Moving the shared job** (new laptop, selling the old one): on the old machine run
  `organism setup --section role` and answer `release`; then on the new one, the same
  command and `claim owner`; then `organism start` on both to reload their jobs. Done in
  that order, a move stopped halfway leaves neither machine running the job, never both.
- **Personal jobs stay personal:** anything you don't want in a public manifest goes in a
  gitignored `~/.claude/state/agents.local` on that machine, run by the same installer
  with the same role checks.

## What happens on `organism update`, exactly

`organism update` is `git pull` in the repo, and the pull is the whole procedure. A
`post-merge` hook (installed by `deploy.sh`, so a fresh clone gets it) runs
**`converge.sh`**, "make this machine match what the repo says should exist": re-run the
symlink deploy, regenerate the scheduled jobs from the manifest, unload any job the
manifest no longer names. That's all "converge" ever means here.

It exists because only *some* of the deploy follows a pull. The symlinked trees (`hooks/`,
`commands/`, `git-hooks/`, `keybindings.json`) go live instantly.
`settings.json` does not: it's generated as the committed base plus your gitignored
`~/.claude/state/settings.local.json` overlay. Neither do the `launchd` plists. (When the
clone is also loaded as a plugin, the hooks come from the plugin's `hooks/hooks.json`
and `settings.json` carries none of them; the plugin picks up a pull at the next session
start.)

That last one fails **silently**, which is the real reason any of this exists. Because the
hooks directory is a symlink into the repo, a pull that deletes a hook removes it from
the live tree the moment it lands, while `launchd` keeps firing the plist it already
holds, on schedule, at a script that is gone: a job neither running nor visibly dead.
`install-agents.sh` sweeps any `com.organism.*` plist absent from both its manifest and
your `agents.local`, and that sweep is the only thing that retires a job cleanly.

Git hooks are best-effort, though: a GUI client, `git fetch && git reset --hard`, or a
machine whose global `core.hooksPath` points at another tool's hooks will skip them. So
a daily job **re-runs the same match-the-repo script on every machine**. The hook makes
the common path correct; the timer makes the uncommon path survivable. Force it now with
`bash converge.sh`; its dated success marker means a silently dead updater shows up as
*stale*, not as nothing.

## Alerts

Doc check warnings and publish-guard blocks for an armed term reach you as alerts (a guard
block for a timeout or an internal error is only logged). They show as a macOS
notification, and with Telegram set up they also go to your phone. An alert sent without
a notification falls back to one when Telegram is not set up or the send fails, so no
alert is silent. Every alert is also written to
`~/.claude/logs/alerts.jsonl` before it is sent.

To set up Telegram, create a bot with BotFather, then save its token and your chat id in
`~/.claude/state/telegram-alerts.json` (outside the vault, never committed):

```json
{"bot_token": "123456:ABC…", "chat_id": "123456789"}
```

Then `chmod 600 ~/.claude/state/telegram-alerts.json`; setup's checklist checks both the
keys and the permissions.

The design rule everywhere: **silence means "the job didn't run", never "the alert
channel died"**. The daily sync and the weekly log cleanup each write a dated success
marker, and `organism status` shows one that went stale.

## The publish guard

Set it up before you need it. A guard runs before Claude's `Bash` commands, web fetches
and searches, and connector tool calls. When a command would publish something (`git
push`, `gh` commands that create, comment, edit or upload, `npm publish`, `cargo publish`,
`twine upload`, `docker push`, `vercel`, `netlify deploy` and other deploys, `scp`, `rsync`
or a cloud storage copy to a remote, a `curl` or `wget` upload) and the content contains a
term from your private blocked list, it stops the command. This catches a project being
published too early, by mistake.

What it reads:

- **Every publishing command:** the command text, base64 inside it, and any file it names
  (`--input`, `--body-file`, `@file`, `< file`, `cat file`, `"$(cat file)"`). Commands
  inside `bash -c`, `eval`, `$( )` and behind wrappers such as `env` or `sudo` count too;
  `git stash push` or `echo "git push"` does not.
- **`git push`:** every commit the destination doesn't have yet (the lines each one
  adds, its file names and its message), plus the tracked files at `HEAD` and in your
  working tree (untracked files are not read). A `cd <dir> &&` or `git -C <dir>` before
  the push is followed. `gh repo create --push` counts as a push of the whole history.
- **Deploys:** the folder `vercel`, `netlify deploy` or `npm publish` sends (skipping
  `.git`, `node_modules`, binary files and files over 5 MB), and the files `gh release`
  and `gh gist` upload.
- **Deploys and copies** (`firebase`, `wrangler`, `fly`, `surge`, `scp`, `rsync`, `aws s3`,
  `gsutil`, `gcloud storage`, `rclone`): the local folders and files they send.
- **Web tools:** the URL a web fetch opens and the query a web search sends.
- **Connector tools** whose name says they send or publish something (deploy, publish, push,
  release, gist, upload, create, update, post, send, comment, issue, message, commit, merge,
  share): everything the call sends. Other connector tools are not checked.

If it can't finish reading within 10 seconds, it blocks and says so, because it can't
vouch for what it didn't read. The same goes for an internal error, and for a missing
`node` while the list has terms: every call the guard sees is blocked until node is
installed or the list is emptied. While the list is empty the guard does not start node
at all, so it costs nothing until you add a term.

Setup uses one file: a markdown page. Setup finds a page named `Confidential Terms.md`
anywhere in your vault; or set `GUARD_PUBLISH_DENYLIST` in `paths.env` to any page. Put
one blocked term on each line between two markers:

```markdown
<!-- CONFIDENTIAL-TERMS:BEGIN -->
- project-falcon
- acme-internal
<!-- CONFIDENTIAL-TERMS:END -->
```

These can be project code names, client names, and unreleased product names. Matching
ignores case. Check that the guard is active:

```sh
organism guard-status   # armed:<N> (armed:0 means it blocks NOTHING)
```

An empty list blocks nothing, so you must add terms to it. A repo may carry its own code
name: list it for that repo in `~/.claude/state/guard-exempt.json` (see
`config/guard-exempt.example.json`), and pushes of that repo stop checking for it. The
guard sees only Claude's tool calls, never your own terminal, and it is a safety net
against accidents, not a sandbox. The full rules are in
`claude/hooks/guard-publish.spec.md`.

## `adr-link.sh`: the decision links by hand

`/handoff` makes the links for you; this is the tool underneath, for when you want them
by hand. In a repo:

- `query <N|hash>` returns the commits for a decision, or the decision for a commit.
- `link <commit> <N…>` joins them by hand.
- `backfill` builds links from older history (commit messages that mention a record).
- `backlinks write` rewrites the list of linked commits at the end of each record.

A commit can also name its decision itself, with an `ADR: N` (or `ADR: none`) line at the
end of its message. To be reminded of that line in every repo that has decision records,
run `git config --global core.hooksPath ~/.claude/git-hooks` (clone install only: a plugin
install has no such folder, and git then runs no hooks at all); the reminder only warns. To
require it in one repo, install the hook that blocks a commit without it (the
instructions are at the top of `claude/hooks/adr-link.sh`).
