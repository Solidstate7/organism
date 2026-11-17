# Changelog

All notable changes to Organism are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [0.3.0] - Unreleased

### Added

- `organism fleet`, for running several Claude Code sessions at once, one per repo. An
  agent's life has four steps: summon (a fresh session in the repo), work (orders in,
  reports out), harvest (`/handoff` rewrites the brief, then `/exit`) and resummon (the
  next fresh session, which starts from that brief). Each fresh session is one generation.
  - `fleet list`: each agent's state (live, harvested with the date of its brief, closed,
    or never summoned), its number of generations and its role.
  - `fleet up <name>` summons an agent, or resummons it when it has lived before. It
    says which first. `summon` and `resummon` are other names for it.
  - `fleet down <name>` harvests a live agent and stops if the brief was not rewritten.
    `harvest` is another name for it. An agent with `"handoff": false` in the roster, or
    `--no-handoff`, is closed without `/handoff`.
  - `fleet ledger`: the log, filtered with `--since` and `--agent`, as JSON with
    `--json`, or written as a Markdown table to a file you name with `--md`. It never
    replaces a file it didn't write unless you add `--force`.
  - `--dry` on `up` and `down` prints the plan and changes nothing.
- The fleet roster: a JSON file listing each agent's name, repo, model, effort, brief and
  role. `config/organism-fleet.example.json` is a copy to start from, and
  [docs/FLEET.md](docs/FLEET.md) explains each field. Without a roster, nothing in the
  fleet runs.
- The fleet ledger, a log at `~/.claude/state/fleet-ledger.jsonl` that only grows. Hooks
  write it, so sessions started by hand count too. Session start records a summon,
  resummon or resume, with the session a resummon continues and the brief it starts from
  (path, time and hash). Session end records a harvest when the brief was rewritten, or
  a close and why. After `SendMessage`, it records an order, a report or a message
  between agents. Only the first line of a message is kept, after secret redaction.
- herdr-core is optional. `fleet up` and `fleet down` use it to open and type into
  terminals. Without it, `up` prints the command to run in the repo, and `down` refuses
  and says how to harvest by hand.

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
- Session notes in the vault are capped at 300 KB: a longer session keeps its newest
  turns there under a header line that names the full note, which is written outside
  the vault to `~/.claude/mirror-archive/sessions/<id>.md` (`CLAUDE_MIRROR_ARCHIVE_DIR`
  in `paths.env` moves it). Obsidian's indexer runs out of memory on multi-megabyte
  notes and the window never opens; a 5 MB note did that on 2026-10-26. The reviews
  read the full note through the header line, `/incognito purge` removes the archived
  copy too, and `obsidian-mirror.py --trim-oversize` applies the cap to notes already
  written.

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
