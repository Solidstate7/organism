# Commands

This page is the reference for the agent that runs Organism's commands. You don't need
to type any of them yourself: say what you want in a session, and Claude runs the
command. The [README](../README.md) says what each one does in plain words.

## In a session

| Command | What it does |
|---|---|
| `/harvest` | Commit the work, write the decision records, link them to commits, rewrite the brief. |
| `/harvest <note>` | The same run, with the note steering the brief. Any text after the command is the note. |
| `/harvest --retro` | Rebuild past decisions from the repo's history now, instead of waiting for the offer. Text after the flag narrows it: a subsystem, a date range, a suspected gap. |
| `/incognito` | Control the session notes: `on`, `off`, `toggle`, `purge` or `status` (the default). |
| `/organism-setup` | Show what setup found and apply it when the operator says yes. |

`/organism:harvest` and `/organism:incognito` work too, in case another plugin also has a
`/harvest`. Until 0.4.0, `/harvest` was called `/handoff`.

A note that says what's next shapes the brief, and the next session opens on it:

```
/harvest next: migrate the queue consumer, then re-run the load test
```

A note that makes a choice and gives a reason becomes a record as well:

```
/harvest we keep SQLite for the job store; Postgres adds an ops burden we can't staff
```

## In Claude's shell

The plugin puts an `organism` command on the path of Claude's own shell. `organism help`
lists everything, and `organism doctor` checks the install.

| Command | What it does |
|---|---|
| `organism orchestrate roster` | Shows each agent: running, stopped after `/harvest`, stopped without it, or never started. It also shows how many sessions the agent has had. |
| `organism orchestrate summon <name>` | Starts a new session in the agent's repo. If the agent ran before, the session starts from the brief that the last one wrote. |
| `organism orchestrate unsummon <name>` | Runs `/harvest` in the agent's session, checks that the brief was rewritten, and then ends the session with `/exit`. |
| `organism orchestrate ledger` | Shows the log: when each session started and stopped, and the first line of each order and report. |

[ORCHESTRATE.md](ORCHESTRATE.md) explains the roster and every option.

## Updating

```sh
claude plugin marketplace update organism
claude plugin update organism@organism
```

Restart Claude Code afterwards. A session that is already open keeps the old version.

## Tests

```sh
bash claude/hooks/tests/run-all.sh   # prints ALL PASS or exits nonzero
```
