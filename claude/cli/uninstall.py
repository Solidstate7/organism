#!/usr/bin/env python3
"""organism uninstall: remove Organism from this machine, per install mode.

    organism uninstall [--yes] [--purge-state] [--remove-marketplace]

Without --yes it only prints the plan. Steps, in the order they run:

  step                                                    symlink  in-place  copied
  background jobs (launchd/uninstall-agents.sh)              x        x
  this repo's own git hooks, if they link into the clone     x        x
  claude plugin uninstall organism@organism                           x        x
  claude plugin marketplace remove organism                           x        (--remove-marketplace)
  settings: drop Organism's hooks and the plugin's keys      x        x        (if generated)
  ~/.claude links into the clone                             x        x
  global core.hooksPath, only if it is ~/.claude/git-hooks   x        x
  ~/.local/bin/organism, if it links into the clone          x        x
  the install record (state/organism-install.json)           x        x        x
  --purge-state: Organism's own state files, by exact name   x        x        x

Links: hooks, commands and git-hooks are removed, and the newest *.pre-organism*.bak that
deploy.sh left in their place is moved back. skills and keybindings.json are replaced by real
copies, so personal skills survive deleting the clone. Never touched: the clone, the vault,
~/.claude/logs, other backups, and Claude Code's own cache copy of the plugin (Claude Code
removes an orphaned copy by itself).
"""
import argparse
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import installlib as L  # noqa: E402

ROOT = L.ROOT

# Organism's own state files. Exact names only, never a pattern.
STATE_FILES = ("paths.env", "host.json", "agents.local", "incognito.json", "incognito.json.lock",
               "reconcile-pairings.json", "reconcile-state.json", "reconcile.lock",
               "settings.local.json", "settings.generated.json", "organism-install.json",
               "guard-publish-node-missing.alerted", "entrypoint-census.json", "llm-job.lock")


def _rmdir_if_empty(d):
    try:
        os.rmdir(d)
    except OSError:
        pass   # something else lives there: leave it


class Step:
    def __init__(self, title):
        self.title = title
        self.actions = []   # (description, callable or None for a note)

    def add(self, desc, fn=None):
        self.actions.append((desc, fn))


def markers():
    """The `<marker>-last-success` names both job manifests declare."""
    rc, out = L.run(["bash", os.path.join(ROOT, "launchd", "install-agents.sh"), "--list"], timeout=60)
    names = []
    for ln in out.splitlines() if rc == 0 else []:
        p = ln.strip().split("|")
        if len(p) > 4 and p[4].strip() and p[4].strip() not in names:
            names.append(p[4].strip())
    return [n + "-last-success" for n in names]


def sh(cmd, env=None):
    def go():
        r = subprocess.run(cmd, env=env)
        if r.returncode != 0:
            raise RuntimeError(f"{' '.join(cmd)} exited {r.returncode}")
    return go


def remove_link_restore(path):
    def go():
        os.unlink(path)
        bak = L.newest_backup(path)
        if bak:
            os.rename(bak, path)
            L.say(f"      moved back {os.path.basename(bak)}")
    return go


def replace_with_copy(path):
    def go():
        target = os.path.realpath(path)
        if not os.path.exists(target):
            os.unlink(path)
            return
        tmp = path + ".organism-copy.tmp"
        if os.path.lexists(tmp):
            shutil.rmtree(tmp) if os.path.isdir(tmp) and not os.path.islink(tmp) else os.unlink(tmp)
        if os.path.isdir(target):
            shutil.copytree(target, tmp, symlinks=True)
        else:
            shutil.copy2(target, tmp)
        os.unlink(path)
        os.rename(tmp, path)
    return go


def build(mode, args):
    full = mode in ("symlink", "plugin-inplace")
    steps = []
    claude = L.claude_dir()
    state = L.where.state_dir()

    if full:
        s = Step("background jobs")
        jobs = [os.path.basename(p)[:-len(".plist")] for p in L.plists()]
        if jobs:
            s.add("unload and remove " + ", ".join(jobs),
                  sh(["bash", os.path.join(ROOT, "launchd", "uninstall-agents.sh")]))
        else:
            s.add(f"none installed in {L.launchd_dir()}")
        steps.append(s)

        s = Step("this repo's own git hooks")
        gd = L.git_dir(ROOT)
        for h in L.SELF_HOOKS if gd else ():
            p = os.path.join(gd, "hooks", h)
            if L.link_into(p, ROOT):
                bak = L.newest_backup(p)
                s.add(f"remove {p}" + (f" (and move back {os.path.basename(bak)})" if bak else ""),
                      remove_link_restore(p))
        if not s.actions:
            s.add("none link into this clone")
        steps.append(s)

    entry = L.plugin_entry() if L.have("claude") else None
    ours = entry and L.same_path(entry.get("readFromFolder") or "", ROOT)
    if mode in ("plugin-inplace", "plugin-copied") or ours:
        s = Step("the plugin")
        if not L.have("claude"):
            s.add("`claude` not found on PATH: remove the plugin inside Claude Code with /plugin")
        else:
            if entry:
                s.add(f"claude plugin uninstall {L.PLUGIN_ID}",
                      sh(["claude", "plugin", "uninstall", L.PLUGIN_ID]))
            else:
                s.add(f"{L.PLUGIN_ID} is not installed")
            listed = [m for m in L.marketplace_list() or [] if isinstance(m, dict) and m.get("name") == L.MARKETPLACE]
            if listed and (mode != "plugin-copied" or args.remove_marketplace):
                s.add(f"claude plugin marketplace remove {L.MARKETPLACE} (its source folder stays)",
                      sh(["claude", "plugin", "marketplace", "remove", L.MARKETPLACE]))
            elif listed:
                s.add(f"the {L.MARKETPLACE} marketplace stays (add --remove-marketplace to remove it)")
        steps.append(s)

    s = Step("settings")
    target, overlay, snapshot = L.settings_paths()
    current = L.load_json(target) or {}
    if full or os.path.isfile(snapshot):
        s.add("drop Organism's hooks and the plugin's settings keys, then regenerate "
              f"{target} from your own settings (the overlay is backed up first)",
              sh([sys.executable, os.path.join(ROOT, "claude", "hooks", "lib", "settingsmerge.py"),
                  os.path.join(ROOT, "claude", "settings.json"), "--mode", "eject"]))
    elif L.organism_hook_commands(current):
        s.add(f"{target} has Organism hook entries that Organism did not write; remove them by hand")
    else:
        s.add("nothing to change")
    steps.append(s)

    # a deployed clone that was moved or deleted leaves links into a folder that is gone;
    # they are this install's too
    gone = L.deployed_root()
    gone = gone if gone and not os.path.isdir(gone) and not L.same_path(gone, ROOT) else None

    def ours(p):
        return L.link_into(p, ROOT) or bool(gone and L.link_into(p, gone))

    if full:
        s = Step("links in ~/.claude")
        for item in L.LINKS:
            p = os.path.join(claude, item)
            if not ours(p):
                continue
            if item in ("skills", "keybindings.json"):
                s.add(f"replace the {item} link with a real copy of {os.path.realpath(p)}",
                      replace_with_copy(p))
            else:
                bak = L.newest_backup(p)
                s.add(f"remove the {item} link" + (f" and move back {os.path.basename(bak)}" if bak else ""),
                      remove_link_restore(p))
        if ours(os.path.join(claude, "hooks")):
            s.add("note: anything of your own that imports ~/.claude/hooks/lib stops working "
                  "once the hooks link is gone")
        if not s.actions:
            s.add("none link into this clone")
        steps.append(s)

        s = Step("global git hooks")
        hp = L.global_hooks_path()
        if L.ours_hooks_path(hp):
            s.add(f"unset core.hooksPath (was {hp})",
                  sh(["git", "config", "--global", "--unset", "core.hooksPath"]))
        else:
            s.add("core.hooksPath is " + (f"{hp}, not Organism's; left alone" if hp else "not set"))
        steps.append(s)

        s = Step("the organism command")
        cli = os.path.join(L.home(), ".local", "bin", "organism")
        if os.path.islink(cli) and (L.same_path(cli, os.path.join(ROOT, "bin", "organism"))
                                    or bool(gone and L.link_into(cli, gone))):
            s.add(f"remove {cli}", lambda: os.unlink(cli))
        else:
            s.add(f"{cli} does not link into this clone; left alone")
        steps.append(s)

    s = Step("state")
    marker = L.where.marker_path()
    if os.path.isfile(marker) and not args.purge_state:
        s.add(f"remove the install record {marker}", "marker")
    if args.purge_state:
        for name in STATE_FILES + tuple(markers()):
            p = os.path.join(state, name)
            if os.path.isfile(p) and not os.path.islink(p) and not L.same_path(p, marker):
                s.add(f"delete {p}", (lambda p=p: os.unlink(p)))
        # SessionEnd's per-session locks: state/locks/session-end-<session>.lock
        locks = os.path.join(state, "locks")
        if os.path.isdir(locks) and not os.path.islink(locks):
            for name in sorted(os.listdir(locks)):
                p = os.path.join(locks, name)
                if (name.startswith("session-end-") and name.endswith(".lock")
                        and os.path.isfile(p) and not os.path.islink(p)):
                    s.add(f"delete {p}", (lambda p=p: os.unlink(p)))
            s.add(f"remove {locks} if empty", (lambda: _rmdir_if_empty(locks)))
        if os.path.isfile(marker):
            s.add(f"delete {marker}", "marker")   # last, like the record above
    elif not s.actions:
        s.add("kept (add --purge-state to delete Organism's own state files)")
    if not s.actions:
        s.add("no Organism state files found")
    steps.append(s)
    return steps


def main(argv=None):
    ap = argparse.ArgumentParser(prog="organism uninstall", description="remove Organism from this machine")
    ap.add_argument("--yes", action="store_true", help="apply the plan (default: print it)")
    ap.add_argument("--purge-state", action="store_true", help="also delete Organism's own state files")
    ap.add_argument("--remove-marketplace", action="store_true",
                    help="copied installs: also remove the organism marketplace")
    args = ap.parse_args(argv)

    mode = L.where.mode(ROOT)
    other = L.other_deployed(ROOT) if mode != "plugin-copied" else None
    if other:
        L.say(f"✗ this machine runs Organism from {other}, not {ROOT}.")
        L.say(f"  Run `{other}/bin/organism uninstall` instead; nothing changed.")
        return 1
    L.say(f"organism uninstall: install mode {mode}, code in {ROOT}")
    L.say("plan:" if not args.yes else "removing:")
    failed = 0
    for step in build(mode, args):
        L.say(f"  {step.title}")
        for desc, fn in step.actions:
            if fn is None or not args.yes:
                L.say(f"    - {desc}")
                continue
            if fn == "marker":
                # last, and only when everything else worked: a retry needs the record
                # to know the install mode
                if failed:
                    L.say(f"    - kept the install record, so a re-run can retry")
                    continue
                fn = lambda: os.unlink(L.where.marker_path())
            try:
                fn()
                L.say(f"    ✓ {desc}")
            except Exception as e:
                failed += 1
                L.say(f"    ✗ {desc}: {e}")
    L.say("")
    L.say(f"never touched: {ROOT}, your vault (notes and decision records), {L.claude_dir()}/logs")
    for b in L.all_backups():
        L.say(f"  backup kept for manual restore: {b}")
    if not args.yes:
        L.say("nothing changed. Run `organism uninstall --yes` to apply this plan.")
        return 0
    if failed:
        L.say(f"{failed} action(s) failed; the rest were applied. Re-run to retry.")
        return 1
    L.say("Organism is uninstalled. Start a new Claude Code session to drop its hooks.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
