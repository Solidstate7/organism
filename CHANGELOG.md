# Changelog

All notable changes to Organism are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- A diagram of one main session with three agents, their briefs and the log, in the
  README and in [docs/ORCHESTRATE.md](docs/ORCHESTRATE.md).
- `organism adr-link link-session --list` prints the work commits since the last harvest,
  each with the record it is linked to or `none`. It writes nothing.
- At session start, a project whose `CLAUDE.md` has a "Decisions" index gets one more
  paragraph. It tells Claude to read the record behind code before changing that code.

### Changed

- `organism fleet` is now `organism orchestrate`, `list` is now `roster`, `up` is now
  `summon`, and `down` is now `unsummon`. The commands do the same work as before, and
  the old names still work.
- `docs/FLEET.md` is now `docs/ORCHESTRATE.md`.
- These names stay as they were: the roster `~/.claude/state/organism-fleet.json`, the
  log `~/.claude/state/fleet-ledger.jsonl`, the `ORGANISM_FLEET_*` variables, and the
  events in the log.
- The README no longer lists commands. It says what to ask Claude for, and the commands
  are in [docs/COMMANDS.md](docs/COMMANDS.md) for the agent that runs them. The two
  install lines stay.
- The README says to ask for a harvest at the end of every session, not only after a
  session that made a decision.
- A harvest that finds several decisions links each record to its own commits. Before, it
  linked every commit of the session to every new record.
- The harvest decides what one decision is with three checks: the choice can be undone
  alone, it has its own commits, and a choice with no commit yet still gets a record.
  Two choices in the same commits become one record.
- The harvest draft shows each record's commits and who made the choice. It lists the
  commits that belong to no record under "Changed without a decision".
- A table written by `ledger --md` has a new first line and the heading "Ledger". A table
  with the old first line is still replaced without `--force`.

## [0.4.0] - 2026-11-20

### Changed

- `/handoff` is now `/harvest`, and `/organism:handoff` is now `/organism:harvest`. The
  command does the same work as before. The old name is gone, so type the new one at the
  end of a session.
- `organism fleet down` types `/harvest` into the agent's session. A roster that sets
  `handoff_command` keeps its own value.
- These names stay as they were: the brief `HANDOFF.md`, `organism handoff-state`, the
  roster field `"handoff": false`, and the `--no-handoff` flag.

## [0.3.0] - 2026-11-20

The first release since 0.1.2. It also brings the changes listed under 0.2.0, which was
never published on its own.

### Added

- `organism fleet`, for running several Claude Code sessions at once, one per repo. Each
  repo with its session is an agent. `fleet up` starts a new session in the agent's
  repo. `fleet down` runs `/handoff` in it and then ends it with `/exit`. The next
  `fleet up` starts from the brief that `/handoff` wrote.
  - `fleet list`: each agent's state (running, stopped after `/handoff` with the date of
    its brief, stopped without it, or never started), its number of sessions and its role.
  - `fleet up <name>` says first whether the agent is new or starts again from its brief.
    `summon` and `resummon` are other names for it. When Claude Code asks whether to
    trust the repo's folder, it answers yes, and it answers no other prompt. Values in
    the launch command are quoted for the shell, so a repo path with spaces works.
  - `fleet down <name>` stops if the brief was not rewritten. `harvest` is another name
    for it. An agent with `"handoff": false` in the roster, or `--no-handoff`, is closed
    without `/handoff`. It acts only on the session in the agent's own terminal. It never
    answers a dialog: if `/handoff` asks for a permission or a question, it stops and
    leaves the session open. The one key it presses unasked is Esc, when `/exit` asks
    about an unsent feedback draft.
  - `fleet ledger`: the log, filtered with `--since` and `--agent`, as JSON with
    `--json`, or written as a Markdown table to a file you name with `--md`. It never
    replaces a file it didn't write unless you add `--force`.
  - `--dry` on `up` and `down` prints the plan and changes nothing.
- The fleet roster: a JSON file listing each agent's name, repo, model, effort, brief and
  role. `config/organism-fleet.example.json` is a copy to start from, and
  [docs/ORCHESTRATE.md](docs/ORCHESTRATE.md) explains each field. Without a roster, nothing in the
  fleet runs.
- The fleet ledger, a log at `~/.claude/state/fleet-ledger.jsonl` that only grows. Hooks
  write it, so sessions started by hand count too. At session start it records `summon`
  for a new agent, `resummon` for a session that starts from the last brief, or `resume`
  for a reopened one. A resummon also records the brief's path, time and hash. At session end it
  records `harvest` when the brief was rewritten, or `close` and the reason. After
  `SendMessage`, it records an order, a report or a message between agents. Only the
  first line of a message is kept, after secret redaction. Headless runs and messages to
  a subagent inside one session are left out. After `/clear`, the new session counts as
  a new start. A hook wired twice still writes each event once, and a roster it can't
  read never fails a session.
- herdr-core is optional. `fleet up` and `fleet down` use it to open and type into
  terminals. Without it, `up` prints the command to run in the repo, and `down` refuses
  and says how to run `/handoff` and `/exit` by hand.

### Changed

- A session note in the vault stops growing at 300 KB. Obsidian can't open a vault that
  holds notes of several megabytes. A longer session keeps its newest turns in the vault,
  under a first line that names the full note. The full note goes to
  `~/.claude/mirror-archive/sessions/<id>.md` (`CLAUDE_MIRROR_ARCHIVE_DIR` in
  `paths.env` moves it). The reviews read the full note through that line, and
  `/incognito purge` deletes the full note too. `obsidian-mirror.py --trim-oversize`
  applies the cap to notes that were written before it. A reply that quotes a turn inside
  a code block doesn't count as a turn, so the kept part starts at a real one.
- The README says what the fleet commands do, how to update the plugin, and how
  `/handoff` checks the brief. Its sentences are shorter and plainer.
- CONTRIBUTING's release steps match how a release is made now: a version bump, a
  changelog entry and a tag before the push.

### Fixed

- Session notes check every path that a tool call touched for `.organism-no-notes`, not
  only the first five that the note shows. A Glob or Grep pattern that starts at `/` or
  `~` counts as a path.
- A connector call is marked outbound when the first verb in its name writes (create,
  reply, forward). A name with no such verb counts as outbound when it writes all the
  same (batch, execute, import, sync).
- `/handoff` catches a commit hash that the new brief makes up, also in a list item that
  starts with the hash. A run from a subfolder of a repo no longer fails, and it stages
  the whole repo instead of only that subfolder.
- The decisions page keeps a note that you added in the place where you put it.
- `organism uninstall` after an install from a clone removes the fleet hook from
  `settings.json` too. Before, it stayed behind and failed at every session start once
  the clone was gone.
- Tests: the brief-loading test no longer reads a pairings file that sits next to the
  code outside its temporary folder, and the timing test counts turns in the full note
  instead of the capped one.

## [0.2.0] - 2026-10-19

### Added

- `organism handoff-state`: the facts a brief may state (root, branch, HEAD, upstream,
  ahead and behind, pushed or not, dirty files, the last commits, every hash the brief
  names with whether it exists and is pushed, the sessions since the brief), and
  `--check`, which names a hash the new brief invents or calls pushed while the upstream
  lacks it and lists the open threads the old brief dropped. `/handoff` reads the state
  before writing the brief and runs the check after.
- `organism adr-index next|moc|trim`: the records folder and the next number, the
  decisions MOC regenerated from every record (an existing page keeps its header, its
  sections and the lines it already has), and the twenty-entry trim of the `CLAUDE.md`
  index.
- Session notes say what each tool call touched: the paths it read or wrote, the hosts it
  reached, the browser's own description of its action marked "as described by the
  session", the connector's name, and `outbound: remote` or `git: local`. A call that
  touched a path under a folder holding `.organism-no-notes` shows `[excluded]` and no
  output, so one project's opt-out holds inside another's notes.

### Changed

- One brief per repo or vault: `/handoff` works from the git top level, else the vault
  root (the configured vault, or the nearest folder above holding `.obsidian` or
  `docs/adr`), and outside both it writes nothing and says so. A vault that is the home
  folder counts as none.
- `/handoff` says in one line when a session wrote and decided nothing, and still writes
  the brief. The skill is 20 KB instead of 35; the retro reconstruction lives in its own
  file, read only when it is used.
- The README's hook table says the brief loader reads the nearest `HANDOFF.md`; its
  "writes only" line names the MOC page and the retro marker.

## [0.1.2] - 2026-10-16

### Changed

- The plugin is the one install path in the docs. The README Quickstart is the two
  install commands and `/organism-setup`. The clone install and its background jobs are
  gone from the README and the docs (the code stays for now), `docs/LINK-A-DEVICE.md`
  covers using the plugin on another computer, and CONTRIBUTING shows how to work on a
  clone with `claude --plugin-dir`.
- `/organism-setup` applies the plan when you say yes in the chat (it runs
  `organism setup --yes`), so setup needs no terminal command. `organism setup
  --doc-check` turns on the experimental doc check.
- In a plugin install, `organism help`, `organism addon` and the setup output no longer
  point to a clone, and setup skips the role and schedule questions, which only steer the
  clone's background jobs.
- The README merges "What you get" and "What it promises" into one list, and the docs no
  longer describe adding your own scheduled jobs.

## [0.1.1] - 2026-10-16

### Changed

- The marketplace and plugin descriptions say what Organism is for, and `plugin.json`
  points at an icon (`docs/img/icon.png`) for Anthropic's directory.
- Tests: the session-end check allows a slow CI runner (1 s instead of 300 ms, still well
  inside the hook's budget) and never reads a half-written file; the `bin/` and setup
  checks pass in a clone in daily use (stray cache files, a clone's own pairings file).

## [0.1.0] - 2026-10-16

First public release.

### Added

- **Claude Code plugin.** Organism installs as a plugin (`.claude-plugin/plugin.json`,
  a one-plugin marketplace, `hooks/hooks.json`). A clone installed with `bootstrap.sh`
  still works, and `organism migrate --to-plugin` moves a clone install over (with
  `--to-symlink` to go back).
- **Session notes.** Every Claude Code session is saved as a readable markdown note in
  your vault, updated after every reply and finished when the session ends. Thinking
  blocks are never written, and secrets that look like keys, tokens or passwords are
  redacted before anything reaches disk.
- **`/handoff`.** Commits outstanding work, writes decision records (ADRs) for the
  decisions the session made, links each record to the commits that carry it out, and
  rewrites a short `HANDOFF.md` brief that the next session loads on start.
- **`/incognito`.** Pauses, resumes or deletes the saved copy of the current session.
- **`/organism-setup`** (`/setup` in a clone install). Finds your vault and repos and
  writes the one config file, asking before it writes anything.
- **Publish guard.** Blocks a `git push`, deploy, upload or web request that would send a
  term from your private blocked-words list. It fails closed: if it is set up but cannot
  run, the command is blocked.
- **Decision links.** `adr-link.sh` links commits to records with git notes, reads `ADR:`
  trailers, backfills links from history and keeps the work bounded on large repositories.
- **`organism` CLI.** Setup, status, doctor, update, uninstall and migrate, plus the
  helpers the skills use.
- **Doc check (experimental, off by default).** Keeps a status table per repo current in
  the vault and warns when a check you added fails.
- **Tests and CI.** One command, `bash claude/hooks/tests/run-all.sh`, runs the static
  checks and every test; CI runs it on macOS and Linux.
- Security policy, code of conduct, contributing guide, and issue and pull request
  templates.
