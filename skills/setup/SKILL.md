---
name: organism-setup
description: Configure THIS machine for Organism (vault and Obsidian, repo paths, credentials checklist). Shows the plan, then applies it when you say yes. Wraps organism-setup.py.
disable-model-invocation: true
allowed-tools: Bash(organism setup:*), Bash(~/.local/bin/organism setup:*)
---

Show this machine's Organism configuration and the plan setup would apply, then apply it only if the user says yes. If `organism` is not found, use `~/.local/bin/organism` in every command below.

1. Run the wizard in dry-run mode and show its output verbatim:

   `organism setup --print`

2. Summarize what it reported: the code-repo root and discovered repos, the vault and derived paths (including where session notes will go), whether the experimental doc check is on, any `.obsidian/app.json` cleanup it would make, and the per-repo credential checklist. If it found no vault, say that session notes stay off until one is set, and that any folder of markdown works.

3. Ask the user whether to apply the plan shown, and wait for the answer.
   - **A clear yes:** run `organism setup --yes` and show its output. `--yes` takes only the defaults the plan showed and leaves the optional extras off, so the user's yes here is the confirmation.
   - **The user names a vault folder** (setup may not have found one): show the plan again with `organism setup --print --vault <dir>`, ask again, and on a yes run `organism setup --yes --vault <dir>`.
   - **The user also wants the experimental doc check:** show the plan with `--doc-check` added to the same `--print` command, ask again, and on a yes run the same `--yes` command with `--doc-check`.
   - **No, or no clear answer:** write nothing, and say so.

Setup writes nothing without `--yes` or a terminal to ask in, so never run `organism setup` without `--print` or `--yes` here. In a terminal, `organism setup` asks question by question instead.

The wizard never stores secrets: each repo's `.env` stays the repo's own; it only validates and points at the repo's template. For the Obsidian **Sync** steps it prints (account, remote-vault pick, encryption password, exact-path excluded folders), those are GUI or keychain steps and must be done in the Obsidian app.
