# Organism: Intent to Implementation

![CI](https://github.com/Solidstate7/organism/actions/workflows/ci.yml/badge.svg)
![License: MIT](https://img.shields.io/badge/license-MIT-green)
![Platform: macOS, Linux](https://img.shields.io/badge/platform-macOS%2C%20Linux-lightgrey)

Organism is a [Claude Code](https://claude.com/claude-code) plugin that keeps the reasons
behind the code that your agent writes. It does three things:

1. It saves every session to your notes folder as readable markdown, as it happens.
2. At the end of a session, a harvest writes each decision as a dated record in the
   repo. It links each record to the commits that carry it out.
3. The next session starts with a short "where we are" brief, loaded automatically.

Organism can also run several sessions at once, one per repo. It starts each session,
stops it after a harvest, and logs both. See
[Running several sessions](#running-several-sessions).

You don't type Organism's commands. You say what you want in a session, and Claude runs
the command. The commands are listed in [docs/COMMANDS.md](docs/COMMANDS.md), for the
agent that runs them.

Organism is plain files, a few hooks and a few scripts. No server, no database, no vector
store: everything opens in a text editor and lives in git or in your own folder.

## Install

```sh
claude plugin marketplace add Solidstate7/organism
claude plugin install organism@organism
```

These two lines are the only commands that you type yourself. Then start a new session
and ask Claude to set up Organism. It shows what it found (your vault and your repos) and
writes nothing until you say yes.

A vault is any folder of markdown, and Obsidian is optional. Setup looks for one in
`~/Projects`, `~/Documents`, Obsidian's iCloud folder and your home folder. If yours is
somewhere else, tell it the path. Until a vault is set, session notes stay off. A harvest
works from the start in any git repo, and anywhere inside the vault once one is set.

After that, the only habit is asking for a harvest at the end of a session. The hooks do
the rest.

To update or remove Organism, ask Claude to do it, and restart Claude Code afterwards. A
session that is already open keeps the old version.

## Why

A coding agent writes code faster than you can review it. The code stays, but your part
of the work doesn't: the corrections, the ideas that you turned down, the reasons why one
option won. A month later the conversation has expired, because Claude Code deletes its
local transcripts after 30 days by default. The commit says `fix auth`. The diff shows
what was built, but not what was rejected or why.

Addy Osmani calls the growing gap between how much code you have and how much of it you
understand [comprehension debt](https://addyosmani.com/blog/comprehension-debt/). Notes
written by hand don't close it, because they go stale silently. Organism writes the
understanding down while it's still in the session, and links it to the code that it
explains. The longer argument is in the essay
[Semantic Sovereignty: Your Agent Writes the Code. Who Keeps the Reasons?](https://medium.com/@vanitasium/semantic-sovereignty-your-agent-writes-the-code-who-keeps-the-reasons-671fa8785adc)

## How it works

![Four layers, stacked: natural language, decision records, commits, code. Solid arrows go down for creation; dashed arrows go up for management.](docs/img/layers.svg)

Read the layers downwards and you see how work is created: what you say becomes a
record, then a commit, then code. Read them upwards and you can start from any line of
code and get back to the sentence in which you asked for it.

| What gets lost | What Organism keeps |
|---|---|
| Why the code exists | A dated decision record for each decision, linked both ways to its commits. See [What a harvest does](#what-a-harvest-does). |
| What was said while building it | Every session, saved to your vault as notes you own. See [Session notes](#session-notes). |

![The loop: a session feeds session notes after every reply. A harvest writes the "where we are" note and the decision records, and the next session loads the note when it starts. Records link both ways to commits. Reconcile, an optional and experimental check, reads the commits, rewrites the half of the doc page that the machine owns, and warns you on a mismatch.](docs/img/loop.svg)

## What a harvest does

You ask for it once, at the end of a session: "harvest this session" is enough. It
commits the work and writes each decision that the session made as a record. Then it links the records to the commits that carry
them out, and rewrites the brief for the next session.

![The eight steps of a harvest in four jobs. Settle the code: read the repo, commit the work first. Record the reasons: find the decisions, write the records, show the draft, commit the records. Link records to commits. Hand over: rewrite the brief. Each step carries the problem it prevents.](docs/img/handoff.svg)

The diagram shows one run, top to bottom, in four jobs. Under each step is the problem
that it prevents. Amber marks the only two places where it may ask you something. Green
marks the approval step, and yellow marks what's optional. The dashed boxes are the steps
that touch git, and they are skipped outside a repository.

### What it writes

| File | What it holds |
|---|---|
| `docs/adr/NNNN-title.md` (or the repo's existing ADR folder) | One decision per file: the situation, the choice, what it costs and enables, the alternatives. Numbered in order and dated. |
| `CLAUDE.md`, under "Decisions" | One line per record, newest first, the 20 most recent plus a link to the rest. Claude Code loads `CLAUDE.md` at every session start, so the decisions are known before the first prompt. |
| git notes on the commits | The link between a record and its commits. Each record lists its commits, and each commit names its record. |
| `HANDOFF.md` | The brief: where things stand, what's live, what comes next. Kept out of git in a repo, written at the vault root outside one, and loaded into the next session automatically. |

A record is never renumbered, rewritten or deleted. A wrong one is superseded by a new
record, and both stay on file. The commits that carry out a decision land before the
record that explains it, so the history reads as cause, then effect.

Only a harvest writes the brief. If you skip one, the next session still gets the
brief as you left it. It also gets links to the saved notes of the sessions that ran
after it.

The brief states facts about the repo, so the harvest checks them. Before writing, it
reads the branch, the recent commits and what has been pushed. After writing, it checks
the new brief against them. A commit the brief names but the repo doesn't have, or a
commit called pushed that isn't, is reported to you. So is an open item the old brief
had and the new one dropped.

### What it won't do

- It writes only the records, the index, the brief and `.gitignore`. In a vault it also
  writes a page that lists every record. After a rebuild of old history, it writes a
  marker file in the records folder. It never edits code.
- It commits on the current branch and never pushes unless you ask for a push. Inside a vault but outside a git repository it writes the records and the brief at
  the vault root and skips the git steps. Outside both it writes nothing and says so.
- It records only this project's decisions. A decision about another project is named in
  the draft and left for that project's own harvest.
- Records describe situations, never account numbers, addresses or names. If the work
  contains a secret, the harvest stops.
- It asks at most two questions: whether to rebuild old history, and whether to
  gitignore generated data. It never asks for approval, because asking for the harvest is
  the approval.
- A session with no decision produces only the brief.

### What you can add

Anything that you say with the request steers the harvest. If you say what comes next,
the brief opens on it: "harvest, next is the queue consumer and then the load test". If
you make a choice and give a reason, it becomes a record as well: "harvest, we keep
SQLite for the job store, because Postgres needs operations work we can't staff".

In a repo with history but no records, the harvest offers once to rebuild them. You can
also ask for the rebuild yourself, for the whole history or for one part of it. Rebuilt
records are marked "reconstructed" and name their sources.
[docs/ADVANCED.md](docs/ADVANCED.md#decision-records-for-old-repos-and-commits-that-explain-themselves)
says how far to trust them.

The links change how you review. Open a record and it lists the commits that carry it
out. You can then read each diff against the reason that it was meant to serve. Start
from a commit and it names its decision, so a change with no decision behind it stands
out.

## Session notes

After every reply, each session is copied into your vault's `_claude-live` folder as
plain markdown, optimised for Obsidian. The note holds your prompts and Claude's replies
in full. Each tool call is folded to one line with the paths that it touched and the
hosts that it reached. Anything outbound gets a mark: a push, a deploy, a web fetch, or a
connector call that sends.

| File | What it holds | Open it when |
|---|---|---|
| `latest.md` | The turn that just finished, in full, then a table of the 30 most recent sessions | You want to see what just happened. Pin it in Obsidian. |
| `sessions/<id>.md` | One session, every turn, rebuilt from the transcript after each reply | You want to see how you got here. |
| `~/.claude/mirror-archive/sessions/<id>.md` | The full note of a long session, outside the vault | You want the early turns of a session over 300 KB. |

A note in the vault stops growing at 300 KB, because Obsidian can't open a vault that
holds notes of several megabytes. Past that size, the vault copy keeps the newest turns,
and its first line names the full note in `~/.claude/mirror-archive`.

Common secrets (API keys, tokens, private keys, passwords in URLs) are redacted before
anything is written. This is best effort, not a guarantee. A project can opt out with an
empty `.organism-no-notes` file. When a call in any other session touches a path under
such a folder, that session's note shows `[excluded]` and no output. Tell Claude to go
incognito, and the work from then on stays out of the notes until you end it. You can
also have it delete what the current session has saved so far.
[docs/SESSION-NOTES.md](docs/SESSION-NOTES.md) shows the format and lists exactly what is
copied.

## Running several sessions

Some people run one Claude Code session per repo and send each one orders from a main
session. Organism calls each repo with its session an agent and lists them in a roster,
a JSON file that you write. Without a roster, none of this runs.

![One main session sends orders to three agents and gets their reports. Each agent is one repo with one session at a time. Stopping an agent runs a harvest, which rewrites the brief in its repo, and the agent's next session starts from that brief. A log records every start, stop, order and report.](docs/img/orchestrate.svg)

You ask the main session for four things:

| You ask to | What happens |
|---|---|
| see the roster | It shows each agent: running, stopped after a harvest, stopped without one, or never started. It also shows how many sessions the agent has had. |
| start an agent | A new session starts in the agent's repo. If the agent ran before, the session starts from the brief that the last one wrote. |
| stop an agent | The agent's session runs a harvest. Once the brief is rewritten, the session ends. |
| see the log | It shows when each session started and stopped, and the first line of each order and report. |

A long session fills its context and gets slower and less careful. A new session that
starts from a short brief has a clean context and still knows where the work stands.
`claude --resume` is different, because it reopens the old session with all of its
context.

Hooks write the log, so a session that you start by hand is in it too. To open and
type into terminals, Organism needs herdr-core, a terminal manager. Without it, starting
an agent prints what to run in the repo, and you close the session yourself. If the
harvest asks for a permission or asks a question, the stop waits and leaves the answer
to you. [docs/ORCHESTRATE.md](docs/ORCHESTRATE.md) explains the roster and every option.

## What runs on its own

Five hooks run without any input from you:

| Hook | When | What it does |
|---|---|---|
| Session notes | After every reply | Adds the exchange to the session's note in the vault. |
| Brief loading | Session start | Loads the nearest `HANDOFF.md` above the folder in which the session started, then lists the sessions that ran after it was written, each linked to its note. |
| Session close | Session end | Adds the final exchange to the note, and runs the doc check if you turned it on. |
| Publish guard | Before `Bash`, connector, web fetch and web search calls | Blocks a push, deploy, upload or web request that contains a term from your blocked list. |
| Agent log | Session start and end, and after `SendMessage` | Logs when an agent's session starts and stops, and the first line of each order and report. Does nothing until you write a roster. |

## Requirements

| Needs | For |
|---|---|
| Claude Code 2.1.295 or later | Everything. Older versions let a command through when the guard times out. |
| git, python3 3.9 or later | Everything else. Standard library only, nothing to install with pip. |
| macOS or Linux | CI runs the tests on both. Alerts show as notifications on macOS. |
| node | The publish guard, once its list has terms. With terms and no node, the guard blocks every call that it checks until you install node or empty the list. |
| herdr-core (optional) | Starting and stopping agents, to open and type into terminals. |

## Optional extras

Each one has a section in [docs/ADVANCED.md](docs/ADVANCED.md).

- [The publish guard](docs/ADVANCED.md#the-publish-guard) keeps a private list of terms,
  such as project codenames. It blocks a push, deploy, upload or web request that would
  send one of them. For a push it reads every commit that the remote doesn't have yet.
  It guards against accidents, not against someone who is set on leaking, and an empty
  list blocks nothing.
- To use [more than one computer](docs/ADVANCED.md#more-than-one-computer), install the
  plugin on each and sync the vault. Your session notes follow you, and each is headed
  with the machine on which it ran. [docs/LINK-A-DEVICE.md](docs/LINK-A-DEVICE.md) has
  the steps.
- [Alerts](docs/ADVANCED.md#alerts) go to a macOS notification, and to your phone if you
  set up a Telegram bot.
- [The doc check](docs/ADVANCED.md#the-doc-check-experimental) is experimental and off
  until you turn it on in setup. It keeps a status table on one vault page per repo
  (latest commit, branch, uncommitted files, record count). It doesn't read your prose.

## Tests

CI runs the tests on every push, on macOS and Linux. [CONTRIBUTING.md](CONTRIBUTING.md)
covers running them and working from a clone.

## License

MIT. See [LICENSE](LICENSE).
