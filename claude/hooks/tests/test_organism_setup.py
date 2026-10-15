#!/usr/bin/env python3
"""tests for the organism setup wizard + its writers. Runs entirely against temp
targets (CLAUDE_PATHS_ENV / CLAUDE_HOST_JSON / a temp vault); never touches the
real machine. Run:  python3 claude/hooks/tests/test_organism_setup.py"""
import json
import os
import shutil
import sys
import tempfile

HOOKS = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, os.path.join(HOOKS, "lib"))

_fails = []


def check(name, cond):
    print(("  ✓ " if cond else "  ✗ ") + name)
    if not cond:
        _fails.append(name)


def test_pathsenv():
    print("pathsenv")
    fd, p = tempfile.mkstemp(suffix=".env")
    os.close(fd)
    os.environ["CLAUDE_PATHS_ENV"] = p
    import importlib
    import pathsenv
    importlib.reload(pathsenv)
    open(p, "w").write('# header\n\n# --- code ---\nCLAUDE_CODE_ROOT="$HOME/dev"   # base\nOTHER="keep"\n')
    pathsenv.set_keys({"CLAUDE_CODE_ROOT": "$HOME/code"})
    after = open(p).read()
    check("in-place update keeps inline comment", "# base" in after)
    check("sibling key untouched", 'OTHER="keep"' in after)
    check("section/header comments kept", "# --- code ---" in after and "# header" in after)
    check("value updated", 'CLAUDE_CODE_ROOT="$HOME/code"' in after)
    check("idempotent no-op on same value", pathsenv.set_keys({"CLAUDE_CODE_ROOT": "$HOME/code"}) == "")
    try:
        pathsenv.set_keys({"CLAUDE_CODE_ROOT": "$CLAUDE_VAULT_ROOT/x"}, write=False)
        check("rejects $CLAUDE_* on a Python-read key", False)
    except ValueError:
        check("rejects $CLAUDE_* on a Python-read key", True)
    ok = True
    try:
        pathsenv.set_keys({"CLAUDE_ROTATE_EXTRA_DIRS": "$CLAUDE_CODE_ROOT/logs"}, write=False)
    except ValueError:
        ok = False
    check("allows $CLAUDE_* on the shell-only key", ok)
    os.unlink(p)
    del os.environ["CLAUDE_PATHS_ENV"]


def test_hostjson():
    print("hostjson")
    fd, p = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    os.environ["CLAUDE_HOST_JSON"] = p
    import importlib
    import role
    importlib.reload(role)
    import hostjson
    importlib.reload(hostjson)
    hostjson.save({"roles": {"per_host": True, "singleton_bookkeeping": True, "singleton_sideeffect": True},
                   "platform_uuid": "UUID-A", "host_id": "id0", "short": "mini", "peers": ["book"]})
    hostjson.merge({"schedules": {"reconcile": "cal:8:15"}})
    d = json.load(open(p))
    check("roles preserved", d["roles"]["singleton_bookkeeping"] is True)
    check("platform_uuid preserved", d["platform_uuid"] == "UUID-A")
    check("short/peers preserved", d.get("short") == "mini" and d.get("peers") == ["book"])
    check("schedules merged", d.get("schedules") == {"reconcile": "cal:8:15"})
    os.unlink(p)
    del os.environ["CLAUDE_HOST_JSON"]


def test_appjson_and_organs():
    print("app.json prune + job/schedule validation")
    import importlib
    import organism_setup as w  # loaded via importlib below (hyphenated filename)
    v = tempfile.mkdtemp()
    for name in ("_vault", "Notion"):          # present top-level folders
        os.makedirs(os.path.join(v, name))
    os.makedirs(os.path.join(v, ".obsidian"))
    # neutral placeholder names only (no real private project codenames in code);
    # the two slash-wrapped / nested entries pin: never prune a /regex/
    # or anchored/nested pattern, and keep survivors byte-verbatim.
    stale = ["/node_modules/", "/.git/", "proj-a/", "proj-b/", "proj-c/", "proj-d/",
             "_vault/", "/attach.*\\.tmp/", "Notion/sub/"]
    json.dump({"userIgnoreFilters": stale, "newLinkFormat": "shortest", "livePreview": True},
              open(os.path.join(v, ".obsidian", "app.json"), "w"), indent=2)
    plan = w.appjson_plan(v)
    w.write_appjson(plan)
    data = json.load(open(os.path.join(v, ".obsidian", "app.json")))
    f = data["userIgnoreFilters"]
    check("hygiene set present", all(h in f for h in w.HYGIENE))
    check("present folder (_vault/) kept", "_vault/" in f)
    check("absent folders pruned", not any(x in f for x in ("proj-a/", "proj-b/", "proj-c/", "proj-d/")))
    check("regex filter never pruned", "/attach.*\\.tmp/" in f)
    check("nested pattern kept verbatim", "Notion/sub/" in f)
    check("editor prefs preserved", data.get("newLinkFormat") == "shortest" and data.get("livePreview") is True)
    check("schedule grammar cal:8:15 valid", bool(w.SCHED_RE.match("cal:8:15")))
    check("schedule grammar cal:99 invalid", not w.SCHED_RE.match("cal:99"))
    check("valid_organs parses the AGENTS manifest", "reconcile" in w.valid_organs())
    import shutil
    shutil.rmtree(v)


# ── the wizard end to end, as a subprocess in a temp HOME ─────────────────────
WIZ = os.path.join(HOOKS, "organism-setup.py")


def _fresh_home():
    """A temp HOME with a vault at ~/Documents/MyVault (.obsidian) and one git repo in
    ~/code, neither in the old default places (~/Projects, ~/dev)."""
    import shutil
    import subprocess
    h = tempfile.mkdtemp(prefix="setup-e2e-")
    os.makedirs(os.path.join(h, "Documents", "MyVault", ".obsidian"))
    repo = os.path.join(h, "code", "app")
    os.makedirs(repo)
    subprocess.run(["git", "init", "-q", repo], check=True)
    os.makedirs(os.path.join(h, ".claude", "state"))
    shutil.copy(os.path.join(os.path.dirname(os.path.dirname(HOOKS)), "config", "paths.env.example"),
                os.path.join(h, ".claude", "state", "paths.env"))
    return h


def _env(h):
    e = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE_", "GIT_"))}
    e.update(HOME=h, NO_COLOR="1", ORGANISM_DRY="1")
    e.pop("RECONCILE_CONFIG", None)
    # a clone that holds its own (gitignored) pairings file would be found first; point
    # the wizard at the sandbox's state copy so the test sees only its own HOME
    if os.path.exists(os.path.join(HOOKS, "reconcile-pairings.json")):
        e["RECONCILE_CONFIG"] = os.path.join(h, ".claude", "state", "reconcile-pairings.json")
    return e


def _state(h):
    out = {}
    for name in ("paths.env", "host.json"):
        try:
            out[name] = open(os.path.join(h, ".claude", "state", name)).read()
        except FileNotFoundError:
            out[name] = None
    out["pairings"] = os.path.exists(os.path.join(h, ".claude", "state", "reconcile-pairings.json"))
    out["wiki"] = os.path.exists(os.path.join(h, "Documents", "MyVault", "wiki"))
    return out


def _run(h, args, stdin=None):
    import subprocess
    return subprocess.run([sys.executable, WIZ] + args, env=_env(h), capture_output=True, text=True,
                          stdin=stdin if stdin is not None else subprocess.DEVNULL, timeout=120)


def _run_tty(h, args, answers):
    """Run the wizard on a pseudo-terminal. `answers` maps a prompt substring to the
    reply; any other prompt gets Enter (its default). Returns the full transcript."""
    import pty
    import select
    import subprocess
    import time
    master, slave = pty.openpty()
    proc = subprocess.Popen([sys.executable, WIZ] + args, env=_env(h), stdin=slave, stdout=slave,
                            stderr=slave, close_fds=True)
    os.close(slave)
    buf, answered = b"", 0
    deadline = time.time() + 120
    while time.time() < deadline:
        r, _, _ = select.select([master], [], [], 0.4)
        if r:
            try:
                chunk = os.read(master, 4096)
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
            continue
        if proc.poll() is not None:
            break
        tail = buf[answered:].decode("utf-8", "replace")
        last = tail.rsplit("\n", 1)[-1]
        if last.rstrip().endswith((":", "]")) and last.strip():
            reply = next((a for k, a in answers.items() if k in last), "")
            os.write(master, (reply + "\n").encode())
            answered = len(buf)
    proc.wait(timeout=30)
    os.close(master)
    return buf.decode("utf-8", "replace"), proc.returncode


def test_wizard_end_to_end():
    import shutil
    print("the wizard end to end (temp HOME)")
    h = _fresh_home()
    try:
        before = _state(h)
        r = _run(h, [])
        check("no terminal + no flags → refuses (exit 2)", r.returncode == 2)
        check("…says why", "no terminal to ask in" in r.stderr)
        check("…and writes nothing", _state(h) == before)
        r = _run(h, ["--section", "verify"])
        check("a read-only section still runs without a terminal", r.returncode == 0)

        r = _run(h, ["--print"])
        check("--print writes nothing", _state(h) == before)
        check("--print finds the vault outside ~/Projects", "Documents/MyVault" in r.stdout)
        check("--print plans the notes folder inside the vault",
              'CLAUDE_OBSIDIAN_MIRROR_DIR="$HOME/Documents/MyVault/_claude-live"' in r.stdout)
        check("--print finds the code outside ~/dev", 'CLAUDE_CODE_ROOT="$HOME/code"' in r.stdout)

        out, rc = _run_tty(h, ["--no-apply"], {"Set up the doc check": "y", "write these changes?": "n"})
        # a clone in daily use may keep its own (gitignored) pairings file, which setup finds,
        # so it never offers the doc check there; only a clone without one shows the offer
        own_pairings = os.path.exists(os.path.join(HOOKS, "reconcile-pairings.json"))
        if not own_pairings:
            check("on a terminal, the doc check was offered and staged", "doc check →" in out)
        check("…and answering n at the end aborts", rc == 0 and "aborted: nothing written" in out)
        check("…writes nothing: no config, no pairings, no wiki pages", _state(h) == before)

        r = _run(h, ["--yes", "--no-apply"])
        st = _state(h)
        check("--yes writes the vault and its notes folder",
              'CLAUDE_VAULT_ROOT="$HOME/Documents/MyVault"' in st["paths.env"]
              and 'CLAUDE_OBSIDIAN_MIRROR_DIR="$HOME/Documents/MyVault/_claude-live"' in st["paths.env"])
        roles = json.loads(st["host.json"] or "{}").get("roles", {})
        check("--yes claims no shared job (a second Mac stays safe)",
              not roles.get("singleton_bookkeeping") and not roles.get("singleton_sideeffect"))
        check("--yes leaves the experimental doc check off", not st["pairings"] and not st["wiki"])
        check("the ready card points at the vault", "saved to your vault" in r.stdout
              and "Documents/MyVault/_claude-live" in r.stdout)

        out, rc = _run_tty(h, ["--no-apply"], {"Set up the doc check": "y", "write these changes?": "y"})
        st = _state(h)
        if own_pairings:
            print("  - this clone has its own pairings file: doc-check offer checks skipped")
        else:
            check("on a terminal, yes to the doc check + yes at the end → pairings written", st["pairings"])
            check("…with a page per repo in a new wiki folder",
                  os.path.isfile(os.path.join(h, "Documents", "MyVault", "wiki", "Repos", "app.md")))
        check("…and the wiki folder recorded in paths.env",
              'CLAUDE_VAULT_WIKI="$HOME/Documents/MyVault/wiki"' in st["paths.env"])
    finally:
        shutil.rmtree(h, ignore_errors=True)

    print("a named vault gets every default, not a search")
    h = _fresh_home()
    try:
        v = os.path.join(h, "Documents", "MyVault")
        r = _run(h, ["--print", "--vault", v])
        check("--vault on a folder with .obsidian proposes its notes folder",
              'CLAUDE_OBSIDIAN_MIRROR_DIR="$HOME/Documents/MyVault/_claude-live"' in r.stdout)
        check("…and a wiki folder for the doc check", 'CLAUDE_VAULT_WIKI="$HOME/Documents/MyVault/wiki"' in r.stdout)
        plain = os.path.join(h, "notes")
        os.makedirs(plain)
        r = _run(h, ["--print", "--vault", plain])
        check("--vault on a plain folder (no .obsidian) works too",
              'CLAUDE_OBSIDIAN_MIRROR_DIR="$HOME/notes/_claude-live"' in r.stdout)
    finally:
        shutil.rmtree(h, ignore_errors=True)


def test_apply_from_plugin_cache():
    print("organism-setup: apply from a plugin cache copy")
    import subprocess
    s = tempfile.mkdtemp(prefix="setup-cache-")
    try:
        root = os.path.dirname(os.path.dirname(HOOKS))
        c = os.path.join(s, "plugins", "cache", "organism", "organism", "0.1.0")
        os.makedirs(c)
        listed = subprocess.run(["git", "-C", root, "ls-files", "-z", "--cached", "--others",
                                 "--exclude-standard"], capture_output=True).stdout.split(b"\0")
        files = b"\0".join(f for f in listed if f and os.path.lexists(os.path.join(root, f.decode())))
        tar = subprocess.run(["tar", "--null", "-T", "-", "-cf", "-"], cwd=root, input=files,
                             capture_output=True).stdout
        subprocess.run(["tar", "-xf", "-", "-C", c], input=tar, check=True)
        h = os.path.join(s, "home")
        os.makedirs(os.path.join(h, ".claude", "state"))
        e = _env(h)
        e["CLAUDE_CODE_PLUGIN_CACHE_DIR"] = os.path.join(s, "plugins")
        r = subprocess.run([sys.executable, os.path.join(c, "claude", "hooks", "organism-setup.py"),
                            "--section", "apply", "--yes"], env=e, capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=120)
        check("apply says it won't run from the plugin cache", "plugin cache" in r.stdout)
        check("…and runs neither deploy.sh nor install-agents.sh",
              "running deploy.sh" not in r.stdout and not os.path.lexists(os.path.join(h, ".claude", "hooks")))
    finally:
        shutil.rmtree(s, ignore_errors=True)


def _load_wizard():
    import importlib.util
    spec = importlib.util.spec_from_file_location("organism_setup", os.path.join(HOOKS, "organism-setup.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    sys.modules["organism_setup"] = m


if __name__ == "__main__":
    _load_wizard()
    test_pathsenv()
    test_hostjson()
    test_appjson_and_organs()
    test_wizard_end_to_end()
    test_apply_from_plugin_cache()
    print(f"\n{'ALL PASS' if not _fails else 'FAILED: ' + ', '.join(_fails)}")
    sys.exit(1 if _fails else 0)
