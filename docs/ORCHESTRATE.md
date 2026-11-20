# Running several agents

Some people run several Claude Code sessions at once, one for each repo, and send each one
orders from a main session. `organism orchestrate` keeps that set of sessions in order. It knows
which agents exist, whether each one is running, and how many times it has been restarted
from its brief. Every step goes into a ledger, which you can read back later.

All of this is optional. Nothing here runs until you write a roster, the file that lists your agents.

## An agent's life

An agent is one repo with one Claude Code session at a time. Its life has four steps:

1. `summon` starts a fresh session in the repo. It knows nothing except what the
   repo's brief (`HANDOFF.md`) and `CLAUDE.md` tell it.
2. The session works: it takes orders and sends reports, usually with `SendMessage`.
3. `unsummon` runs `/harvest`, which rewrites the brief with what the session learned,
   and then ends the session with `/exit`.
4. The next `summon` starts a fresh session from that brief.

![One main session sends orders to three agents and gets their reports. Each agent is one repo with one session at a time. Stopping an agent runs /harvest, which rewrites the brief in its repo, and the agent's next session starts from that brief. A log records every start, stop, order and report.](img/orchestrate.svg)

Each fresh session is one generation. A long session fills its context and gets slower and
less careful. Unsummoning it and summoning it again from a short brief gives you a clean session
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
| `effort` | `high` | Filled into `{effort}`. `summon --effort` overrides it once. |
| `brief` | `<repo>/HANDOFF.md` | The file that `/harvest` must rewrite. |
| `handoff` | `true` | `false` closes the agent without `/harvest`, and the ledger says so. |
| `role` | the first line of the repo's `CLAUDE.md` | Shown by `orchestrate list`. |
| `launch` | the roster's `launch` | A launch command for this agent only. |

At the top level, `launch` is the command that `summon` runs in the repo. It can use
`{model}`, `{effort}`, `{name}` and `{repo}`. `herdr` is the herdr-core command (default
`hc`), `shell` is the shell that runs the launch command (default `$SHELL`), and
`handoff_command` is the command that `unsummon` types (default `harvest`).

When an agent's repo holds another agent's repo, the deeper one wins.

## Commands

```
organism orchestrate list
organism orchestrate summon <name> [--effort E] [--dry]
organism orchestrate unsummon <name> [--no-handoff] [--dry]
organism orchestrate ledger [--since 3d|12h|DATE] [--agent NAME] [--json] [--md PATH [--force]]
```

The old names `organism fleet`, `up` and `down` still work.

**`orchestrate list`** shows each agent with its state, its number of generations, the date of
its brief and its role. The state is one of these:

- **live as `<session name>`**: a session is running in the repo now.
- **harvested**: the last session rewrote the brief before it ended.
- **closed**: the last session ended without rewriting the brief.
- **never summoned**: the ledger has nothing for this agent.

**`orchestrate summon`** starts the agent's session. It says first whether the agent is new
or starts again from its brief (a resummon in the ledger), which generation it will be, and which brief the session starts from. If the
agent is already live, it says where and starts nothing. When Claude Code asks whether
you trust the repo's folder, `summon` answers yes, since the roster already names that repo.
It answers no other prompt.

The launch command comes from `launch` in the roster (default
`claude --model {model} --effort {effort}`). Each value is quoted for the shell, so a repo
path with spaces works. For a literal brace in the template, write `{{` or `}}`.

**`orchestrate unsummon`** closes a live agent. It types `/harvest` into the session that runs in
the agent's terminal, waits for the brief to be rewritten, types `/exit`, and closes the
terminal. A session you started by hand in the same repo is left alone. If the brief was
not rewritten, it stops and leaves the session running so you can look. It refuses while
the agent is in the middle of a turn, and it never types over text waiting in the prompt
box. If a command doesn't arrive in the box intact, it clears the box and stops, so a
later Enter can't run half a command. For an agent with `"handoff": false`, or with
`--no-handoff`, it skips `/harvest` and closes the session.

`unsummon` never answers a dialog. If `/harvest` asks for a permission or asks a question,
it stops, shows the screen and leaves the session open for you to answer. The one
exception: when `/exit` shows the dialog about an unsent feedback draft, `unsummon` presses
Esc, which discards the draft and lets the session end.

`--dry` on either command prints the plan and changes nothing.

## herdr-core is optional

`summon` and `unsummon` open and type into terminals through herdr-core, a terminal manager with a
command line called `hc`. Without it:

- `summon` prints the command to run in the agent's repo, for example
  `cd ~/dev/example-api && claude --model opus --effort xhigh`. You open a terminal and
  run it.
- `unsummon` refuses. Do it by hand: run `/harvest` in the session, then `/exit`.

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
| PostToolUse on `SendMessage` | `order` | A message sent to an agent by a session that is not in the roster. |
| | `report` | A message an agent sent to a session that is not in the roster. |
| | `message` | A message from one agent to another. |

Only the first line of a message is kept. It goes through the same secret redaction as the
session notes and is cut at 200 characters. If redaction can't run, the ledger stores a note saying the line
was not recorded.

Only interactive sessions count. A headless `claude -p` run in an agent's repo is a job,
and the ledger leaves it out, along with its messages. A message to a subagent or
teammate inside one session (`main`, or an agent's id) is not recorded either: the ledger
keeps messages sent to an address, a live session's name or an agent in the roster.

`/clear` starts a new session id, so the ledger records the old one's end (`harvest` or
`close`) and a `resummon` for the new one, marked `source: clear`. Compacting keeps the
session and records nothing. If the hook is wired twice (the plugin and an old line in
`settings.json`), each event is still written once.

Orders and reports are recorded where they are sent, not where they arrive. A message
that arrives while a session is busy reaches it in the middle of a turn, where no hook
sees it. The send is always one tool call, which a hook can see. Because of this, a message
sent by something other than a Claude Code session is not in the ledger.

**`orchestrate ledger`** prints the ledger, one event per line. `--since` takes `3d`, `12h` or a
date, `--agent` keeps one agent, and `--json` prints the raw lines. `--md PATH` writes the
events as a Markdown table to a file you name, for example in your vault. It writes a
marker on the first line and refuses to replace a file without one, so it never replaces
a note you wrote. `--force` replaces it anyway.
