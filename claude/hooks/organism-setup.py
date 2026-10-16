#!/usr/bin/env python3
"""organism setup: guided, idempotent machine configuration for the harness.

Fills the machine-local config the harness reads: repo paths + vault paths
(~/.claude/state/paths.env), roles + schedules + short/peers (host.json), and
Obsidian indexing hygiene (.obsidian/app.json), with detection + prompts, so
onboarding a Mac is a wizard instead of hand-editing files. Reuses hostid /
host-role / paths / install-agents; adds only the small missing writers.

  python3 ~/.claude/hooks/organism-setup.py            interactive (Enter keeps the shown value)
    --print / --dry-run   compute + show the paths.env/host.json/app.json diffs; write nothing
    --yes                 accept every default it shows (optional extras stay off), then write + apply
    --doc-check           offer the experimental doc check with a yes default (with --yes: turn it on)
    --non-interactive     same as --yes
    --section <name>      run one section: identity|repos|vault|pairings|role|schedules|peers|creds|apply|verify
    --no-apply            do everything except the deploy.sh + install-agents.sh re-run
  env CLAUDE_HOST_JSON / CLAUDE_PATHS_ENV, --vault <dir>   redirect targets (tests / temp vault)

Asks before it writes: nothing is written until the final "write these changes?" (or
--yes), and without a terminal to ask in it refuses unless --print or --yes is given.
Never reads or writes secrets. Per-host-safe: no machine takes the shared (singleton)
jobs unless you type "claim owner"; --yes keeps the current role.
"""
import argparse
import glob
import json
import os
import re
import subprocess
import sys

_HERE = os.path.dirname(os.path.realpath(__file__))          # .../.organism/claude/hooks
REPO = os.path.dirname(os.path.dirname(_HERE))               # .../.organism
sys.path.insert(0, os.path.join(_HERE, "lib"))
import hostid                       # noqa: E402
import paths as _paths             # noqa: E402
import pathsenv                    # noqa: E402
import hostjson                    # noqa: E402
import role as _role               # noqa: E402
import where                       # noqa: E402

HYGIENE = ["/node_modules/", "/dist/", "/build/", "/.venv/", "/site-packages/", "/.git/"]
SCHED_RE = re.compile(r"^(keepalive|interval:\d+|cal:\d+:\d+|calw:[0-6]:\d+:\d+)$")
ALL_SECTIONS = ["identity", "repos", "vault", "pairings", "role", "schedules", "peers", "creds", "apply", "verify"]
READ_ONLY_SECTIONS = {"identity", "creds", "verify"}   # never write, so they need no terminal
# Where a vault is looked for when none is named, in order (each searched 2 levels deep):
# the usual homes of an Obsidian vault, then the home folder itself.
VAULT_SEARCH = ["$HOME/Projects", "$HOME/Documents",
                "$HOME/Library/Mobile Documents/iCloud~md~obsidian/Documents", "$HOME"]
# Where code projects usually live; the first that holds a git repo is the default.
CODE_ROOTS = ["$HOME/dev", "$HOME/code", "$HOME/src", "$HOME/Developer", "$HOME/repos",
              "$HOME/workspace", "$HOME/Projects"]

# ─── ui helpers ────────────────────────────────────────────────────────────
def _tty_colors():
    """ANSI palette gated the conventional way: a real terminal (or CLICOLOR_FORCE=1),
    never when NO_COLOR is set. Off = empty strings, so output stays byte-identical
    for pipes, logs, and the test suite."""
    on = (os.environ.get("CLICOLOR_FORCE") == "1"
          or (sys.stdout.isatty() and not os.environ.get("NO_COLOR")))
    if not on:
        return {k: "" for k in ("B", "DIM", "G", "R", "Y", "C", "X")}
    return {"B": "\033[1m", "DIM": "\033[2m", "G": "\033[32m", "R": "\033[31m",
            "Y": "\033[33m", "C": "\033[36m", "X": "\033[0m"}


_C = _tty_colors()


def hr(t):
    print(f"\n{_C['B']}{_C['C']}━ {t} {_C['X']}{_C['DIM']}" + "─" * max(2, 60 - len(t)) + _C["X"])


def say(m):
    if m.startswith("✓"):
        m = _C["G"] + "✓" + _C["X"] + m[1:]
    elif m.startswith("✗"):
        m = _C["R"] + "✗" + _C["X"] + m[1:]
    elif m.startswith("⚠"):
        m = _C["Y"] + m + _C["X"]
    print(f"  {m}")


def prompt(label, default="", mode="interactive"):
    if mode != "interactive":
        return default
    try:
        r = input(f"  {_C['B']}{label}{_C['X']} {_C['DIM']}[{default}]{_C['X']}: ").strip()
    except EOFError:
        return default
    return r or default


def confirm(label, default=True, mode="interactive"):
    # --yes accepts the DEFAULT shown, like Enter would: a default-No question (an
    # optional extra) stays No, so --yes never switches on something it didn't ask about.
    if mode in ("yes", "print", "noninteractive"):
        return default
    try:
        r = input(f"  {label} [{'Y/n' if default else 'y/N'}]: ").strip().lower()
    except EOFError:
        return default
    return default if not r else r.startswith("y")


def homeify(p):
    home = os.environ.get("HOME") or os.path.expanduser("~")
    return "$HOME" + p[len(home):] if p and p.startswith(home) else p


def expand(p):
    return os.path.expandvars(os.path.expanduser(p or ""))


# ─── discovery ─────────────────────────────────────────────────────────────
def _find(root, name, depth):
    try:
        return subprocess.run(
            ["find", root, "-maxdepth", str(depth), "-type", "d", "-name", name,
             "-not", "-path", "*/node_modules/*"],
            capture_output=True, text=True, timeout=20).stdout.split("\n")
    except Exception:
        return []


def discover_repos(code_root):
    # realpath: a symlinked base (macOS /var -> /private/var, or a linked ~/dev) must
    # still yield RELATIVE repo names, or every downstream id/path goes absolute.
    base = os.path.realpath(expand(code_root).rstrip("/"))
    roots = sorted({os.path.dirname(h) for h in _find(base, ".git", 4) if h.strip()})
    rels = []
    for r in roots:
        rr = os.path.realpath(r)
        rels.append(os.path.relpath(rr, base) if rr.startswith(base + "/") else rr)
    return rels


def derive_vault_keys(vr, assume=False):
    """Derive docs-layer keys from a vault root. The session-notes folder is always
    proposed (<vault>/_claude-live, created on the first reply): saving sessions into
    the vault is the core feature, so it must never depend on the folder existing
    already. For the other keys, assume=False (auto-discovery) names only subdirs that
    exist; assume=True (the user pointed at the folder explicitly) also fills sensible
    defaults for missing ones; the tools create them on first use. The 'vault' is any
    folder of markdown; Obsidian is a viewer, not a requirement."""
    d = {"CLAUDE_VAULT_ROOT": homeify(vr)}

    def add(k, rels, default=None):
        for rel in rels:
            p = os.path.join(vr, rel)
            if os.path.exists(p):
                d[k] = homeify(p)
                return
        if assume and default:
            d[k] = homeify(os.path.join(vr, default))
    add("CLAUDE_VAULT_WIKI", ["wiki", "Notion", "docs/wiki"], default="wiki")
    d["CLAUDE_OBSIDIAN_MIRROR_DIR"] = homeify(os.path.join(vr, "_claude-live"))
    add("CLAUDE_VAULT_ADR_DIR", ["docs/adr"], default="docs/adr")
    # Publish-guard denylist: probe anywhere under the vault for the user's
    # "Confidential Terms.md" (the guard's documented denylist page); an existing
    # paths.env value wins in sec_vault.
    g = glob.glob(os.path.join(vr, "**", "Confidential Terms.md"), recursive=True)
    if g:
        d["GUARD_PUBLISH_DENYLIST"] = homeify(sorted(g)[0])
    return d


def find_vaults(roots=None):
    """Every folder holding a .obsidian dir, up to 2 levels under each search root,
    in search order (first root first, sorted within a root), without duplicates."""
    out = []
    for root in roots or VAULT_SEARCH:
        base = expand(root)
        if not os.path.isdir(base):
            continue
        for h in sorted(h for h in _find(base, ".obsidian", 2) if h.strip()):
            vr = os.path.dirname(h)
            if vr not in out:
                out.append(vr)
    return out


def discover_vault(roots=None):
    """(vault, derived keys, every vault found) for the first vault found, or (None, {}, [])."""
    hits = find_vaults(roots)
    if not hits:
        return None, {}, []
    return hits[0], derive_vault_keys(hits[0]), hits


def default_code_root():
    """The first usual code folder that holds a git repo; $HOME/dev when none does."""
    for root in CODE_ROOTS:
        if os.path.isdir(expand(root)) and discover_repos(root):
            return root
    return "$HOME/dev"


def explicit_vault(root):
    """The user named the folder (--vault or CLAUDE_VAULT_ROOT): accept it as the
    vault with no .obsidian required: plain-folder users are first-class."""
    vr = expand(root)
    if not os.path.isdir(vr):
        return None, {}
    return vr, derive_vault_keys(vr, assume=True)


# ─── .obsidian/app.json userIgnoreFilters ────────────────────────
def reconcile_filters(vault_root, cur):
    keep = list(HYGIENE)
    for e in cur:
        if e in HYGIENE:
            continue
        # Prune ONLY entries that are unambiguously a bare top-level folder path
        # ("Foo/"): anything anchored ("/Foo/": Obsidian reads slash-wrapped entries
        # as /regex/), nested, regex-ish, or an extension pattern is kept VERBATIM
        # (a /regex/ filter would otherwise strip to a non-dir and be pruned as a
        # "gone folder", and kept "/Foo/" entries would lose their anchor, auto-applied
        # without review under --yes).
        bare = (e.endswith("/") and not e.startswith("/") and "/" not in e[:-1]
                and not re.search(r"[\\^$.|?*+()\[\]{}]", e[:-1]))
        if bare and not os.path.isdir(os.path.join(vault_root, e[:-1])):
            continue                                          # folder gone (moved to ~/dev) → prune
        keep.append(e)                                        # everything else → keep verbatim
    return keep


def appjson_plan(vault_root):
    app = os.path.join(vault_root, ".obsidian", "app.json")
    try:
        with open(app) as f:
            data = json.load(f)
    except Exception:
        return None
    cur = data.get("userIgnoreFilters", [])
    new = reconcile_filters(vault_root, cur)
    return {"path": app, "data": data, "cur": cur, "new": new, "changed": new != cur}


def write_appjson(plan):
    plan["data"]["userIgnoreFilters"] = plan["new"]
    tmp = plan["path"] + ".tmp"
    with open(tmp, "w") as f:
        json.dump(plan["data"], f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, plan["path"])


# ─── schedule jobs (validation) ──────────────────────────────────────────
def valid_organs():
    organs = set()
    try:
        txt = open(os.path.join(REPO, "launchd", "install-agents.sh")).read()
        m = re.search(r'AGENTS="\n(.*?)\n"', txt, re.S)
        if m:
            for ln in m.group(1).splitlines():
                ln = ln.strip()
                if ln and "|" in ln:
                    organs.add(ln.split("|", 1)[0].strip())
    except Exception:
        pass
    try:
        for ln in open(os.path.expanduser("~/.claude/state/agents.local")):
            ln = ln.strip()
            if ln and not ln.startswith("#") and "|" in ln:
                organs.add(ln.split("|", 1)[0].strip())
    except Exception:
        pass
    return organs


# ─── sections ──────────────────────────────────────────────────────────────
def sec_identity():
    hr("Identity")
    pinned, live = "", hostid.platform_uuid()
    say(f"device : {hostid.host_label()}")
    say(f"host_id: {hostid.host_id()}    uuid {live[:13]}…")


def sec_repos(raw, mode, pu):
    hr("Code repos")
    # Every project where /handoff runs gets its note, so there is no per-project
    # question here. The doc check's "leave out" list is asked in its own, opt-in section.
    code_root = prompt("Where do your code projects live? [CLAUDE_CODE_ROOT]",
                       raw.get("CLAUDE_CODE_ROOT") or default_code_root(), mode)
    pu["CLAUDE_CODE_ROOT"] = code_root
    repos = discover_repos(code_root)
    say("scanning %s … found: %s" % (code_root, ", ".join(repos) or "(none)"))
    # Rotate dirs default OFF, matching paths.env.example (a committed default
    # must not bake in private repo codenames). Discovered <repo>/logs dirs are OFFERED as a suggestion, never
    # pre-filled, so --yes on a fresh machine writes nothing it didn't ask for.
    if not raw.get("CLAUDE_ROTATE_EXTRA_DIRS"):
        logdirs = ["$CLAUDE_CODE_ROOT/%s/logs" % r for r in repos
                   if os.path.isdir(os.path.join(expand(code_root), r, "logs"))]
        if logdirs:
            say("log dirs you could rotate: " + " ".join(logdirs))
    rot = prompt("Extra log folders to clean weekly (optional)", raw.get("CLAUDE_ROTATE_EXTRA_DIRS", ""), mode)
    pu["CLAUDE_ROTATE_EXTRA_DIRS"] = rot
    return code_root, repos


def sec_vault(raw, mode, pu, args, staged):
    hr("Vault + Obsidian")
    # A folder the user NAMED (--vault, or the vault this machine already uses) is
    # the vault as given, .obsidian or not, and gets every default. Only an unnamed
    # vault is searched for. (A named folder that happened to contain .obsidian must not
    # go down the search path, which names only existing subfolders, or a fresh vault
    # would get neither a notes folder nor a wiki for the doc check.)
    named = args.vault or raw.get("CLAUDE_VAULT_ROOT")
    vr, derived, others = None, {}, []
    if named:
        vr, derived = explicit_vault(named)
        if vr:
            say(f"using {homeify(vr)} as the vault"
                + ("" if os.path.isdir(os.path.join(vr, ".obsidian")) else " (plain folder: no .obsidian needed)"))
        else:
            say(f"{homeify(named)} doesn't exist: looking for a vault the usual way instead")
    if not vr:
        vr, derived, others = discover_vault()
        if vr:
            say(f"found .obsidian → {homeify(vr)}")
            if len(others) > 1:
                say("⚠ other vaults found: " + ", ".join(homeify(o) for o in others[1:])
                    + "; to use one of them instead: organism setup --vault <dir>")
    if not vr:
        say("no vault found (looked in ~/Projects, ~/Documents, Obsidian's iCloud folder and ~) "
            "; pass --vault <dir> (any folder of markdown works); session notes stay off until then.")
        return
    for k in ("CLAUDE_VAULT_ROOT", "CLAUDE_VAULT_WIKI", "CLAUDE_VAULT_ADR_DIR",
              "CLAUDE_OBSIDIAN_MIRROR_DIR", "GUARD_PUBLISH_DENYLIST"):
        # The machine's own paths.env value wins over the probe (a key deliberately set
        # to "" stays off); a key neither set nor discovered is skipped. This is what
        # lets the probe stay generic: the strict
        # sub-wiki is only ever named by gitignored config or the filesystem, never code.
        dv = raw.get(k, derived.get(k, ""))
        if dv:
            pu[k] = prompt(k, dv, mode)
    plan = appjson_plan(vr)
    if plan and plan["changed"]:
        pruned = [e for e in plan["cur"] if e not in plan["new"]]
        say("app.json userIgnoreFilters: keep %d, PRUNE %s (folders no longer in the vault)"
            % (len(plan["new"]), " ".join(pruned) or "(none)"))
        if confirm("apply this app.json cleanup?", True, mode):
            staged["appjson"] = plan
    elif plan:
        say("app.json userIgnoreFilters already clean ✓")




def pairings_path():
    """The pairings file in use (lib/where.py's read order), else where a new one goes."""
    return where.pairings() or where.pairings_write_path()


def sec_pairings(raw, mode, pu, args, code_root, repos, staged):
    """Offer the experimental doc check: one vault page per repo whose fenced status
    table the checker keeps current, the Reconcile Queue page, and the pairings file
    that ties them together. OPT-IN: the question defaults to No, so
    --yes leaves it off, and nothing is written here: the plan is staged and written
    only after the final "write these changes?". Cold-start design: probes are
    volatile (display-only, zero alarms) so a fresh install gets a live dashboard, not
    noise; guards are the user's to add. The stub page body is seeded from the
    repo's own docs/adr/ titles. NEVER touches an existing pairings file (a hand-tuned
    config outranks generation)."""
    hr("Doc check (experimental, optional)")
    cfg_path = pairings_path()
    if os.path.exists(cfg_path):
        try:
            head = open(cfg_path).read(4096)
        except Exception:
            head = ""
        if "__VAULT_OPS__" in head or "SANITIZED EXAMPLE" in head:
            say("seeded placeholder pairings found: they can be replaced with real values")
        else:
            say(f"✓ {homeify(cfg_path)} exists: the doc check is set up (generation skipped: hand-tuned config wins)")
            return
    vault = pu.get("CLAUDE_VAULT_ROOT") or raw.get("CLAUDE_VAULT_ROOT") or ""
    wiki_key = pu.get("CLAUDE_VAULT_WIKI") or raw.get("CLAUDE_VAULT_WIKI") or ""
    if not wiki_key and vault:
        wiki_key = homeify(os.path.join(expand(vault), "wiki"))   # proposed; created only on yes
    if not wiki_key:
        say("no vault set: the doc check writes its pages into your vault, so it is skipped")
        return
    if not repos:
        say(f"no git repos found under {code_root}: nothing to check, skipped")
        return
    wiki = expand(wiki_key)
    say(f"Keeps a status table (latest commit, branch, uncommitted files, decision count) in one")
    say(f"page per repo under {homeify(wiki)}/Repos, current at every session end and on `organism check`.")
    say("It never reads your prose and warns only on checks you add yourself.")
    if not confirm(f"Set up the doc check for {len(repos)} repo(s)?", bool(getattr(args, "doc_check", False)), mode):
        say("off (turn it on later: organism setup --doc-check)")
        return
    ex = prompt("Any repos to leave out? (space-separated; empty = none)",
                raw.get("CLAUDE_RECONCILE_EXCLUDE", ""), mode)
    pu["CLAUDE_RECONCILE_EXCLUDE"] = ex
    exclude = set(ex.split())
    watch = [r for r in repos if r not in exclude]
    if not watch:
        say("no repos left to check (all left out): skipped")
        return
    if not (pu.get("CLAUDE_VAULT_WIKI") or raw.get("CLAUDE_VAULT_WIKI")):
        pu["CLAUDE_VAULT_WIKI"] = wiki_key
    base = expand(code_root).rstrip("/")
    pagedir = os.path.join(wiki, "Repos")
    queue = os.path.join(wiki, "Reconcile Queue.md")
    pages, pairs = [], []
    for r in watch:
        rid = r.replace("/", "-")
        page = os.path.join(pagedir, f"{rid}.md")
        if not os.path.exists(page):
            adrs = sorted(glob.glob(os.path.join(base, r, "docs", "adr", "[0-9]*.md")))[:30]
            body = [f"# {rid}", "", f"Paired to `{r}` by organism setup. Prose above the fence is yours; "
                    "the fenced block below is machine-written on every reconcile run.", ""]
            if adrs:
                body += ["## Decisions (seeded from docs/adr at pairing time; curate freely; "
                         "the live count is in the fenced table below)", ""]
                for a in adrs:
                    body.append(f"- {os.path.basename(a)[:-3]}")
                body.append("")
            body += ["<!-- AUTO:BUILT-STATE:BEGIN -->", "<!-- AUTO:BUILT-STATE:END -->", ""]
            pages.append((page, "\n".join(body), len(adrs)))
        pairs.append({
            "id": rid, "repo": os.path.join(base, r), "repo_short": r, "git": True,
            "page": page, "heading": f"## Built state: auto-derived from `{r}`",
            "header_note": "> [!info] Machine-written on {today} at commit `{head}`: do not hand-edit inside the fence.",
            "probes": {
                "head":   {"cmd": "git log -1 --format='%h %ad %s' --date=short 2>/dev/null | head -c 120", "volatile": True},
                "branch": {"cmd": "git rev-parse --abbrev-ref HEAD 2>/dev/null", "volatile": True},
                "dirty":  {"cmd": "git status --porcelain 2>/dev/null | wc -l | tr -d ' '", "volatile": True},
                "adrs":   {"cmd": "ls docs/adr/[0-9]*.md 2>/dev/null | wc -l | tr -d ' '", "volatile": True},
                "last_adr": {"cmd": "ls docs/adr/[0-9]*.md 2>/dev/null | tail -1 | xargs -n1 basename 2>/dev/null | sed 's/.md$//' | grep . || echo '(none yet)'", "volatile": True},
            },
            "guards": {},
            "rows": [
                {"component": "Latest commit", "status": "`{head}`", "evidence": "git log -1"},
                {"component": "Branch", "status": "`{branch}`", "evidence": "git rev-parse --abbrev-ref HEAD"},
                {"component": "Uncommitted changes", "status": "{dirty} file(s)", "evidence": "git status --porcelain \\| wc -l"},
                {"component": "Decision records", "status": "{adrs} ADR(s), latest `{last_adr}`", "evidence": "ls docs/adr/"},
            ],
            "_comment": "generated by organism setup: probes are display-only; add guards to make a row alarm on drift",
        })
    staged["pairings"] = {"cfg_path": cfg_path, "cfg": {"queue_page": queue, "pairs": pairs},
                          "pagedir": pagedir, "pages": pages, "queue": queue,
                          "queue_new": not os.path.exists(queue)}
    say(f"will check {len(watch)} repo(s): {', '.join(watch)}"
        + (f"  (left out: {', '.join(sorted(exclude))})" if exclude else ""))


def write_pairings(plan):
    """Write a staged doc-check plan: pages first (never over an existing one), then
    the queue page, then the pairings file that points at them."""
    os.makedirs(plan["pagedir"], exist_ok=True)
    for page, body, n_adrs in plan["pages"]:
        if not os.path.exists(page):
            with open(page, "w") as f:
                f.write(body)
            say(f"✓ page {homeify(page)}" + (f"  ({n_adrs} ADRs seeded)" if n_adrs else ""))
    if plan["queue_new"] and not os.path.exists(plan["queue"]):
        with open(plan["queue"], "w") as f:
            f.write("# Reconcile Queue\n\nMachine alarms land between the markers; "
                    "triage, then run `reconcile.py --ack`.\n\n"
                    "<!-- AUTO:ALARMS:BEGIN -->\n<!-- AUTO:ALARMS:END -->\n")
        say(f"✓ created {homeify(plan['queue'])}")
    os.makedirs(os.path.dirname(plan["cfg_path"]), exist_ok=True)
    with open(plan["cfg_path"], "w") as f:
        json.dump(plan["cfg"], f, indent=2)
        f.write("\n")
    say(f"✓ wrote {homeify(plan['cfg_path'])} ({len(plan['cfg']['pairs'])} pair(s)); run it now: organism check")


def sec_role(mode):
    hr("Role")
    cfg = hostjson.load()
    roles = cfg.get("roles", {"per_host": True})
    owner = roles.get("singleton_bookkeeping") and roles.get("singleton_sideeffect")
    say("current: per_host=%s bookkeeping=%s sideeffect=%s%s" % (
        "on" if roles.get("per_host", True) else "off",
        "on" if roles.get("singleton_bookkeeping") else "off",
        "on" if roles.get("singleton_sideeffect") else "off",
        "   (this device is the OWNER)" if owner else ""))
    # Never a claim by default. This machine cannot see whether another
    # Mac already owns the shared jobs (each host.json is local), so a "no peers
    # listed → claim owner" default would make a second Mac set up with defaults run them too.
    if not owner:
        say("The shared job (the daily doc check) runs on one Mac only. If this is your only")
        say("Mac, answer 'claim owner' to run it here; on a second Mac, keep the default.")
    choice = prompt("This machine's role: [k]eep current / claim owner / release", "keep", mode)
    c = choice.strip().lower()
    if c in ("", "k", "keep"):
        return None
    if c == "release":
        return ["release"]
    if c.startswith("claim"):
        what = c.split()[1] if len(c.split()) > 1 else "owner"
        return ["claim", what]
    return None


def sec_schedules(mode):
    hr("Schedules (per-machine time overrides)")
    cfg = hostjson.load()
    sched = dict(cfg.get("schedules", {}))
    organs = valid_organs()
    if sched:
        say("current: " + json.dumps(sched))
    if mode != "interactive":
        return sched if sched != cfg.get("schedules", {}) else None
    while True:
        organ = prompt("Job to reschedule (blank = done; e.g. reconcile)", "", mode).strip()
        if not organ:
            break
        if organ not in organs:
            say(f"⚠ '{organ}' is not a known agent (known: {', '.join(sorted(organs))}): adding anyway.")
        tok = prompt(f"  token for {organ}", sched.get(organ, ""), mode).strip()
        if not tok:
            sched.pop(organ, None)
            continue
        if not SCHED_RE.match(tok):
            say("  ✗ invalid; grammar: keepalive | interval:N | cal:H:M | calw:W:H:M")
            continue
        sched[organ] = tok
    return sched if sched != cfg.get("schedules", {}) else None


def sec_peers(raw, mode, host_updates):
    hr("short / peers")
    cfg = hostjson.load()
    short = prompt("Short name for this machine", cfg.get("short", hostid.host_short()), mode).strip()
    if short and short != cfg.get("short"):
        host_updates["short"] = short
    peers = prompt("Other machines you run (optional, space-separated)", " ".join(cfg.get("peers", [])), mode).strip()
    pl = peers.split() if peers else []
    if pl != cfg.get("peers", []):
        host_updates["peers"] = pl


def sec_creds(code_root, repos):
    hr("Credentials checklist (validate + guide only, never stored here)")
    # The harness's OWN local state first: without these two
    # files every alert is a silent no-op and the weekly lint skips
    # log-only, and nothing else in onboarding mentions them. Validate + tell the
    # user; NEVER write or echo a secret value.
    home = os.environ.get("HOME") or os.path.expanduser("~")
    tg = os.path.join(home, ".claude", "state", "telegram-alerts.json")
    if os.path.exists(tg):
        try:
            with open(tg, encoding="utf-8") as f:
                creds = json.load(f)
            missing = [k for k in ("bot_token", "chat_id") if not creds.get(k)]
            perm = os.stat(tg).st_mode & 0o777
            if missing:
                say(f"✗ {homeify(tg)}: missing key(s) {', '.join(missing)}; no phone alerts "
                    "(macOS notifications still show)")
            elif perm & 0o077:
                say(f"⚠ {homeify(tg)}: perms {perm:03o}; run: chmod 600 {homeify(tg)}")
            else:
                say(f"✓ {homeify(tg)}  (bot_token + chat_id present, 0600)")
        except Exception:
            say(f"✗ {homeify(tg)}: unreadable/invalid JSON; alert.py needs {{\"bot_token\", \"chat_id\"}}")
    else:
        say(f"– {homeify(tg)} not set up; alerts show as macOS notifications only. For phone alerts "
            "create it with {\"bot_token\": …, \"chat_id\": …} and chmod 600 (docs/ADVANCED.md, Alerts)")
    base = expand(code_root)
    for rel in repos:
        repo = os.path.join(base, rel)
        examples = [e for e in glob.glob(os.path.join(repo, "**", ".env.example"), recursive=True)
                    if "/node_modules/" not in e and "/.venv" not in e]
        if examples:
            for ex in examples:
                envf = ex[:-len(".example")]
                mark = "✓" if os.path.exists(envf) else "✗"
                say(f"{mark} {rel}/{os.path.relpath(envf, repo)}  (template: {os.path.relpath(ex, repo)})")
        else:
            envs = [e for e in glob.glob(os.path.join(repo, "**", ".env"), recursive=True)
                    if "/node_modules/" not in e and "/.venv" not in e]
            if envs:
                for e in envs:
                    say(f"✓ {rel}/{os.path.relpath(e, repo)}  (no template in repo: consult it)")
            else:
                say(f"– {rel}: no .env / .env.example found; consult the repo's own setup")
    say("the harness never stores these; fill each repo's .env from its own template.")


def guide_obsidian():
    hr("Obsidian Sync GUIDE (cannot be scripted: account/keychain/IndexedDB)")
    for step in (
        "1. ⌘, → Account → sign in to Obsidian.",
        "2. NEW machine: Open → \"Sync with remote vault\" → pick your vault.  (first machine: create it.)",
        "3. Enter the end-to-end encryption password (from your password manager; unrecoverable).",
        "4. Selective sync: \"Sync all other file types\" OFF, \"Sync configuration folder\" ON.",
        "5. Excluded folders (exact-path UPLOAD list): the last defense against node_modules upload;",
        "   app.json above covers indexing only. Node projects in the vault must use pnpm",
        "   (engine-strict + engines.npm), whose .pnpm/symlink layout Obsidian never uploads.",
        "6. Turn Sync ON.",
    ):
        say(step)



# ─── apply / verify ────────────────────────────────────────────────────────
def apply(mode):
    hr("Apply")
    if where.in_cache(REPO):
        # a plugin copy is replaced on every update: linking ~/.claude into it, or pointing
        # launchd at it, would break at the next update
        say("this is the plugin Claude Code keeps in its plugin cache, so there is nothing")
        say("else to apply. Your settings above are saved; the hooks read them from the next session on.")
        return
    for script in (os.path.join(REPO, "deploy.sh"), os.path.join(REPO, "launchd", "install-agents.sh")):
        name = os.path.basename(script)
        say(f"running {name} …")
        r = subprocess.run(["bash", script], capture_output=True, text=True)
        tail = (r.stdout + r.stderr).strip().splitlines()[-3:]
        for ln in tail:
            print("    " + ln)


def verify():
    hr("Verify")
    subprocess.run([sys.executable, os.path.join(_HERE, "host-role.py"), "status"])


# ─── main ──────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(add_help=True, description="guided machine setup for the organism harness")
    ap.add_argument("--print", "--dry-run", dest="dry", action="store_true")
    ap.add_argument("--yes", "-y", action="store_true")
    ap.add_argument("--non-interactive", dest="noninteractive", action="store_true")
    ap.add_argument("--section", choices=ALL_SECTIONS)
    ap.add_argument("--no-apply", dest="no_apply", action="store_true")
    ap.add_argument("--vault")
    ap.add_argument("--doc-check", dest="doc_check", action="store_true")
    args = ap.parse_args()
    mode = "print" if args.dry else ("yes" if args.yes else ("noninteractive" if args.noninteractive else "interactive"))
    sections = [args.section] if args.section else ALL_SECTIONS
    if not args.section and where.in_cache(REPO):
        # a plugin copy runs no background jobs, so the role and schedule questions,
        # which only steer those jobs, are left out
        sections = [x for x in sections if x not in ("role", "schedules")]
    if mode == "interactive" and not sys.stdin.isatty() and set(sections) - READ_ONLY_SECTIONS:
        # No terminal to ask in (a script, a pipe, an agent's shell): input() would hit
        # EOF and every question would take its default, writing without asking.
        print("organism setup asks before it writes, and there is no terminal to ask in.\n"
              "Run it in a terminal, or pass --print to see the plan, or --yes to accept the defaults.",
              file=sys.stderr)
        sys.exit(2)

    print("\n  organism setup: configure THIS machine.  "
          "(Enter keeps the shown value; nothing is written before you confirm; ^C aborts.)")
    raw = pathsenv.read_raw()
    pu = {}                 # paths.env updates
    host_updates = {}       # host.json short/peers (+ schedules)
    role_action = None
    staged = {}             # {'appjson': plan, 'pairings': plan}: written only after the confirm
    code_root, repos = raw.get("CLAUDE_CODE_ROOT", "$HOME/dev"), []

    if "identity" in sections:
        sec_identity()
    if "repos" in sections:
        code_root, repos = sec_repos(raw, mode, pu)
    if "vault" in sections:
        sec_vault(raw, mode, pu, args, staged)
    if "pairings" in sections:
        # needs the repo scan even when --section pairings runs alone
        code_root = code_root or pu.get("CLAUDE_CODE_ROOT") or raw.get("CLAUDE_CODE_ROOT", "$HOME/dev")
        repos = repos or discover_repos(code_root)
        sec_pairings(raw, mode, pu, args, code_root, repos, staged)
    if "role" in sections:
        role_action = sec_role(mode)
    if "schedules" in sections:
        s = sec_schedules(mode)
        if s is not None:
            host_updates["schedules"] = s
    if "peers" in sections:
        sec_peers(raw, mode, host_updates)
    if "creds" in sections:
        # --section creds must not silently depend on the repos section having run
        # discover the repo list itself when needed.
        repos = repos or discover_repos(code_root)
        sec_creds(code_root, repos)
    if "vault" in sections:      # Sync guide belongs to the vault section, not to e.g. --section creds
        guide_obsidian()

    # ── review ──
    hr("Review & write")
    paths_diff = pathsenv.set_keys(pu, write=False) if pu else ""
    if paths_diff:
        print(paths_diff, end="")
    if host_updates:
        say("host.json += " + json.dumps(host_updates))
    if role_action:
        say("host.json role → host-role.py " + " ".join(role_action))
    if staged.get("appjson"):
        say("app.json → userIgnoreFilters " + json.dumps(staged["appjson"]["new"]))
    if staged.get("pairings"):
        pl = staged["pairings"]
        say(f"doc check → {homeify(pl['cfg_path'])} + {len(pl['pages'])} new page(s) under "
            f"{homeify(pl['pagedir'])}" + (" + the Reconcile Queue page" if pl["queue_new"] else ""))
    no_changes = not (paths_diff or host_updates or role_action or staged)
    if no_changes:
        say("no changes: this machine is already configured. ✓")

    if mode == "print":
        say("[--print] wrote nothing.")
        return
    # An EXPLICITLY requested apply/verify section runs even with no config diff
    # (otherwise `--section apply|verify` would hit the no-changes
    # early return before ever reaching apply()/verify()). A full
    # no-changes run still exits quietly, so an idempotent re-run never reloads agents.
    run_apply = not args.no_apply and "apply" in sections and (args.section == "apply" or not no_changes)
    run_verify = "verify" in sections and (args.section == "verify" or not no_changes)
    if no_changes and not (run_apply or run_verify):
        return
    if not no_changes:
        if not confirm("write these changes?", True, mode):
            say("aborted: nothing written.")
            return

        # ── write ──
        if pu:
            pathsenv.set_keys(pu, write=True)
            say("✓ wrote paths.env")
        if role_action:                               # host-role.py writes host.json + prints its own guard
            subprocess.run([sys.executable, os.path.join(_HERE, "host-role.py"), *role_action])
        if host_updates:
            hostjson.merge(host_updates)              # merges AFTER role write, preserving roles
            say("✓ wrote host.json")
        if staged.get("appjson"):
            write_appjson(staged["appjson"])
            say("✓ wrote .obsidian/app.json")
        if staged.get("pairings"):
            write_pairings(staged["pairings"])

    # ── apply + verify ──
    if run_apply:
        apply(mode)
    if run_verify:
        verify()
    if not args.dry and not args.section:
        ready_card()
    print("\n  done.")


def ready_card():
    hr("You're ready")
    import paths as _p
    notes = _p.mirror_dir()
    if notes:
        say(f"From now on, every Claude Code session is saved to your vault as you work ({homeify(notes)}).")
    else:
        say("Almost there: no vault is set yet, so sessions aren't being saved.")
        say(f"Point me at any folder of notes:  {_C['G']}organism setup --vault ~/my-notes{_C['X']}")
    say("")
    say(f"{_C['B']}Try these:{_C['X']}")
    if not where.in_cache(REPO):
        say(f"  - {_C['G']}organism status{_C['X']}   see your background jobs (or that none are scheduled)")
    if os.path.exists(pairings_path()):
        say(f"  - {_C['G']}organism check{_C['X']}    refresh the doc check's status tables now")
    say(f"  - after a real coding session, run {_C['G']}/handoff{_C['X']} inside Claude Code:")
    say("    it writes down what you decided, and your next session starts from it.")
    say("")
    say(f"Something wrong later? {_C['G']}organism doctor{_C['X']}. Everything else: README.md")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n  aborted: nothing written.")
        sys.exit(130)
