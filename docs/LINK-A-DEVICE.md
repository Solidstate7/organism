# Adding another Mac

> **The one rule.** Only one Mac owns the shared job, the daily doc check. A new Mac owns
> nothing: setup never claims the role for you, and `--yes` keeps it that way. Claim it on
> another Mac only to move it there, and release it on the old owner first. Two owners
> would both rewrite the same vault pages every morning.

## 0. What runs where

| job | runs on | when |
|---|---|---|
| sync with the repo (`converge`) | every Mac | daily 06:30, and after every `git pull` |
| log cleanup (`logrotate`) | every Mac | Sundays 04:30 |
| doc check (`reconcile`, experimental) | the one Mac that owns the shared job | daily 07:45 |
| session hooks (notes, the brief at start, the publish guard, session end) | every Mac | in every Claude Code session |

`bash ~/.organism/launchd/install-agents.sh --list` prints the authoritative list from the
manifest: each job, its role, and whether it runs on this Mac. Jobs you add yourself live
in `~/.claude/state/agents.local` on each Mac.

So a second Mac keeps itself in sync, cleans up its own logs, and saves its own sessions
into the shared vault (each note names the Mac it ran on), but it never runs the daily
doc check.

## 1. Install

First, `launchctl list | grep organism` should show nothing on the new Mac. If it shows
`com.organism.reconcile`, find out which Mac owns the shared job before going on.

```sh
git clone https://github.com/Solidstate7/organism.git ~/.organism
bash ~/.organism/bootstrap.sh     # add --plugin if your first Mac uses the plugin
organism setup          # keep the default at the role question
```

---

## 2. What bootstrap does

Bootstrap stops at the first failed step, and:

- checks that `git` and `python3` actually run (a Mac without the Command Line Tools fails
  here, with the `xcode-select --install` hint; a missing `node` only warns, but the
  publish guard can't run until it's installed),
- seeds `~/.claude/state/paths.env` from `config/paths.env.example`,
- creates `~/.claude/state/host.json`, holding no shared role,
- moves this Mac's own `~/.claude/{hooks,commands,keybindings.json,git-hooks}` aside to
  `*.pre-organism.bak` and links them to the repo (with `--plugin`, `commands` is left
  out and the plugin supplies the hooks), and merges its hooks into
  `~/.claude/settings.json` (without `--plugin`), keeping a `settings.json.pre-organism.bak` copy. A later run
  that finds a real file makes a timestamped `*.pre-organism.<ts>.bak` instead of
  deleting anything,
- schedules the per-Mac jobs (`converge`, `logrotate`),
- installs the `organism` command in `~/.local/bin`.

It never copies or touches secrets. Phone alerts need
`~/.claude/state/telegram-alerts.json` on each Mac that should send them (see
[ADVANCED.md, Alerts](ADVANCED.md#alerts)).

## 3. What setup does

Setup finds the vault and your repos, shows the plan, and writes nothing until you
confirm; `organism setup --print` shows the plan without asking. If the vault sits at
the same path on both Macs, the settings come out the same as on your first Mac. The doc
check stays off unless you say yes to it, and a second Mac can skip it: the owner's daily
run keeps the shared pages current.

To edit by hand instead, the keys are in `~/.claude/state/paths.env`. The one a second
Mac most needs is the publish guard's list, so it blocks the same terms:

```sh
CLAUDE_VAULT_ROOT="$HOME/<your vault>"
GUARD_PUBLISH_DENYLIST="$HOME/<your vault>/<folder>/Confidential Terms.md"
```

## 4. Check

```sh
organism doctor          # per_host on, both shared roles off, uuid matches this hardware
organism status          # converge and logrotate scheduled, no reconcile
```

Then start a Claude Code session here: its note appears in the vault's
`_claude-live/sessions/`, headed with this Mac's name.

## 5. Moving the shared job (rare, deliberate)

Release it on the old owner first, never both at once:

```sh
# on the OLD owner:
organism setup --section role      # answer: release
organism start                     # reloads its jobs; the doc check stops there

# then on the NEW owner:
organism setup --section role      # answer: claim owner
organism start                     # the doc check starts here
```

The doc check also needs its pairings file on the new owner: run
`organism setup --section pairings` there, or copy
`~/.claude/state/reconcile-pairings.json` across (older installs keep it in
`~/.claude/hooks/`).

## 6. Undo

```sh
organism uninstall          # prints the plan: jobs, plugin, links, settings, the command
organism uninstall --yes    # does it; --purge-state also deletes Organism's own state files
```

The links are put back from the newest `*.pre-organism.bak` copy, except
`keybindings.json`: it stays as a real copy of the repo's file, and your own is in
`keybindings.json.pre-organism.bak` if you want it back. If the command is
gone or fails, the same by hand (for a clone installed without `--plugin`):

```sh
organism stop                                     # unload the jobs
cd ~/.claude
for d in hooks commands keybindings.json git-hooks; do
  [ -L "$d" ] && rm "$d"                          # the links into the repo
  [ -e "$d.pre-organism.bak" ] && mv "$d.pre-organism.bak" "$d"
done
if [ -e settings.json.pre-organism.bak ]; then mv settings.json.pre-organism.bak settings.json
else rm -f settings.json; fi                      # generated; there was none before
rm -f ~/.local/bin/organism
```

If bootstrap ran more than once there may also be timestamped `*.pre-organism.<ts>.bak`
copies: `ls -d ~/.claude/*.bak` and restore the one you trust. Your session notes and
decision records stay where they are.

## 7. Day to day

- **Update (every Mac):** `organism update`, which is `git pull` in `~/.organism`. Its
  post-merge hook runs `converge.sh` (the deploy plus the jobs), and the daily `converge`
  job re-runs it in case a hook was skipped. [ADVANCED.md](ADVANCED.md#what-happens-on-organism-update-exactly)
  explains why.
- **This Mac's own settings:** `~/.claude/settings.json` is generated: the committed
  `claude/settings.json` (no hooks: those come from `hooks/hooks.json`, through the plugin
  or rendered in by the deploy) plus the gitignored `~/.claude/state/settings.local.json`. Put
  this Mac's settings in the overlay; changes Claude Code makes to the generated file are
  folded back into it on the next deploy.
- **Jobs you add:** register them in `~/.claude/state/agents.local` (template:
  `config/agents.local.example`). The installer removes any `com.organism.*` job named in
  neither its manifest nor that file, so never hand-write a plist with that prefix.
- **A different time on this Mac:** a `"schedules": {"<job>": "<token>"}` entry in
  `~/.claude/state/host.json` (setup's schedules question writes it) moves a job here;
  tokens are `keepalive`, `interval:N`, `cal:H:M` and `calw:W:H:M`. An invalid token is
  skipped with a warning and the old schedule stays.

## When to stop and check

- `reconcile` is already loaded on the new Mac, or it's unclear which Mac owns it.
- `organism doctor` reports a **uuid mismatch**: `host.json` was made on other hardware (a
  Migration Assistant copy, say). Its shared roles switch off, which is correct. To
  start clean: `rm ~/.claude/state/host.json`, then
  `python3 ~/.claude/hooks/host-role.py init`.
- After a pull, `install-agents.sh --list` shows `reconcile` assigned to a Mac that
  shouldn't own it, or the installer wants to remove a job you can't account for.
