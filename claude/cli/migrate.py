#!/usr/bin/env python3
"""organism migrate: move a symlink install to the plugin, or back.

    organism migrate --to-plugin  [--dry-run] [--force] [--yes] [--keep-pairings] [--skip-tests]
    organism migrate --to-symlink [--dry-run]

--to-plugin loads this clone as a plugin in place (`claude plugin marketplace add <clone>`),
so the clone stays the only copy of the code that runs and `git pull` stays the update. Its
steps are checkpointed in ~/.claude/state/organism-install.json; an interrupted run resumes
at the first step not done:

  1 preflight  claude >= 2.1.295, `claude plugin validate <clone> --strict`, the test suite,
               no other marketplace named organism, and no interactive claude running
               (a running session would keep its old hooks; --force skips this check)
  2 snapshot   settings.json, the overlay, settings.generated.json, the ~/.claude links,
               core.hooksPath, launchd jobs and plists, the pairings file, the plugin list,
               into ~/.claude/state/pre-plugin-<time>/
  3 plugin     marketplace add + install, then pin both keys in the settings overlay
  4 pairings   the doc check's "harness" pair reads settings.json and ~/.claude/commands,
               which change below. The rewrite is shown as a diff and applied only with
               --yes; --keep-pairings leaves the file as it is
  5 mode       the install record says plugin-inplace
  6 deploy     deploy.sh in plugin mode: Organism's hooks leave settings.json and the
               commands link goes (launchd and the other links are not touched)
  7 verify     no Organism hooks in settings.json; the plugin is enabled and read from this
               clone; the launchd job count is unchanged

The plugin is on before the settings hooks go, so there is never a moment with no guard.
--to-symlink reverses it in the safe order: settings hooks back first, then the plugin out.
ORGANISM_MIGRATE_STOP_AFTER=N stops after step N (for tests of the resume path).
"""
import argparse
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import installlib as L  # noqa: E402

ROOT = L.ROOT
MIN_CLAUDE = (2, 1, 295)
STEPS = ("preflight", "snapshot", "plugin", "pairings", "mode", "deploy", "verify")


class Stop(Exception):
    """Ends the run with a message and an exit code, leaving the checkpoint as it is."""
    def __init__(self, msg, code=1):
        super().__init__(msg)
        self.code = code


# ─── checkpoint (the install record) ────────────────────────────────────────
def record():
    m = L.where.read_marker()
    if not m or not L.same_path(m.get("root", ""), ROOT):
        m = {"mode": L.where.mode(ROOT), "root": ROOT}
    return m


def save(m):
    L.write_json(L.where.marker_path(), m)


# ─── the harness pair rewrite ───────────────────────────────────────────────
def rewrite_harness(pairings, root):
    """A copy of the pairings with the "harness" pair reading the plugin's files instead of
    settings.json hooks and ~/.claude/commands. Each new command prints what the old one
    printed on a symlink install, so the doc check sees no change and raises no alarm.
    Only the exact shapes the example config ships are rewritten; anything else is kept."""
    out = json.loads(json.dumps(pairings))
    q = json.dumps(root)
    for pair in out.get("pairs", []) if isinstance(out, dict) else []:
        if not isinstance(pair, dict) or pair.get("id") != "harness":
            continue
        probes = pair.get("probes") or {}
        cmd = (probes.get("commands") or {}).get("cmd", "")
        if "commands/*.md" in cmd:
            probes["commands"]["cmd"] = (
                f"ls -d {q}/skills/*/ 2>/dev/null | xargs -n1 basename | sort | tr '\\n' ' '")
        cmd = (probes.get("settings_hooks") or {}).get("cmd", "")
        if "settings.json" in cmd and "hooks.json" not in cmd:
            probes["settings_hooks"]["cmd"] = (
                "python3 -c \"import json,sys;"
                "k=set(json.load(open('settings.json')).get('hooks',{}))|"
                "set(json.load(open(sys.argv[1])).get('hooks',{}));"
                "print(' '.join(sorted(k)) or 'none')\" "
                f"{q}/hooks/hooks.json")
        guards = pair.get("guards") or {}
        g = guards.get("g_guardpublish", "")
        if g.strip() == "grep -q guard-publish settings.json":
            guards["g_guardpublish"] = (
                f"grep -q guard-publish {q}/hooks/hooks.json && "
                "grep -q '\"organism@organism\": true' settings.json")
    return out


def pairings_diff(path, new):
    with open(path, encoding="utf-8") as f:
        old = f.read()
    text = json.dumps(new, indent=2, ensure_ascii=False) + "\n"
    return old, text, "".join(difflib.unified_diff(
        json.dumps(json.loads(old), indent=2, ensure_ascii=False).splitlines(True),
        text.splitlines(True), fromfile=path, tofile=path + " (plugin mode)"))


# ─── helpers ────────────────────────────────────────────────────────────────
def claude_version():
    rc, out = L.run(["claude", "--version"], timeout=60)
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", out) if rc == 0 else None
    return tuple(int(x) for x in m.groups()) if m else None


def interactive_claudes():
    """Running interactive claude sessions: `claude` or `node .../claude` processes without
    -p/--print that are not a `claude plugin|mcp|config` call. A session this command was
    started from counts too: it would keep its old hooks."""
    rc, out = L.run(["ps", "-Ao", "pid=,args="], timeout=30)
    found = []
    for ln in out.splitlines() if rc == 0 else []:
        parts = ln.split()
        if len(parts) < 2 or not parts[0].isdigit() or int(parts[0]) == os.getpid():
            continue
        words = parts[1:]
        i = 0
        if os.path.basename(words[0]) in ("node", "bun") and len(words) > 1:
            i = 1
        if os.path.basename(words[i]) != "claude":
            continue
        rest = words[i + 1:]
        if any(w in ("-p", "--print") for w in rest) or (rest and rest[0] in ("plugin", "plugins", "mcp", "config", "--version", "-v")):
            continue
        found.append(ln.strip())
    return found


def deploy():
    r = subprocess.run(["bash", os.path.join(ROOT, "deploy.sh")])
    if r.returncode != 0:
        raise Stop(f"deploy.sh exited {r.returncode}")


def pin_plugin(on=True):
    """Pin (or drop) the plugin's two settings keys in the overlay, so a later deploy keeps
    them whatever the generated settings.json says."""
    _, overlay, _ = L.settings_paths()
    o = L.load_json(overlay)
    if o is None and os.path.exists(overlay) or o is not None and not isinstance(o, dict):
        raise Stop(f"{overlay} is not a JSON object; fix it and re-run")
    o = o or {}
    if on:
        o.setdefault("enabledPlugins", {})[L.PLUGIN_ID] = True
        o.setdefault("extraKnownMarketplaces", {})[L.MARKETPLACE] = {
            "source": {"source": "directory", "path": ROOT}}
    else:
        o = L.settingsmerge.eject_plugin(o)
    L.write_json(overlay, o)


def install_plugin(dry):
    """marketplace add <clone> + install + pin. Idempotent."""
    entry = L.plugin_entry()
    if entry and entry.get("enabled") and L.same_path(entry.get("readFromFolder", ""), ROOT):
        L.say("    ✓ the plugin is already installed from this clone")
    else:
        mk = [m for m in L.marketplace_list() or [] if isinstance(m, dict) and m.get("name") == L.MARKETPLACE]
        for x in mk:
            src = x.get("path") or x.get("installLocation") or ""
            if not L.same_path(src, ROOT):
                raise Stop(f"a different marketplace is already named {L.MARKETPLACE} ({src or x}). "
                           f"Remove it first: claude plugin marketplace remove {L.MARKETPLACE}")
        cmds = []
        if not mk:
            cmds.append(["claude", "plugin", "marketplace", "add", ROOT])
        cmds.append(["claude", "plugin", "install", L.PLUGIN_ID])
        for c in cmds:
            L.say(f"    {'would run' if dry else 'running'}: {' '.join(c)}")
            if not dry:
                rc, out = L.run(c, timeout=180)
                if rc != 0:
                    raise Stop(f"{' '.join(c)} failed: {out}")
    L.say(f"    {'would pin' if dry else 'pinning'} enabledPlugins and extraKnownMarketplaces in the overlay")
    if not dry:
        pin_plugin(True)


# ─── steps ──────────────────────────────────────────────────────────────────
def s_preflight(m, a):
    if L.where.in_cache(ROOT):
        raise Stop("this copy lives in the plugin cache; migrate runs from a clone")
    if not L.have("claude"):
        raise Stop("`claude` not found on PATH")
    v = claude_version()
    if v is None or v < MIN_CLAUDE:
        raise Stop(f"Claude Code {'.'.join(map(str, MIN_CLAUDE))} or newer is needed (found {v and '.'.join(map(str, v))})")
    rc, out = L.run(["claude", "plugin", "validate", ROOT, "--strict"], timeout=180)
    if rc != 0:
        raise Stop(f"claude plugin validate --strict failed:\n{out}")
    for mk in L.marketplace_list() or []:
        if isinstance(mk, dict) and mk.get("name") == L.MARKETPLACE and \
                not L.same_path(mk.get("path") or mk.get("installLocation") or "", ROOT):
            raise Stop(f"a different marketplace is already named {L.MARKETPLACE}: {mk.get('path') or mk}")
    if not a.skip_tests:
        L.say("    running the test suite …")
        rc, out = L.run(["bash", os.path.join(ROOT, "claude", "hooks", "tests", "run-all.sh")], timeout=1800)
        if rc != 0:
            raise Stop("the test suite failed:\n" + "\n".join(out.splitlines()[-15:]))
    procs = interactive_claudes()
    if procs and not a.force:
        raise Stop("interactive claude sessions are running; they would keep their old hooks:\n  "
                   + "\n  ".join(procs) + "\nClose them (or pass --force) and run this again.")
    L.say(f"    ✓ claude {'.'.join(map(str, v))}, plugin valid, "
          f"{'tests skipped' if a.skip_tests else 'tests pass'}, "
          f"{'no other sessions' if not procs else 'sessions running (--force)'}")


def s_snapshot(m, a):
    d = os.path.join(L.where.state_dir(), "pre-plugin-" + time.strftime("%Y%m%d-%H%M%S"))
    if a.dry_run:
        L.say(f"    would save the snapshot to {d}")
        return
    os.makedirs(d, exist_ok=True)
    for p in L.settings_paths():
        if os.path.isfile(p):
            shutil.copy2(p, d)
    pairings = L.where.pairings()
    if pairings:
        shutil.copy2(pairings, os.path.join(d, "reconcile-pairings.json"))
    for p in L.plists():
        os.makedirs(os.path.join(d, "plists"), exist_ok=True)
        shutil.copy2(p, os.path.join(d, "plists"))
    links = {i: os.readlink(os.path.join(L.claude_dir(), i)) for i in L.LINKS
             if os.path.islink(os.path.join(L.claude_dir(), i))}
    facts = {"links": links, "core.hooksPath": L.global_hooks_path(),
             "launchd": L.launchctl_jobs(), "plugins": L.plugin_list(), "pairings": pairings}
    L.write_json(os.path.join(d, "snapshot.json"), facts)
    m["migration"]["snapshot"] = d
    L.say(f"    ✓ saved {d}")


def s_pairings(m, a):
    path = L.where.pairings()
    if not path or a.keep_pairings:
        L.say("    no pairings file" if not path else "    kept as it is (--keep-pairings)")
        return
    data = L.load_json(path)
    new = rewrite_harness(data, ROOT) if isinstance(data, dict) else data
    if new == data:
        L.say(f"    ✓ {path} needs no change")
        return
    old_text, new_text, diff = pairings_diff(path, new)
    L.say(f"    the doc check's harness pair in {path} would change:")
    for ln in diff.splitlines():
        L.say("      " + ln)
    if a.dry_run:
        return
    if not a.yes:
        raise Stop("pairings not changed. Re-run with --yes to apply this diff, or with "
                   "--keep-pairings to leave the file as it is (its harness checks will then alarm once).", 3)
    shutil.copy2(path, path + ".pre-plugin.bak")
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        f.write(new_text)
    os.replace(path + ".tmp", path)
    m["migration"]["pairings"] = {"path": path, "written": new_text, "backup": path + ".pre-plugin.bak"}
    L.say(f"    ✓ rewrote {path} (backup {path}.pre-plugin.bak)")


def s_plugin(m, a):
    install_plugin(a.dry_run)


def s_mode(m, a):
    L.say("    would set the install mode to plugin-inplace" if a.dry_run else "    ✓ install mode: plugin-inplace")
    if not a.dry_run:
        m["mode"] = "plugin-inplace"


def s_deploy(m, a):
    if a.dry_run:
        target, overlay, _ = L.settings_paths()
        n = len(L.organism_hook_commands(L.load_json(target) or {})) + \
            len(L.organism_hook_commands(L.load_json(overlay) or {}))
        L.say(f"    would run deploy.sh in plugin mode: drop {n} Organism hook entr{'y' if n == 1 else 'ies'} "
              "from settings, remove the ~/.claude/commands link; launchd and the other links stay")
        return
    save(m)   # deploy.sh reads the mode from the install record
    deploy()


def s_verify(m, a):
    if a.dry_run:
        L.say("    would check: no Organism hooks in settings.json, plugin enabled from this clone, launchd unchanged")
        return
    problems = []
    target, _, _ = L.settings_paths()
    left = L.organism_hook_commands(L.load_json(target) or {})
    if left:
        problems.append(f"settings.json still runs Organism hooks: {left}")
    entry = L.plugin_entry()
    if not entry:
        problems.append(f"`claude plugin list --json` does not list {L.PLUGIN_ID}")
    else:
        if not entry.get("enabled"):
            problems.append("the plugin is installed but not enabled")
        if not L.same_path(entry.get("readFromFolder") or "", ROOT):
            problems.append(f"the plugin is read from {entry.get('readFromFolder')}, not {ROOT}")
        if entry.get("errors"):
            problems.append(f"the plugin reports errors: {entry['errors']}")
    snap = L.load_json(os.path.join(m["migration"].get("snapshot") or "", "snapshot.json")) or {}
    before, now = snap.get("launchd"), L.launchctl_jobs()
    if before is not None and now is not None and len(before) != len(now):
        problems.append(f"launchd jobs changed: {len(before)} before, {len(now)} now")
    if problems:
        raise Stop("verify failed:\n  " + "\n  ".join(problems) +
                   "\nRoll back with: organism migrate --to-symlink")
    L.say("    ✓ settings.json has no Organism hooks; the plugin is enabled and read from this clone")


RUNNERS = {"preflight": s_preflight, "snapshot": s_snapshot, "pairings": s_pairings,
           "plugin": s_plugin, "mode": s_mode, "deploy": s_deploy, "verify": s_verify}


def refuse_other_clone():
    other = L.other_deployed(ROOT)
    if other:
        raise Stop(f"this machine runs Organism from {other}, not {ROOT}. "
                   f"Run `{other}/bin/organism migrate` instead; nothing changed.")


def to_plugin(a):
    refuse_other_clone()
    m = record()
    if m.get("mode") == "plugin-inplace" and "migration" not in m:
        L.say("already in plugin-inplace mode; nothing to do")
        return 0
    if m.get("mode") == "plugin-copied":
        raise Stop("this is a copied plugin install; migrate runs from a clone")
    mig = m.setdefault("migration", {"to": "plugin-inplace", "done": [], "started": time.strftime("%Y-%m-%dT%H:%M:%S")})
    stop_after = int(os.environ.get("ORGANISM_MIGRATE_STOP_AFTER") or 0)
    L.say(f"organism migrate --to-plugin{' (dry run: nothing changes)' if a.dry_run else ''}: {ROOT}")
    for n, name in enumerate(STEPS, 1):
        if name in mig["done"]:
            L.say(f"  {n} {name}: done earlier")
            continue
        L.say(f"  {n} {name}")
        RUNNERS[name](m, a)
        if a.dry_run:
            continue
        mig["done"].append(name)
        save(m)
        if stop_after == n:
            L.say(f"stopped after step {n} (ORGANISM_MIGRATE_STOP_AFTER); run again to resume")
            return 0
    if a.dry_run:
        L.say("dry run: nothing changed.")
        return 0
    done = m.pop("migration")
    m["last_migration"] = {k: done[k] for k in ("snapshot", "pairings") if k in done}
    save(m)
    L.say("done. Start new Claude Code sessions to load the plugin; running ones keep their old hooks.")
    return 0


def to_symlink(a):
    refuse_other_clone()
    m = record()
    last = m.get("migration") or m.get("last_migration") or {}
    snap = last.get("snapshot")
    L.say(f"organism migrate --to-symlink{' (dry run: nothing changes)' if a.dry_run else ''}: {ROOT}")
    if m.get("mode") == "plugin-copied":
        raise Stop("this is a copied plugin install; there is no symlink install to go back to")
    plan = ["set the install mode to symlink and run deploy.sh (Organism's hooks back in settings.json, "
            "the commands link back)"]
    entry = L.plugin_entry() if L.have("claude") else None
    if entry:
        plan.append(f"claude plugin uninstall {L.PLUGIN_ID}")
    if any(isinstance(x, dict) and x.get("name") == L.MARKETPLACE for x in L.marketplace_list() or []):
        plan.append(f"claude plugin marketplace remove {L.MARKETPLACE}")
    plan.append("drop the plugin's keys from the overlay and run deploy.sh again")
    pr = last.get("pairings")
    if pr and os.path.isfile(pr.get("path", "")):
        with open(pr["path"], encoding="utf-8") as f:
            same = f.read() == pr.get("written")
        plan.append(f"move back {pr['backup']}" if same and os.path.isfile(pr.get("backup", ""))
                    else f"leave {pr['path']} (changed since the migration)")
    for p in plan:
        L.say(f"  - {p}")
    if a.dry_run:
        L.say("dry run: nothing changed.")
        return 0
    m["mode"] = "symlink"
    save(m)                        # deploy.sh reads the mode from the install record
    try:
        deploy()                   # settings hooks back on first: no moment without a guard
    except Stop as e:
        raise Stop(f"{e}; the plugin is still installed. Fix the cause, then run "
                   "organism migrate --to-symlink again")
    failed = []
    if entry:
        rc, out = L.run(["claude", "plugin", "uninstall", L.PLUGIN_ID], timeout=180)
        L.say(f"  {'✓' if rc == 0 else '✗'} claude plugin uninstall {L.PLUGIN_ID}" + ("" if rc == 0 else f": {out}"))
        if rc != 0:
            failed.append("plugin uninstall")
    if any(isinstance(x, dict) and x.get("name") == L.MARKETPLACE for x in L.marketplace_list() or []):
        rc, out = L.run(["claude", "plugin", "marketplace", "remove", L.MARKETPLACE], timeout=180)
        L.say(f"  {'✓' if rc == 0 else '✗'} claude plugin marketplace remove {L.MARKETPLACE}" + ("" if rc == 0 else f": {out}"))
        if rc != 0:
            failed.append("marketplace remove")
    pin_plugin(False)
    deploy()                       # the plugin's keys leave settings.json even when a step failed
    if failed:
        raise Stop(f"claude {' and '.join(failed)} failed; the plugin is disabled but still installed. "
                   "Run organism migrate --to-symlink again")
    if pr and os.path.isfile(pr.get("path", "")) and os.path.isfile(pr.get("backup", "")):
        with open(pr["path"], encoding="utf-8") as f:
            if f.read() == pr.get("written"):
                os.replace(pr["backup"], pr["path"])
                L.say(f"  ✓ moved back {pr['path']}")
    m.pop("migration", None)       # kept until here so a retry after a failure still has it
    m.pop("last_migration", None)
    save(m)
    L.say("back in symlink mode" + (f"; the pre-migration snapshot is in {snap}" if snap else "") + ".")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="organism migrate")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--to-plugin", action="store_true")
    g.add_argument("--to-symlink", action="store_true")
    g.add_argument("--install-plugin", action="store_true", help=argparse.SUPPRESS)  # bootstrap.sh --plugin
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true", help="run even while interactive claude sessions run")
    ap.add_argument("--yes", action="store_true", help="apply the pairings rewrite after showing it")
    ap.add_argument("--keep-pairings", action="store_true", help="leave the pairings file as it is")
    ap.add_argument("--skip-tests", action="store_true", help="skip the test suite in the preflight")
    a = ap.parse_args(argv)
    try:
        if a.install_plugin:
            install_plugin(a.dry_run)
            return 0
        return to_plugin(a) if a.to_plugin else to_symlink(a)
    except Stop as e:
        L.say(f"✗ {e}")
        return e.code


if __name__ == "__main__":
    sys.exit(main())
