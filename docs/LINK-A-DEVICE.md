# Using Organism on another computer

Each computer runs its own copy of the plugin. What they share is the vault: sync it
(Obsidian Sync, iCloud or any file sync) and every computer's session notes land in the
same place, each headed with the name of the machine it ran on. Decision records and the
"where we are" note live in your repos, so they travel with git as usual.

## 1. Install

```sh
claude plugin marketplace add Solidstate7/organism
claude plugin install organism@organism
```

## 2. Set up

Start a new session and run `/organism-setup`. It finds the vault and your repos, shows
the plan, and writes nothing until you say yes. If the vault sits at the same path on
both computers, the settings come out the same as on the first one.

Settings stay on each computer, in `~/.claude/state/paths.env`, and never sync. The one
to check on a new computer is the publish guard's list, so it blocks the same terms.
Setup finds a page named `Confidential Terms.md` anywhere in the vault; for a page with
another name, set the key by hand:

```sh
GUARD_PUBLISH_DENYLIST="$HOME/<your vault>/<folder>/<page>.md"
```

Secrets are never copied. Phone alerts need `~/.claude/state/telegram-alerts.json` on
each computer that should send them (see [ADVANCED.md, Alerts](ADVANCED.md#alerts)). The
doc check, if you use it, needs its own yes on each computer; it refreshes a repo's
page when a session in that repo ends, wherever that session ran.

## 3. Check

Ask Claude to run these (the plugin puts `organism` on the path of Claude's shell):

```sh
organism doctor          # checks the install
organism guard-status    # armed:<N>, the same N as on your first computer
```

Then start a session in any repo: its note appears in the vault's
`_claude-live/sessions/`, headed with this computer's name.

## 4. Remove

`/plugin` inside Claude Code removes the plugin. `organism uninstall` prints what it
would remove; `organism uninstall --yes --purge-state` also deletes Organism's own
settings files. Your session notes and decision records stay where they are.
