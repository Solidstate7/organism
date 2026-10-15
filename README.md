# Organism: Intent to Implementation

![CI](https://github.com/Solidstate7/organism/actions/workflows/ci.yml/badge.svg)
![License: MIT](https://img.shields.io/badge/license-MIT-green)
![Platform: macOS](https://img.shields.io/badge/platform-macOS-lightgrey)

**Vibe coding is dead. Lost reasons are dead.** Organism is the layer that carries what
you *intend* down into what gets *implemented*, and keeps the two joined. Call it
**semantic sovereignty**: the meaning of your software stays under your authority, in
records you own, instead of dying with a chat window or drifting inside docs nobody
checks.

Concretely: your agent writes code faster than you can read it, and Organism keeps your
part of that work. It saves every [Claude Code](https://claude.com/claude-code) session
as clear notes. Important decisions become version-controlled records, and each record
links to the commits that carry it out. The next session starts with a short "where we
are" note. Plain files and a few scripts, as a Claude Code plugin. No vector store, no
server; everything opens in a text editor. Organism does not run agents. It makes heavy
agent use something you can sustain and still stand behind.

![Four layers, stacked: natural language, decision records, commits, code. Solid arrows go down for creation; dashed arrows go up for management.](docs/img/layers.svg)

**Downward is creation**: what you say becomes a record, then a commit, then code.
**Upward is management**: start from any line of code and climb back to the words that
asked for it.

## The problem

Working with an AI by giving prompts, accepting the result, and moving on (*vibe
coding*) is fast. But it leaves no record of your part in the work. The generated code
remains. Your corrections, rejected ideas, and choices between options do not. A month
later, the conversation that shaped the code has expired. The commit message says
`fix auth`. The code shows what was built. It does not show what was rejected or why.

An agent writes code several times faster than a person can fully review it. The
industry calls the growing gap between how much code you have and how much you
understand **comprehension debt**, and calls it **drift** when the code slowly moves
away from its original goal. Notes written by hand cannot close the gap. Documentation
does not show an error when it becomes wrong. It just stops being true. Most tools add
model power. That makes more output, which people cannot read any faster.

## How it works

Organism answers comprehension debt with two records you own:

| The debt | The answer |
|---|---|
| You forget **why** code exists | Every decision is a dated record, linked both ways to its commits. Review a change against the intent it serves, not against a bare diff. See [What /handoff does](#what-handoff-does). |
| You lose **what was said** while building | Every session is saved to your vault as notes you own. Vendors delete transcripts; your copy stays. See [Session notes](#session-notes). |

In one sentence: understanding is written down while it is cheap, and linked to the code
it explains.

![The loop: a session feeds session notes after every reply. /handoff writes the "where we are" note and the decision records, and the next session loads the note when it starts. Records link both ways to commits. Reconcile, an optional and experimental check, reads the commits, rewrites the doc page's machine-owned half, and warns you on a mismatch.](docs/img/loop.svg)

## Quickstart

There are two ways in. The plugin is the quick one: session notes, `/handoff`, the
publish guard and the "where we are" note, with nothing outside Claude Code's own
folders. The clone adds the background jobs and an `organism` command in your terminal,
and needs macOS for the jobs.

### As a Claude Code plugin

```sh
claude plugin marketplace add Solidstate7/organism
claude plugin install organism@organism
```

(Inside a session, `/plugin marketplace add Solidstate7/organism` and
`/plugin install organism@organism` do the same.) Start a new session, then run
`/organism-setup`: it shows what setup found (your vault, your repos) and how to apply
it. Until a vault is set, session notes are off; `/handoff` works from the start, and
the publish guard as soon as you give it terms to block. Update with `/plugin` inside
Claude Code.

Claude Code keeps the installed plugin as a copy in `~/.claude/plugins/cache`, so the
`organism` command is on the PATH of Claude's own shell but not of your terminal.
Setup asks before it writes and needs a terminal to ask in, so apply it from your
terminal through that copy:

```sh
"$(ls -d ~/.claude/plugins/cache/organism/organism/*/ | tail -1)bin/organism" setup
```

`organism addon` prints how to move to a clone later, for the background jobs.

### From a clone (macOS)

```sh
git clone https://github.com/Solidstate7/organism.git ~/.organism
bash ~/.organism/bootstrap.sh    # checks tools, links the hooks into ~/.claude, installs the CLI
organism setup                   # finds your vault + repos, asks, then writes the config
```

Add `--plugin` to the bootstrap line to also load the clone as a plugin, in place: the
plugin then supplies the hooks and slash commands, and your clone stays the code they
run. Depending on its version, Claude Code may also keep a local copy of the plugin
under `~/.claude/plugins/cache` (not synced); edit and pull in the clone, never in
that copy. A clone already installed without `--plugin` moves over with
`organism migrate --to-plugin`, which shows each step and can be undone with
`organism migrate --to-symlink`.

Bootstrap puts the `organism` command in `~/.local/bin`. If your shell can't find it,
add that folder to your PATH; bootstrap prints the line to add.

Bootstrap takes over part of `~/.claude`: `hooks`, `commands`, `git-hooks` and
`keybindings.json` become links into this repo (with `--plugin`, `commands` is left
out), and anything already there is moved to a `.pre-organism.bak` copy. Without
`--plugin` it also merges its hooks into `~/.claude/settings.json`: your settings and
your own hook entries are kept, and the original file is copied to
`settings.json.pre-organism.bak`. Hook scripts you keep in `~/.claude/hooks` are not:
that folder becomes a link, so its old contents move to
`~/.claude/hooks.pre-organism.bak`, and a settings entry that runs a script from there
stops working until you move the script somewhere else and update the entry's path. It
schedules two background jobs as well (see [Requirements](#requirements)).
`organism uninstall` prints what it would undo, and `organism uninstall --yes` undoes it.

Setup looks for your vault in `~/Projects`, `~/Documents`, Obsidian's iCloud folder and
your home folder; `--vault <dir>` names any folder of markdown instead. It asks before it
writes anything, and without a terminal to ask in it writes nothing. `--yes` accepts the
defaults it shows, and `/organism-setup` (`/setup` in a clone installed without
`--plugin`) shows the same plan inside a session. Every config key is optional: a
missing key falls back to a default or turns its feature off.

From then on, the only habit is running `/handoff` at the end of a session that made a
decision ([what it does](#what-handoff-does)); the hooks do the rest. To update a clone,
run `git pull` in `~/.organism`.

## What /handoff does

One command, run at the end of a session that made a decision. It commits the work,
writes each decision as a dated record, links the record to the commits that carry it
out, and leaves a short "where we are" note for the next session.

![The eight steps of /handoff in four jobs. Settle the code: read the repo, commit the work first. Record the reasons: find the decisions, write the records, show the draft, commit the records. Link records to commits. Hand over: rewrite the brief. Each step carries the problem it prevents.](docs/img/handoff.svg)

One run of `/handoff`, top to bottom, in four jobs. Under each step: the problem it
exists to prevent. Amber: the only two places it may ask you something. Green: the
approval step. Yellow: optional. Dashed boxes touch git and are skipped outside a
repository.

### What you get

| Output | Where | What it is |
|---|---|---|
| Decision records | `docs/adr/NNNN-title.md` (or the repo's existing ADR folder) | One file per decision: the situation, the choice, what it costs and enables, the alternatives. Dated, numbered in order. |
| Decisions index | `CLAUDE.md`, "Decisions" section | One line per record for the newest twenty or so, newest first, with a link to the rest. Loaded at the start of every session, so the project's decisions are known before the first prompt. |
| Links | git notes on the commits | Each record lists the commits that carry it out. Each commit names its record. Ask a commit "why do you exist", ask a record "what did you become". |
| The brief | `HANDOFF.md` | Where things stand, what is live, what comes next. Kept out of git; loaded into the next session automatically. |

### What it promises

- **Order.** The commits that implement a decision always sit before the record that
  explains it. History reads as cause, then effect.
- **Touch.** It writes the record files, the index, and the brief, and adds the brief
  (and, if you agree, generated data) to `.gitignore`. It never edits code.
- **Brief.** This command is the only thing that writes the brief; nothing overwrites it
  between runs. Skip the run and the next session still gets the brief as you left it,
  plus a list of the sessions that came after it, each with a link to its saved session
  note.
- **Git.** It commits on the current branch and never pushes unless instructed. Outside
  a git repository it still writes the records and the brief and skips the git steps.
- **Scope.** Only this project's decisions are recorded. A decision that belongs to
  another project is named in the draft and left for that project's own run. Records
  describe situations, never account numbers, addresses or names.
- **Records.** It never renumbers, rewrites, or deletes an existing record. A wrong
  record is superseded by a new one, and both stay on file.
- **Honesty.** A rebuilt record is labeled "reconstructed" and names its sources.
  Nothing is invented. A session with no decision produces only the brief.
- **Questions.** At most two, both conditional, marked amber in the diagram. It never
  asks "approve this?".

### How to run it

| Form | What it does |
|---|---|
| `/handoff` | The normal run, at the end of a session. |
| `/handoff <note>` | Same run, with your note steering the brief and offered as a decision. Not a flag: any text after the command is the note. |
| `/handoff --retro` | Rebuild past decisions now, without waiting for the offer. Text after the flag narrows it: a subsystem, a date range, a suspected gap. |

Two notes and what each becomes:

```
/handoff next: migrate the queue consumer, then re-run the load test
```

Shapes the brief. The next session opens on this.

```
/handoff we keep SQLite for the job store; Postgres adds an ops burden we can't staff
```

Reads as a decision, a choice with a reason, so it becomes a record as well.

> Rebuilt records are inferred from commit logs, old session transcripts and in-repo
> docs, and their links to commits are attached after the fact. Each record names its
> sources and marks what is verbatim, what is post-hoc and what is inferred. Treat an
> inferred intent as a hypothesis to check against the code and the people who were
> there, not as the decision itself.

### What the links change

The links change how you review code. Open a decision record and it lists the commits
that carry it out, so you read each diff against the reason it was supposed to serve.
Start from a commit and it names its decision, so a change with no decision behind it
stands out, and a change that drifts from its decision is caught at review, not months
later.

## Session notes

Every session is copied into your vault (its `_claude-live` folder) as it happens, as
plain markdown optimised for Obsidian: your prompts and Claude's replies in full, each
tool call folded to one line. Vendors delete transcripts (Claude Code after 30 days);
your copy stays.

| File | What it holds | Open it when |
|---|---|---|
| `latest.md` | The turn that just finished, in full, then a table of the 30 most recent sessions, newest first | "What did we just do?" Pin it in Obsidian |
| `sessions/<id>.md` | One session, every turn, rebuilt from the transcript after each reply | "How did we get here?" |

One turn in a session note, as Organism writes it (tool calls fold away in Obsidian):

````markdown
### 🕐 2026-03-02 09:00:07

**🧑 You**

List the top-level files.

**🤖 Claude** · `opus-5-5`

Here is the listing.

> [!note]- 🖥️ Bash · List files
>
> ```
> README.md
> src
> ```

Two entries: README.md and src.
````

`/incognito on` keeps a stretch of work out of the copy. `/incognito purge` deletes
everything the session has saved so far and stops saving until `/incognito off`.
[docs/SESSION-NOTES.md](docs/SESSION-NOTES.md) shows the format.
Common secrets are redacted before anything is written, and a project can opt out
entirely: [What the notes contain](docs/SESSION-NOTES.md#what-the-notes-contain) says
what is copied and how to keep a project out.

## Command reference

### In a Claude Code session (slash commands)

| Command | What it does | Arguments |
|---|---|---|
| `/handoff` | Commit the work, write the decision records, link them to commits, rewrite the "where we are" note | `--retro`, or a free-text note: see [How to run it](#how-to-run-it) |
| `/incognito` | Control the saved copy of the session | `on` · `off` · `toggle` · `purge` (delete this session's saved copy and stop saving) · `status` (default) |
| `/organism-setup` | Show this machine's configuration and how to apply it (`/setup` in a clone installed without `--plugin`) | none |

With the plugin, `/organism:handoff` and `/organism:incognito` work too, in case another
plugin also has a `/handoff`.

### Hooks that just run (no interaction)

| Hook | Fires | Does |
|---|---|---|
| session copy | after every reply | Adds the exchange to this session's note in the vault |
| note loading | session start | Loads the repo's "where we are" note, then lists the sessions that ran after it was written, each with a link to its saved copy |
| session close | session end | Adds the final exchange to this session's saved copy, and runs the doc check if the repo is set up for it |
| publish guard | before `Bash`, connector, web fetch and web search tool calls | Blocks pushes, deploys, web requests and publishing connector calls that contain terms from the blocked list (see below) |
| post-merge sync | after `git pull` (clone install) | Updates the machine to match the repo |

### In a terminal

Bootstrap also installs an `organism` command for setup and maintenance; `organism help`
lists what it does. With the plugin alone, the same command runs from the plugin's copy
(see [Quickstart](#as-a-claude-code-plugin)); the background-job verbs say they need a clone.

## Requirements

| Needs | For |
|---|---|
| Claude Code ≥ 2.1.295 | Everything: the hooks and the slash commands run inside it. Older versions let a call through when the guard times out |
| git, python3 ≥ 3.9 | The main system. It only uses the standard library, with nothing to install through pip |
| macOS | The clone install's background jobs (`launchd`) and notifications. The hooks are tested on macOS and Linux |
| node | The publish guard. Without node, while your blocked list has terms, the guard blocks every call it checks (and alerts once a day) until you install node or empty the list. An empty list never needs node |

Everything else is optional. In a clone install, up to three jobs run on macOS's
`launchd`: a daily check that the machine matches the repo and a weekly log cleanup,
which bootstrap schedules, and the experimental doc check, which runs daily only on a
Mac you pick in setup. `organism stop` removes them, and each also runs by hand. Without
Telegram, alerts show as macOS notifications. Without Obsidian, any folder of markdown
files works as the vault: point setup at it with `--vault`.

## Optional extras

Each has a section in [docs/ADVANCED.md](docs/ADVANCED.md).

- **Publish guard.** Blocks a `git push`, deploy, upload or web request that would send a
  term from your private banned-words list, such as a project codename. For a push it
  reads every commit the remote doesn't have yet (added lines, file names, messages),
  also when the push hides behind a git alias or a wrapper such as `bash -c`; for a
  deploy, the folder it sends; for `curl`, `wget` and `gh`, the files they upload. It is
  a guard against accidents, not against someone set on leaking. An empty list blocks
  nothing.
- **More than one Mac.** Sync the vault (Obsidian Sync or any file sync) and your session
  notes follow you. The one shared job, the daily doc check, runs only on the Mac you
  pick, and setup never picks one for you, so nothing runs twice.
- **Telegram alerts.** Warnings on your phone, with delivery confirmed.
- **Doc check (experimental).** Off until you say yes to it in setup. Keeps a status
  table on one vault page per repo current: latest commit, branch, uncommitted files,
  decision count. It runs when a session in that repo ends, daily on the Mac you pick, or
  by hand with `organism check`. It does not read your prose. It warns only when a check
  you added fails, or when something it needs (a paired repo, its page, its own state)
  can't be read.

## Tests

```sh
bash claude/hooks/tests/run-all.sh   # prints ALL PASS or exits nonzero
```

CI runs the same command on every push, on macOS and on Linux; on Linux, checks that
need macOS skip themselves. See [CONTRIBUTING.md](CONTRIBUTING.md) for more.

## License

MIT. See [LICENSE](LICENSE).
