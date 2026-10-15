#!/usr/bin/env python3
"""Deploy-time settings merge.

Claude Code reads exactly ONE user-level file, ~/.claude/settings.json, a user-level
settings.local.json is NOT supported. The committed
base must stay conservative for a public-bound repo, yet the user's machines
legitimately run bypassPermissions + personal prefs. So deploy.sh no longer symlinks
settings.json; it GENERATES it:

    merged = deep_merge(repo base claude/settings.json,
                        overlay ~/.claude/state/settings.local.json)   # gitignored, per-machine

A snapshot of the last generated file (~/.claude/state/settings.generated.json) makes
redeploys drift-aware: in-session writes to the real settings.json (e.g. /model persisting
a default) are detected as current-vs-snapshot drift and folded INTO the overlay before
regenerating, choices made through the CLI survive deploys. (Under the old
symlink deploy, /model dirtied the REPO working tree.)

Organism's own hooks have one source, hooks/hooks.json, and are never kept in the base
or the overlay: every run strips any entry whose command runs one of the four entry
points (guard-publish.sh, inject-handoff.sh, obsidian-mirror.py,
session-end-handoff.py under a claude/hooks/ folder) from both, backing the overlay up
first when it changes. Then, by mode:
  legacy  (symlink installs) hooks.json is rendered into settings.json with the
          plugin root replaced by the clone's absolute path, in shell form
  plugin  the plugin supplies the hooks, so nothing is rendered
  eject   (uninstall) nothing is rendered, and enabledPlugins["organism@organism"] and
          extraKnownMarketplaces.organism are dropped from the overlay too, so a later
          deploy can't bring the plugin back

Usage: settingsmerge.py <repo-base-settings.json> [--mode legacy|plugin|eject]
                        [--hooks <hooks.json>] [--root <plugin root>]
--hooks and --root default to the repo this script lives in. Default mode: legacy.
Never raises past main(). An unreadable base leaves settings.json untouched (exit 0); any
other error exits 1, so deploy.sh and uninstall stop instead of reporting success.
"""
import json, os, re, shlex, shutil, sys, tempfile, time

HOME = os.environ.get("HOME") or os.path.expanduser("~")
TARGET = os.path.join(HOME, ".claude", "settings.json")
OVERLAY = os.path.join(HOME, ".claude", "state", "settings.local.json")
SNAPSHOT = os.path.join(HOME, ".claude", "state", "settings.generated.json")
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__)))))
PLUGIN_ID = "organism@organism"
MARKETPLACE = "organism"
OURS = re.compile(r"claude/hooks/(guard-publish\.sh|inject-handoff\.sh|obsidian-mirror\.py"
                  r"|session-end-handoff\.py)(?![\w.-])")


def _load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def union_list(base, over):
    """The base's items, then the overlay's items not already there (JSON equality)."""
    out = list(base)
    seen = {json.dumps(x, sort_keys=True) for x in base}
    for x in over:
        key = json.dumps(x, sort_keys=True)
        if key not in seen:
            out.append(x)
            seen.add(key)
    return out


def deep_merge(base, over, _path=()):
    """dicts merge recursively (overlay wins); a list under "hooks" is unioned, so an
    overlay adds its hooks beside the base's instead of replacing them; any other value:
    overlay replaces. (A replaced hook list froze the base's hooks into the
    overlay, so a hook added to the base later never reached that machine.)"""
    if isinstance(base, dict) and isinstance(over, dict):
        out = dict(base)
        for k, v in over.items():
            out[k] = deep_merge(base[k], v, _path + (k,)) if k in base else v
        return out
    if _path[:1] == ("hooks",) and isinstance(base, list) and isinstance(over, list):
        return union_list(base, over)
    return over


def is_ours(hook):
    """A hook entry that runs one of Organism's four entry points, in any wiring."""
    if not isinstance(hook, dict):
        return False
    words = [hook.get("command") or ""] + [a for a in hook.get("args") or [] if isinstance(a, str)]
    return any(OURS.search(w) for w in words)


def strip_ours(settings):
    """A copy of settings without Organism's hook entries. Matcher groups and events
    left empty are dropped, and so is an empty "hooks" key."""
    if not isinstance(settings, dict) or not isinstance(settings.get("hooks"), dict):
        return settings
    out = dict(settings)
    hooks = {}
    for event, groups in settings["hooks"].items():
        if not isinstance(groups, list):
            hooks[event] = groups
            continue
        kept = []
        for g in groups:
            if isinstance(g, dict) and isinstance(g.get("hooks"), list):
                inner = [h for h in g["hooks"] if not is_ours(h)]
                if inner:
                    kept.append(dict(g, hooks=inner))
            else:
                kept.append(g)
        if kept:
            hooks[event] = kept
    if hooks:
        out["hooks"] = hooks
    else:
        out.pop("hooks", None)
    return out


def eject_plugin(settings):
    """A copy of settings without the plugin's enable flag and marketplace entry."""
    out = dict(settings)
    for key, name in (("enabledPlugins", PLUGIN_ID), ("extraKnownMarketplaces", MARKETPLACE)):
        if isinstance(out.get(key), dict) and name in out[key]:
            rest = {k: v for k, v in out[key].items() if k != name}
            if rest:
                out[key] = rest
            else:
                out.pop(key)
    return out


def render_hooks(hooks_file, root):
    """hooks.json as a settings fragment: ${CLAUDE_PLUGIN_ROOT} replaced by root, and each
    exec-form hook turned into shell form with its arguments quoted. settings.json is also
    read by older Claude Code versions, and one that doesn't know `args` would run the bare
    interpreter with the hook payload as its script."""
    with open(hooks_file, encoding="utf-8") as f:
        data = json.loads(f.read().replace("${CLAUDE_PLUGIN_ROOT}", json.dumps(root)[1:-1]))
    hooks = data.get("hooks", {})
    for groups in hooks.values():
        for g in groups:
            for h in g.get("hooks", []):
                if isinstance(h.get("args"), list):
                    h["command"] = " ".join([h["command"]] + [shlex.quote(a) for a in h.pop("args")])
    return {"hooks": hooks}


def overlay_backup(path):
    bak = path + "." + time.strftime("%Y%m%d-%H%M%S") + ".bak"
    n = 1
    while os.path.exists(bak):
        n += 1
        bak = path + "." + time.strftime("%Y%m%d-%H%M%S") + f"-{n}.bak"
    shutil.copy2(path, bak)
    return bak


def parse_args(argv):
    opts = {"base": None, "mode": "legacy", "hooks": os.path.join(REPO, "hooks", "hooks.json"),
            "root": REPO}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("--mode", "--hooks", "--root") and i + 1 < len(argv):
            opts[a[2:]] = argv[i + 1]
            i += 2
            continue
        if opts["base"] is None:
            opts["base"] = a
        i += 1
    if opts["mode"] not in ("legacy", "plugin", "eject"):
        raise ValueError(f"unknown mode {opts['mode']!r}")
    return opts


def backup_path():
    """settings.json.pre-organism.bak, timestamped when that name is taken (deploy.sh's rule)."""
    bak = TARGET + ".pre-organism.bak"
    if os.path.exists(bak):
        bak = TARGET + ".pre-organism." + time.strftime("%Y%m%d-%H%M%S") + ".bak"
    return bak


def fold_drift(current, snapshot, overlay):
    """Fold keys where the live file drifted from the last-generated snapshot into the
    overlay (deep, additive, removals are NOT propagated, conservative)."""
    changed = False
    if not isinstance(current, dict) or not isinstance(snapshot, dict):
        return overlay, changed
    for k, cur in current.items():
        snap = snapshot.get(k)
        if isinstance(cur, dict) and isinstance(snap, dict):
            sub, ch = fold_drift(cur, snap, overlay.get(k, {}) if isinstance(overlay.get(k), dict) else {})
            if ch:
                overlay[k] = sub
                changed = True
        elif cur != snap:
            overlay[k] = cur
            changed = True
    return overlay, changed


def atomic_write(path, obj):
    d = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".settings-", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def main():
    opts = parse_args(sys.argv[1:])
    base = _load(opts["base"]) if opts["base"] else None
    if base is None:
        print("settingsmerge: base unreadable, leaving settings.json untouched", file=sys.stderr)
        if opts["mode"] == "eject":
            sys.exit(1)   # uninstall must not report Organism's hooks as removed
        return
    rendered = {}
    if opts["mode"] == "legacy":
        rendered = render_hooks(opts["hooks"], os.path.realpath(opts["root"]))
    overlay = _load(OVERLAY)
    if overlay is None and os.path.exists(OVERLAY) or overlay is not None and not isinstance(overlay, dict):
        # a hand edit that broke the overlay: writing over it would lose the user's settings
        print(f"settingsmerge: {OVERLAY} is not a JSON object; fix it and re-run "
              "(settings.json left untouched)", file=sys.stderr)
        sys.exit(1)
    overlay = overlay or {}
    snapshot = _load(SNAPSHOT)
    # a settings.json that links somewhere else (a dotfiles repo, say) is the user's own
    # file; only the old deploy's link into a clone's claude/ folder is Organism's
    linked_dir = os.path.dirname(os.path.realpath(TARGET))
    user_file = os.path.isfile(TARGET) and (
        not os.path.islink(TARGET)
        or not os.path.isfile(os.path.join(linked_dir, "hooks", "lib", "settingsmerge.py")))

    # first deploy over a settings file Organism never generated: keep it.
    # it is copied aside and folded into the overlay, so the user's values win and their hooks run beside the base's.
    if snapshot is None and user_file:
        bak = backup_path()
        shutil.copy2(TARGET, bak)
        current = _load(TARGET)
        if isinstance(current, dict):
            overlay = deep_merge(current, overlay)   # an overlay already on disk still wins
            os.makedirs(os.path.dirname(OVERLAY), exist_ok=True)
            atomic_write(OVERLAY, overlay)
            print(f"settingsmerge: kept your existing settings (original copied to {bak})")
        else:
            print(f"settingsmerge: existing settings.json was unreadable; copied to {bak}, not merged",
                  file=sys.stderr)

    # drift fold: only when the live target is a REAL file we previously generated
    if snapshot is not None and user_file:
        current = _load(TARGET)
        if current is not None:
            overlay, changed = fold_drift(current, snapshot, overlay)
            if changed:
                os.makedirs(os.path.dirname(OVERLAY), exist_ok=True)
                atomic_write(OVERLAY, overlay)
                print("settingsmerge: folded live drift into overlay")
        else:
            # a hand edit that broke the JSON: keep it, the rewrite below would lose it
            bak = backup_path()
            shutil.copy2(TARGET, bak)
            print(f"settingsmerge: settings.json was unreadable; copied to {bak}, not merged",
                  file=sys.stderr)

    # Organism's hooks come from hooks.json only (header): strip them from the overlay,
    # where an earlier drift fold may have copied them, and from the base.
    cleaned = strip_ours(overlay)
    if opts["mode"] == "eject":
        cleaned = eject_plugin(cleaned)
    if cleaned != overlay:
        if os.path.isfile(OVERLAY):
            bak = overlay_backup(OVERLAY)
            print(f"settingsmerge: removed Organism's own entries from the overlay (backup {bak})")
        os.makedirs(os.path.dirname(OVERLAY), exist_ok=True)
        atomic_write(OVERLAY, cleaned)
        overlay = cleaned

    merged = deep_merge(deep_merge(strip_ours(base), rendered), overlay)
    os.makedirs(os.path.dirname(TARGET), exist_ok=True)
    os.makedirs(os.path.dirname(SNAPSHOT), exist_ok=True)
    if os.path.islink(TARGET):   # the old link deploy, or a user's link already folded in above
        os.unlink(TARGET)
    atomic_write(TARGET, merged)
    atomic_write(SNAPSHOT, merged)
    print(f"settingsmerge: wrote {TARGET} (base ⊕ {'overlay' if overlay else 'no overlay'})")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"settingsmerge: {e!r} (settings.json may be partly updated; re-run deploy.sh)", file=sys.stderr)
        sys.exit(1)
