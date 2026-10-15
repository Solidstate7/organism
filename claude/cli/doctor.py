#!/usr/bin/env python3
"""organism doctor, install checks: the wiring matches the install mode.

  - plugin mode: no Organism hooks in settings.json or the overlay (they would run twice),
    and `claude plugin list --json` shows the plugin enabled and read from this clone
  - symlink mode: the plugin is not enabled as well (the hooks would run twice)
  - an unfinished `organism migrate`
  - clone installs: `organism` on PATH resolves into this clone, which the skills rely on

Exit 1 when a check fails, 0 otherwise. Read-only.
"""
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import installlib as L  # noqa: E402


def checks():
    root, mode = L.ROOT, L.where.mode(L.ROOT)
    target, overlay, _ = L.settings_paths()
    settings, over = L.load_json(target) or {}, L.load_json(overlay) or {}
    out = [(True, f"install mode: {mode} ({root})")]
    if mode.startswith("plugin"):
        for name, s in (("settings.json", settings), ("the settings overlay", over)):
            cmds = L.organism_hook_commands(s)
            out.append((not cmds, f"{name} has no Organism hooks" if not cmds else
                        f"{name} still runs Organism hooks beside the plugin (they run twice): "
                        f"{'; '.join(cmds)}. Fix: bash {root}/deploy.sh"))
    if mode == "plugin-inplace":
        entry = L.plugin_entry() if L.have("claude") else None
        if not L.have("claude"):
            out.append((True, "`claude` not on PATH; plugin state not checked"))
        elif not entry:
            out.append((False, f"{L.PLUGIN_ID} is not installed. Fix: organism migrate --to-plugin"))
        else:
            out.append((bool(entry.get("enabled")), "the plugin is enabled" if entry.get("enabled")
                        else f"the plugin is disabled. Fix: claude plugin enable {L.PLUGIN_ID}"))
            src = entry.get("readFromFolder") or ""
            out.append((L.same_path(src, root), "the plugin is read from this clone" if L.same_path(src, root)
                        else f"the plugin is read from {src or 'a copy'}, not {root}"))
            if entry.get("errors"):
                out.append((False, f"the plugin reports errors: {entry['errors']}"))
    if mode == "symlink" and L.plugin_enabled_in(settings):
        out.append((False, f"{L.PLUGIN_ID} is enabled while Organism's hooks are also in settings.json "
                    "(they run twice). Fix: organism migrate --to-plugin, or "
                    f"claude plugin disable {L.PLUGIN_ID}"))
    if "migration" in L.where.read_marker():
        out.append((False, "an `organism migrate --to-plugin` run did not finish. "
                    "Fix: run it again to resume, or organism migrate --to-symlink"))
    if mode != "plugin-copied":
        found = shutil.which("organism")
        ok = bool(found) and L.under(found, root)
        out.append((ok, f"`organism` on PATH is this clone's ({found})" if ok else
                    f"`organism` on PATH is {found or 'missing'}; the skills fall back to "
                    f"~/.local/bin/organism. Fix: ln -sf {root}/bin/organism ~/.local/bin/organism"))
    return out


def main():
    bad = 0
    print("Install checks")
    for ok, msg in checks():
        print(f"  {'✓' if ok else '✗'} {msg}")
        bad += not ok
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
