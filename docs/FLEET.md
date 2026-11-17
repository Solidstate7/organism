# The fleet

Some people run several Claude Code sessions at once, one for each repo, and send each one
orders from a main session. `organism fleet` keeps that set of sessions in order. It knows
which agents exist, whether each one is running, and how many times it has been restarted
from its brief. Every step goes into a ledger, which you can read back later.

The fleet is optional. Nothing in it runs until you write a roster file.

## An agent's life

An agent is one repo with one Claude Code session at a time. Its life has four steps:

1. **Summon.** A fresh session starts in the repo. It knows nothing except what the
   repo's brief (`HANDOFF.md`) and `CLAUDE.md` tell it.
2. **Work.** The session takes orders and sends reports, usually with `SendMessage`.
3. **Harvest.** `/handoff` rewrites the brief with what the session learned, and `/exit`
   ends the session.
4. **Resummon.** The next fresh session starts from that brief.

Each fresh session is one generation. A long session fills its context and gets slower and
less careful. Harvesting it and resummoning from a short brief gives you a clean session
that still knows where the work stands.

`claude --resume` is different: it reopens the same session with its old context. The
ledger records it as a resume, not a new generation.

## The roster

The roster is a JSON file at `~/.claude/state/organism-fleet.json`. To keep it somewhere
else, set `ORGANISM_FLEET_CONFIG` to its path. A copy to start from is in
[`config/organism-fleet.example.json`](../config/organism-fleet.example.json):

```json
{
  "launch": "claude --model {model} --effort {effort}",
  "herdr": "hc",
  "agents": [
    {"name": "web", "repo": "~/dev/example-web", "role": "the web app"},
    {"name": "api", "repo": "~/dev/example-api", "effort": "xhigh"},
    {"name": "notes", "repo": "~/dev/example-notes", "handoff": false}
  ]
}
```

Each agent needs a `name` (letters, digits, dots, dashes and underscores) and a `repo`.
The other fields are optional:

| Field | Default | What it does |
|---|---|---|
| `model` | `opus` | Filled into `{model}` in the launch command. |
| `effort` | `high` | Filled into `{effort}`. `fleet up --effort` overrides it once. |
| `brief` | `<repo>/HANDOFF.md` | The file a harvest must rewrite. |
| `handoff` | `true` | `false` closes the agent without `/handoff`, and the ledger says so. |
| `role` | the first line of the repo's `CLAUDE.md` | Shown by `fleet list`. |
| `launch` | the roster's `launch` | A launch command for this agent only. |

At the top level, `launch` is the command `fleet up` runs in the repo. It can use
`{model}`, `{effort}`, `{name}` and `{repo}`. `herdr` is the herdr-core command (default
`hc`), `shell` is the shell that runs the launch command (default `$SHELL`), and
`handoff_command` is the command a harvest types (default `handoff`).

When an agent's repo holds another agent's repo, the deeper one wins.

## Commands

```
organism fleet list
organism fleet up <name> [--effort E] [--dry]
organism fleet down <name> [--no-handoff] [--dry]
organism fleet ledger [--since 3d|12h|DATE] [--agent NAME] [--json] [--md PATH [--force]]
```

`summon` and `resummon` are other names for `up`, and `harvest` is another name for `down`.

**`fleet list`** shows each agent with its state, its number of generations, the date of
its brief and its role. The state is one of these:

- **live as `<session name>`**: a session is running in the repo now.
- **harvested**: the last session rewrote the brief before it ended.
- **closed**: the last session ended without rewriting the brief.
- **never summoned**: the ledger has nothing for this agent.

**`fleet up`** starts the agent's session. It says first whether this is a summon or a
resummon, which generation it will be, and which brief the session starts from. If the
agent is already live, it says where and starts nothing.

**`fleet down`** harvests a live agent. It types `/handoff` into the session, waits for the
brief to be rewritten, types `/exit`, and closes the terminal. If the brief was not
rewritten, it stops and leaves the session running so you can look. It refuses while the
agent is in the middle of a turn, and it never types over text waiting in the prompt box. For an agent with `"handoff": false`, or with
`--no-handoff`, it skips `/handoff` and closes the session.

`--dry` on either command prints the plan and changes nothing.

## herdr-core is optional

`up` and `down` open and type into terminals through herdr-core, a terminal manager with a
command line called `hc`. Without it:

- `fleet up` prints the command to run in the agent's repo, for example
  `cd ~/dev/example-api && claude --model opus --effort xhigh`. You open a terminal and
  run it.
- `fleet down` refuses. Harvest by hand: run `/handoff` in the session, then `/exit`.

The ledger works the same either way, because hooks write it, not the commands.

## The ledger

The ledger is a file of JSON lines at `~/.claude/state/fleet-ledger.jsonl` (set
`ORGANISM_FLEET_LEDGER` to move it). Lines are only ever added, and only you can read the
file. Three hooks write it, so sessions you start by hand are recorded too:

| Hook | Event | What is recorded |
|---|---|---|
| SessionStart in an agent's repo | `summon` | The session id, generation 1, and the brief it starts from: path, time and hash. |
| | `resummon` | The same, plus the session it continues. |
| | `resume` | `claude --resume` reopened a session. No new generation. |
| SessionEnd in an agent's repo | `harvest` | The brief was rewritten during the session. Its new time and hash. |
| | `close` | The brief was not rewritten, and why: handoff is off for this agent, or the session ended without it. |
| PostToolUse on `SendMessage` | `order` | A message sent to an agent from outside the fleet. |
| | `report` | A message an agent sent to someone outside the fleet. |
| | `message` | A message from one agent to another. |

Only the first line of a message is kept. It goes through the same secret redaction as the
session notes and is cut at 200 characters. If redaction can't run, the ledger stores a note saying the line
was not recorded.

Only interactive sessions count. A headless `claude -p` run in an agent's repo is a job,
and the ledger leaves it out.

Orders and reports are recorded where they are sent, not where they arrive. A message
that arrives while a session is busy reaches it in the middle of a turn, where no hook
sees it. The send is always one tool call, which a hook can see. Because of this, a message
sent by something other than a Claude Code session is not in the ledger.

**`fleet ledger`** prints the ledger, one event per line. `--since` takes `3d`, `12h` or a
date, `--agent` keeps one agent, and `--json` prints the raw lines. `--md PATH` writes the
events as a Markdown table to a file you name, for example in your vault. It writes a
marker on the first line and refuses to replace a file without one, so it never replaces
a note you wrote. `--force` replaces it anyway.
