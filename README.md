# Organism: Intent to Implementation

![CI](https://github.com/Solidstate7/organism/actions/workflows/ci.yml/badge.svg)
![License: MIT](https://img.shields.io/badge/license-MIT-green)
![Platform: macOS, Linux](https://img.shields.io/badge/platform-macOS%2C%20Linux-lightgrey)

Organism is a [Claude Code](https://claude.com/claude-code) plugin that keeps the reasons
behind the code your agent writes. It does three things:

1. It saves every session to your notes folder as readable markdown, as it happens.
2. At the end of a session, `/handoff` writes each decision as a dated record in the repo
   and links it to the commits that carry it out.
3. The next session starts with a short "where we are" brief, loaded automatically.

It's plain files, a few hooks and a few scripts. No server, no database, no vector
store: everything opens in a text editor and lives in git or in your own folder.
Organism doesn't run agents or make them smarter. It keeps a record of your part of the
work.

## Install

```sh
claude plugin marketplace add Solidstate7/organism
claude plugin install organism@organism
```

Inside a session, `/plugin marketplace add Solidstate7/organism` and
`/plugin install organism@organism` do the same. Then start a new session and run
`/organism-setup`. It shows what it found (your vault and your repos) and writes nothing
until you say yes.

A vault is any folder of markdown, and Obsidian is optional. Setup looks for one in
`~/Projects`, `~/Documents`, Obsidian's iCloud folder and your home folder. If yours is
somewhere else, tell it the path. Until a vault is set, session notes stay off; `/handoff`
works from the start.

After that, the only habit is running `/handoff` at the end of a session that made a
decision. The hooks do the rest. Update or remove Organism with `/plugin`.

## Why

A coding agent writes code faster than you can review it. The code stays, but your part
of the work doesn't: the corrections, the ideas you turned down, the reasons one option
won. A month later the conversation has expired (Claude Code deletes local transcripts
after 30 days by default), the commit says `fix auth`, and the diff shows what was built
but not what was rejected or why.

Addy Osmani calls the growing gap between how much code you have and how much of it you
understand [comprehension debt](https://addyosmani.com/blog/comprehension-debt/). Notes
written by hand don't close it, because they go stale silently. Organism writes the
understanding down while it's still in the session, and links it to the code it
explains. The longer argument is in the essay
[Semantic Sovereignty: Your Agent Writes the Code. Who Keeps the Reasons?](https://medium.com/@vanitasium/semantic-sovereignty-your-agent-writes-the-code-who-keeps-the-reasons-671fa8785adc)

## How it works

![Four layers, stacked: natural language, decision records, commits, code. Solid arrows go down for creation; dashed arrows go up for management.](docs/img/layers.svg)

Read the layers downwards and you see how work is created: what you say becomes a
record, then a commit, then code. Read them upwards and you can start from any line of
code and get back to the words that asked for it.

| What gets lost | What Organism keeps |
|---|---|
| Why the code exists | A dated decision record for each decision, linked both ways to its commits. See [What /handoff does](#what-handoff-does). |
| What was said while building it | Every session, saved to your vault as notes you own. See [Session notes](#session-notes). |

![The loop: a session feeds session notes after every reply. /handoff writes the "where we are" note and the decision records, and the next session loads the note when it starts. Records link both ways to commits. Reconcile, an optional and experimental check, reads the commits, rewrites the doc page's machine-owned half, and warns you on a mismatch.](docs/img/loop.svg)

## What /handoff does

You run it once, at the end of a session that made a decision. It commits the work,
writes each decision as a record, links the records to the commits that carry them out,
and rewrites the brief for the next session.

![The eight steps of /handoff in four jobs. Settle the code: read the repo, commit the work first. Record the reasons: find the decisions, write the records, show the draft, commit the records. Link records to commits. Hand over: rewrite the brief. Each step carries the problem it prevents.](docs/img/handoff.svg)

The diagram shows one run, top to bottom, in four jobs. Under each step is the problem it
prevents. Amber marks the only two places it may ask you something, green the approval
step and yellow what's optional. Dashed boxes touch git and are skipped outside a
repository.

### What it writes

| File | What it holds |
|---|---|
| `docs/adr/NNNN-title.md` (or the repo's existing ADR folder) | One decision per file: the situation, the choice, what it costs and enables, the alternatives. Numbered in order and dated. |
| `CLAUDE.md`, under "Decisions" | One line per record, newest first, the 20 most recent plus a link to the rest. Claude Code loads `CLAUDE.md` at every session start, so the decisions are known before the first prompt. |
| git notes on the commits | The link between a record and its commits. Each record lists its commits, and each commit names its record. |
| `HANDOFF.md` | The brief: where things stand, what's live, what comes next. Gitignored, and loaded into the next session automatically. |

A record is never renumbered, rewritten or deleted. A wrong one is superseded by a new
record, and both stay on file. The commits that carry out a decision land before the
record that explains it, so the history reads as cause, then effect.

Only `/handoff` writes the brief. If you skip a run, the next session still gets the
brief as you left it, plus links to the saved notes of the sessions that came after it.

### What it won't do

- It writes only the records, the index, the brief and `.gitignore`. It never edits code.
- It commits on the current branch and never pushes unless your note asks it to. Outside
  a git repository it skips the git steps.
- It records only this project's decisions. A decision about another project is named in
  the draft and left for that project's own run.
- Records describe situations, never account numbers, addresses or names. A secret in the
  work stops the run.
- It asks at most two questions (whether to rebuild old history, and whether to gitignore
  generated data), and never asks for approval: running the command is the approval.
- A session with no decision produces only the brief.

### Running it

| Form | What it does |
|---|---|
| `/handoff` | The normal run, at the end of a session. |
| `/handoff <note>` | The same run, with your note steering the brief. Any text after the command is the note. |
| `/handoff --retro` | Rebuild past decisions from the repo's history now, instead of waiting for the offer. Text after the flag narrows it: a subsystem, a date range, a suspected gap. |

A note that says what's next shapes the brief, and the next session opens on it:

```
/handoff next: migrate the queue consumer, then re-run the load test
```

A note that makes a choice and gives a reason becomes a record as well:

```
/handoff we keep SQLite for the job store; Postgres adds an ops burden we can't staff
```

In a repo with history but no records, `/handoff` offers once to rebuild them. Rebuilt
records are marked "reconstructed" and name their sources;
[docs/ADVANCED.md](docs/ADVANCED.md#decision-records-for-old-repos-and-commits-that-explain-themselves)
says how far to trust them.

The links change how you review. Open a record and it lists the commits that carry it
out, so you read each diff against the reason it was meant to serve. Start from a commit
and it names its decision, so a change with no decision behind it stands out.

## Session notes

Each session is copied into your vault's `_claude-live` folder after every reply, as
plain markdown optimised for Obsidian: your prompts and Claude's replies in full, each
tool call folded to one line.

| File | What it holds | Open it when |
|---|---|---|
| `latest.md` | The turn that just finished, in full, then a table of the 30 most recent sessions | You want to see what just happened. Pin it in Obsidian. |
| `sessions/<id>.md` | One session, every turn, rebuilt from the transcript after each reply | You want to see how you got here. |

Common secrets (API keys, tokens, private keys, passwords in URLs) are redacted before
anything is written. This is best effort, not a guarantee. A project can opt out with an
empty `.organism-no-notes` file. `/incognito on` keeps a stretch of work out of the
notes, and `/incognito purge` deletes what the current session has saved so far and
stops saving until `/incognito off`.
[docs/SESSION-NOTES.md](docs/SESSION-NOTES.md) shows the format and lists exactly what is
copied.

## Commands

| Command | What it does |
|---|---|
| `/handoff` | Commit the work, write the decision records, link them to commits, rewrite the brief. Takes `--retro` or a note. |
| `/incognito` | Control the session notes: `on`, `off`, `toggle`, `purge` or `status` (the default). |
| `/organism-setup` | Show what setup found and apply it when you say yes. |

`/organism:handoff` and `/organism:incognito` work too, in case another plugin also has a
`/handoff`.

These hooks run without any input from you:

| Hook | When | What it does |
|---|---|---|
| Session notes | After every reply | Adds the exchange to the session's note in the vault. |
| Brief loading | Session start | Loads the repo's `HANDOFF.md`, then lists the sessions that ran after it was written, each linked to its note. |
| Session close | Session end | Adds the final exchange to the note, and runs the doc check if you turned it on. |
| Publish guard | Before `Bash`, connector, web fetch and web search calls | Blocks a push, deploy, upload or web request that contains a term from your blocked list. |

The plugin also puts an `organism` command on the path of Claude's own shell for
maintenance: ask Claude to run `organism help` for the list (`organism doctor` checks
the install).

## Requirements

| Needs | For |
|---|---|
| Claude Code 2.1.295 or later | Everything. Older versions let a command through when the guard times out. |
| git, python3 3.9 or later | Everything else. Standard library only, nothing to install with pip. |
| macOS or Linux | CI runs the tests on both. Alerts show as notifications on macOS. |
| node | The publish guard, once its list has terms. With terms and no node, the guard blocks every call it checks until you install node or empty the list. |

## Optional extras

Each one has a section in [docs/ADVANCED.md](docs/ADVANCED.md).

- [The publish guard](docs/ADVANCED.md#the-publish-guard) blocks a push, deploy, upload
  or web request that would send a term from your private list, such as a project
  codename. For a push it reads every commit the remote doesn't have yet. It's a guard
  against accidents, not against someone set on leaking, and an empty list blocks
  nothing.
- [More than one computer](docs/ADVANCED.md#more-than-one-computer): install the plugin
  on each and sync the vault. Your session notes follow you, each headed with the machine
  it ran on. [docs/LINK-A-DEVICE.md](docs/LINK-A-DEVICE.md) has the steps.
- [Alerts](docs/ADVANCED.md#alerts) go to a macOS notification, and to your phone if you
  set up a Telegram bot.
- [The doc check](docs/ADVANCED.md#the-doc-check-experimental) is experimental and off
  until you turn it on in setup. It keeps a status table on one vault page per repo
  (latest commit, branch, uncommitted files, record count). It doesn't read your prose.

## Tests

```sh
bash claude/hooks/tests/run-all.sh   # prints ALL PASS or exits nonzero
```

CI runs the same command on every push, on macOS and Linux. [CONTRIBUTING.md](CONTRIBUTING.md)
covers working from a clone.

## License

MIT. See [LICENSE](LICENSE).
