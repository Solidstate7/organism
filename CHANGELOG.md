# Changelog

All notable changes to Organism are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

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
