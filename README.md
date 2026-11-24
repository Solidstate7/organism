# Organism: Intent to Implementation

![CI](https://github.com/Solidstate7/organism/actions/workflows/ci.yml/badge.svg)
![License: MIT](https://img.shields.io/badge/license-MIT-green)
![Platform: macOS, Linux](https://img.shields.io/badge/platform-macOS%2C%20Linux-lightgrey)

Organism is a [Claude Code](https://claude.com/claude-code) plugin that keeps the
reasons behind the code that your agent writes. It does three things:

1. It saves every session to your notes folder as readable markdown, as it happens.
2. At the end of a session, a harvest writes each decision as a dated record in the
   repo. It links each record to the commits that implement it.
3. The next session starts with a short "where we are" brief, loaded automatically.

Organism can also run several sessions at once, one per repo. It starts each session,
stops it after a harvest, and logs both. See [Running several
sessions](#running-several-sessions).

You don't type Organism's commands. You say what you want in a session, and Claude runs
the command. The commands are listed in [docs/COMMANDS.md](docs/COMMANDS.md), for the
agent that runs them.

Organism is made of plain files, a few hooks and a few scripts. It needs no server,
database or vector store. You can open every file in a text editor, and the files are
kept in git or in your own folder.

## Install

```sh
claude plugin marketplace add Solidstate7/organism
claude plugin install organism@organism
```

These two lines are the only commands that you type yourself. Then start a new session
and ask Claude to set up Organism. Claude shows you what it found (your vault and your
repos) and writes nothing until you say yes.

A vault is any folder of markdown files, and you don't need Obsidian. During setup,
Claude looks for one in `~/Projects`, `~/Documents`, Obsidian's iCloud folder and your
home folder. If yours is somewhere else, tell Claude the path. Until you set a vault,
Organism writes no session notes. You can harvest in any git repo from the start, and
anywhere inside the vault once you have set one.

At the end of a session, ask Claude for a harvest. The hooks run on their own.

To update or remove Organism, ask Claude to do it, and restart Claude Code afterwards. A
session that is already open keeps the old version.

## Why

A coding agent writes code faster than you can review it. The code is saved, but your
part of the work isn't. That part is the corrections, the ideas that you turned down and
the reasons why you chose one option. A month later the conversation is gone, because
Claude Code deletes its local transcripts after 30 days by default. The commit message
says `fix auth`. The diff shows what was built, but not what you rejected or why.

Addy Osmani calls the growing gap between how much code you have and how much of it you
understand [comprehension debt](https://addyosmani.com/blog/comprehension-debt/). Notes
that you write by hand don't close the gap, because they go out of date without anyone
noticing. Organism writes your reasons down while you are still in the session, and
links them to the code that they explain. The longer argument is in the essay [Semantic
Sovereignty: Your Agent Writes the Code. Who Keeps the
Reasons?](https://medium.com/@vanitasium/semantic-sovereignty-your-agent-writes-the-code-who-keeps-the-reasons-671fa8785adc)

## How it works

![Four layers, stacked: natural language, decision records, commits, code. Solid arrows go down for creation; dashed arrows go up for management.](docs/img/layers.svg)

Read the layers downwards and you see how work is created: what you say becomes a
record, then a commit, then code. Read them upwards and you can start from any line of
code and get back to the sentence in which you asked for it.

| What gets lost | What Organism keeps |
|---|---|
| Why the code exists | A dated record of each decision, linked both ways to its commits. See [What a harvest does](#what-a-harvest-does). |
| What was said while building it | Every session, saved to your vault as notes that you own. See [Session notes](#session-notes). |

![The loop: after every reply, the session is saved to the session notes. A harvest writes the "where we are" note and the decision records, and the next session loads the note when it starts. Records link both ways to commits. Reconcile is an optional and experimental check. It reads the commits, rewrites the half of the doc page that is written by machine, and warns you when something doesn't match.](docs/img/loop.svg)

## What a harvest does

You ask for it once, at the end of a session: "harvest this session" is enough. It
commits the work and writes a record of each decision made in the session. Then it links
each record to the commits that implement it, and rewrites the brief for the next
session.

![The eight steps of a harvest in four jobs. Settle the code: read the repo, commit the work first. Record the reasons: find the decisions, write the records, show the draft, commit the records. Link records to commits. Hand over: rewrite the brief. Each step carries the problem it prevents.](docs/img/handoff.svg)

The diagram shows one run from top to bottom, in four jobs. Under each step is the
problem that it prevents. The two places in amber are the only ones where the harvest
may ask you something. The approval step is green, and the optional parts are yellow.
The dashed boxes are the steps that use git. Outside a repository, the harvest skips
them.

### What it writes

| File | What it holds |
|---|---|
| `docs/adr/NNNN-title.md` (or the repo's existing ADR folder) | One decision per file: the situation, who made the choice, the choice, what it costs and what it makes possible, the alternatives. Numbered in order and dated. |
| `CLAUDE.md`, under "Decisions" | One line per record, newest first, the 20 most recent plus a link to the rest. Claude Code loads `CLAUDE.md` at every session start, so Claude knows the decisions before your first prompt. |
| git notes on the commits | The link between a record and its commits. Each record lists its commits, and each commit names its record. Git doesn't push notes, so in a clone Organism reads the links from the list in each record. |
| `HANDOFF.md` | The brief: where things stand, what's live, what comes next. Kept out of git in a repo, written at the vault root outside one, and loaded into the next session automatically. |

A record is never renumbered, rewritten or deleted. A wrong record is superseded by a
new one, and both are kept. The commits for a decision are made before the record that
explains them, so they come first in the history.

Only a harvest writes the brief. If you skip a harvest, the next session still gets the
last brief. It also gets links to the saved notes of the sessions that ran after that
brief was written.

The brief has facts about the repo in it, so the harvest checks them. Before it writes,
it reads the branch, the recent commits and what has been pushed. Afterwards it compares
the new brief with them. You are told when the brief names a commit that the repo
doesn't have, or says that a commit is pushed when it isn't. The same goes for an open
item in the old brief that is missing from the new one.

### What it won't do

- It writes only the records, the index, the brief and `.gitignore`. In a vault it also
  writes a page that lists every record. After a rebuild of old history, it writes a
  marker file in the records folder. It never edits code.
- It commits on the current branch and pushes only when you ask for it. Inside a vault
  but outside a git repository it writes the records and the brief at the vault root and
  skips the git steps. Outside both it writes nothing and says so.
- It records only this project's decisions. A decision about another project is named in
  the draft and left for that project's own harvest.
- Records describe situations and leave out account numbers, addresses and names. If the
  work contains a secret, the harvest stops.
- It asks at most two questions: whether to rebuild old history, and whether to
  gitignore generated data. It never asks for approval. You gave it when you requested
  the harvest.
- If a session has no decision, the harvest writes only the brief.

A session can hold several decisions, and the harvest writes one record for each. A
choice counts as one decision when you could undo it and keep the others. It also needs
commits of its own, so two choices that are in the same commits become one record. If
you chose something that isn't built yet, the harvest still writes the record and says
in it that nothing is built.

Claude divides the session into decisions, and it can make mistakes. The draft shows
each record with its commits. If the division is wrong, tell Claude to merge two
records, divide one or move a commit. Claude then fixes the records and the links. Under
"Changed without a decision", the draft also lists the commits that belong to no record.
Those are routine edits, fixes, and choices that Claude made without asking you. The
records hold only the decisions that you and Claude talked about, so they are not a full
account of the session. The list under that heading shows you the rest.

### What you can add

Claude uses anything that you say with the request. If you tell it what comes next, the
brief starts with that: "harvest, next is the queue consumer and then the load test". If
you make a choice and give a reason, Claude makes a record of it as well. You could say
"harvest, we keep SQLite for the job store, because Postgres needs operations work we
can't staff".

In a repo with history but no records, the harvest offers once to rebuild them. You can
also ask for the rebuild yourself, for the whole history or for one part of it. Rebuilt
records are marked "reconstructed" and name their sources.
[docs/ADVANCED.md](docs/ADVANCED.md#decision-records-for-old-repos-and-commits-that-explain-themselves)
says how far to trust them.

You can use the links when you review code. Each record lists the commits that implement
it, so you can read a diff next to the reason for it. Each commit names its record, so
you can also see which changes have no decision behind them. Claude uses the links too.
When a session starts, a hook tells Claude to read the record behind a piece of code
before changing that code.

## Session notes

After every reply, each session is copied into your vault's `_claude-live` folder as
plain markdown, formatted for Obsidian. The note has your prompts and Claude's replies
in full. Each tool call is folded to one line with the paths that it used and the hosts
that it contacted. Outbound calls are marked: a push, a deploy, a web fetch, or a
connector call that sends.

| File | What it holds | Open it when |
|---|---|---|
| `latest.md` | The turn that just finished, in full, then a table of the 30 most recent sessions | You want to see what just happened. Pin it in Obsidian. |
| `sessions/<id>.md` | One session, every turn, rebuilt from the transcript after each reply | You want to see how you got here. |
| `~/.claude/mirror-archive/sessions/<id>.md` | The full note of a long session, outside the vault | You want the early turns of a session over 300 KB. |

A note in the vault is kept to 300 KB, because Obsidian can't open a vault that has
notes of several megabytes in it. Past that size, the vault copy has only the newest
turns, and its first line names the full note in `~/.claude/mirror-archive`.

Organism redacts common secrets (API keys, tokens, private keys, passwords in URLs)
before it writes anything, but it can miss some. To keep a project out of the notes, put
an empty `.organism-no-notes` file in its folder. When a call in any other session uses
a path under that folder, that session's note shows `[excluded]` and no output. If you
tell Claude to go incognito, the work from then on stays out of the notes until you turn
it off. You can also have Claude delete what the current session has saved so far.
[docs/SESSION-NOTES.md](docs/SESSION-NOTES.md) shows the format and lists exactly what
is copied.

## Running several sessions

Some people run one Claude Code session per repo and send each one orders from a main
session. In Organism, a repo together with its session is called an agent. You list your
agents in a roster, a JSON file that you write. Without a roster, none of this runs.

![One main session sends orders to three agents and gets their reports. Each agent is one repo with one session at a time. Stopping an agent runs a harvest, which rewrites the brief in its repo, and the agent's next session starts from that brief. The history records every start, stop, order and report.](docs/img/orchestrate.svg)

Organism starts and stops agents on its own. When you ask the main session to send an
order to an agent that isn't running, it starts the agent first. Before that, Organism
checks how much memory the machine has available. If there isn't enough for one more
session, it stops the agent that has been idle longest. It runs a harvest first, so the
brief says where that session stopped.

Organism never stops an agent that is working. An agent that waits for you to type
counts as idle, so Organism can stop it when there is too little memory. In the roster
you can turn the automatic stopping off, or limit how many agents run at once.

You can still ask the main session for four things yourself:

| You ask to | What happens |
|---|---|
| see the roster | It shows each agent: running, stopped after a harvest, stopped without one, or never started. It also shows how many sessions the agent has had. |
| start an agent | A new session starts in the agent's repo. If the agent ran before, the session starts from the brief that the last one wrote. |
| stop an agent | The agent's session runs a harvest. Once the brief is rewritten, the session ends. |
| see the history | It shows when each session started and stopped, and the first line of each order and report. |

In a long session the context fills up, and Claude gets slower and less careful. A new
session that starts from a short brief has a clean context, and Claude still knows where
the work stands. `claude --resume` is different, because it reopens the old session with
all of its context.

Hooks write the history, so the sessions that you start by hand are in it too. To open
terminals and type into them, Organism needs herdr-core, which manages terminals. When
you start an agent without it, Organism prints what to run in the repo, and you close
the session yourself. If the harvest asks for a permission or has a question, Organism
waits and leaves the answer to you. [docs/ORCHESTRATE.md](docs/ORCHESTRATE.md) explains
the roster and every option.

## What runs on its own

Five hooks run without any input from you:

| Hook | When | What it does |
|---|---|---|
| Session notes | After every reply | Adds the exchange to the session's note in the vault. |
| Brief loading | Session start | Loads the nearest `HANDOFF.md` above the folder in which the session started, then lists the sessions that ran after it was written, each linked to its note. In a project with decision records, it also tells Claude to read the record behind code before changing it. |
| Session close | Session end | Adds the final exchange to the note, and runs the doc check if you turned it on. |
| Publish guard | Before `Bash`, connector, web fetch and web search calls | Blocks a push, deploy, upload or web request that contains a term from your blocked list. |
| Agent log | Session start and end, and after `SendMessage` | Logs when an agent's session starts and stops, and the first line of each order and report. Does nothing until you write a roster. |

## Requirements

| Needs | For |
|---|---|
| Claude Code 2.1.295 or later | Everything. Older versions let a command through when the guard times out. |
| git, python3 3.9 or later | Everything else. Standard library only, nothing to install with pip. |
| macOS or Linux | CI runs the tests on both. Alerts show as notifications on macOS. |
| node | The publish guard, once its list has terms. If the list has terms and node is missing, the guard blocks every call that it checks until you install node or empty the list. |
| herdr-core (optional) | Starting and stopping agents, to open and type into terminals. |

## Optional extras

Each one has a section in [docs/ADVANCED.md](docs/ADVANCED.md).

- [The publish guard](docs/ADVANCED.md#the-publish-guard) uses a private list of terms
  that you write, such as project codenames. It blocks a push, deploy, upload or web
  request that would send one of them. For a push it reads every commit that the remote
  doesn't have yet. It is meant for accidents and won't stop someone who wants to leak
  something. With an empty list it blocks nothing.
- To use [more than one computer](docs/ADVANCED.md#more-than-one-computer), install the
  plugin on each and sync the vault. You then have your session notes on every computer,
  and each one starts with the name of the machine on which the session ran.
  [docs/LINK-A-DEVICE.md](docs/LINK-A-DEVICE.md) has the steps.
- [Alerts](docs/ADVANCED.md#alerts) are shown as macOS notifications, and sent to your
  phone if you set up a Telegram bot.
- [The doc check](docs/ADVANCED.md#the-doc-check-experimental) is experimental and off
  until you turn it on in setup. It keeps a status table on one vault page per repo
  (latest commit, branch, uncommitted files, record count). It doesn't read the text
  that you write.

## Tests

CI runs the tests on every push, on macOS and Linux. [CONTRIBUTING.md](CONTRIBUTING.md)
explains how to run them and how to work from a clone.

## License

MIT. See [LICENSE](LICENSE).
