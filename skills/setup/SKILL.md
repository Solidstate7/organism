---
name: organism-setup
description: Configure THIS machine for Organism (repo paths, vault and Obsidian, role, schedules, credentials checklist). Wraps organism-setup.py.
disable-model-invocation: true
allowed-tools: Bash(organism setup:*), Bash(~/.local/bin/organism setup:*)
---

Show this machine's current Organism configuration and any proposed cleanup (read-only), then guide the user to apply it. If `organism` is not found, use `~/.local/bin/organism` in every command below.

1. Run the wizard in dry-run mode and show its output verbatim:

   `organism setup --print`

2. Summarize what it reported: the code-repo root and discovered repos, the vault and derived paths (including where session notes will go), whether the experimental doc check is on, the current role, any schedule overrides, any `.obsidian/app.json` cleanup it would make, and the per-repo credential checklist.

3. Tell the user how to apply. The wizard asks before it writes, so it refuses to write without a terminal (as here): applying is for their own terminal.
   - **Interactive (recommended):** `organism setup`
   - **Accept the detected defaults and apply:** `organism setup --yes`
   - **One section only:** `--section repos|vault|pairings|role|schedules|creds` (`pairings` turns on the doc check)

The wizard never stores secrets: each repo's `.env` stays the repo's own; it only validates and points at the repo's template. For the Obsidian **Sync** steps it prints (account, remote-vault pick, encryption password, exact-path excluded folders), those are GUI or keychain steps and must be done in the Obsidian app.
