#!/usr/bin/env python3
"""organism fleet: run a set of Claude Code sessions, one per repo, and keep a ledger of their lives.

  organism fleet list                              every agent: lifecycle state, generations, brief date
  organism fleet up <name> [--effort E] [--dry]    summon: start a fresh session in the agent's repo
                                                   (resummon when it has lived before)
  organism fleet down <name> [--no-handoff] [--dry]
                                                   harvest: /harvest writes the brief, then /exit
  organism fleet ledger [--since 3d|12h|DATE] [--agent NAME] [--json] [--md PATH [--force]]
                                                   the ledger: every summon, order, report and harvest

An agent's life has four steps. Summon starts a fresh session that reads only the agent's
brief (HANDOFF.md). Work is the orders it gets and the reports it sends. Harvest runs
/harvest, which rewrites the brief, and then /exit. Resummon starts the next fresh session
from that brief. Each new session is one generation.

The roster is a JSON file, ~/.claude/state/organism-fleet.json ($ORGANISM_FLEET_CONFIG
moves it); docs/FLEET.md has an example. Without that file the fleet hooks do nothing.

The ledger is append-only JSON Lines, ~/.claude/state/fleet-ledger.jsonl
($ORGANISM_FLEET_LEDGER moves it). The hooks write it, so it also covers sessions started
by hand:
  SessionStart  in an agent's repo: summon, or resummon with the session it continues and
                the brief it starts from (path, mtime, hash); resume for `claude --resume`
  SessionEnd    in an agent's repo: harvest when the brief was rewritten during the
                session, else close (and why)
  PostToolUse   on SendMessage: order (to an agent), report (from an agent), message
                (agent to agent). Only the first line is kept, after secret redaction.

`up` and `down` drive the terminal through herdr-core (`hc`). Without it, `up` prints the
command to run and `down` refuses (run /harvest and /exit in the session yourself).
`up` answers one prompt itself: the folder-trust question, with Yes (the roster names the
repo). `down` acts on the session running in the agent's terminal and answers no dialog:
a permission request or a question during /harvest stops it with the session left open.
The one key it presses unasked is Esc on /exit's unsent-feedback-draft dialog, which
discards the draft. The hook never fails a session: a roster it can't read means no work.
Run:  python3 claude/hooks/fleet.py <command>
"""
import datetime
import fcntl
import glob
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.realpath(__file__))
HOME = os.environ.get("HOME") or os.path.expanduser("~")
DEFAULT_LAUNCH = "claude --model {model} --effort {effort}"
LIFE = ("summon", "resummon", "resume", "harvest", "close")
LINE_MAX = 200
MD_MARK = "<!-- written by organism fleet ledger; rewritten on each run -->"
NAME_RE = r"[A-Za-z0-9][A-Za-z0-9._-]*"
STR_FIELDS = ("repo", "brief", "model", "effort", "launch", "role")


def _tick():
    """Seconds per second of waiting in up and down; tests shrink it with ORGANISM_FLEET_TICK."""
    try:
        return max(float(os.environ.get("ORGANISM_FLEET_TICK") or 1), 0.0)
    except ValueError:
        return 1.0


TICK = _tick()


# ---- config and ledger -----------------------------------------------------------------
def config_path():
    return os.environ.get("ORGANISM_FLEET_CONFIG") or os.path.join(HOME, ".claude", "state", "organism-fleet.json")


def ledger_path():
    return os.environ.get("ORGANISM_FLEET_LEDGER") or os.path.join(HOME, ".claude", "state", "fleet-ledger.jsonl")


def _real(p):
    return os.path.realpath(os.path.expanduser(p))


def load_config(strict=True):
    """The roster: {'agents': {name: {...}}, ...}. strict=False returns None on any problem (hooks)."""
    p = config_path()
    try:
        with open(p, encoding="utf-8") as fh:
            raw = json.load(fh)
        if not isinstance(raw, dict):
            raise ValueError("the roster is a JSON object with an \"agents\" list")
        if not isinstance(raw.get("agents", []), list):
            raise ValueError("\"agents\" is a list of objects, one per agent")
        for k in ("herdr", "launch", "shell", "handoff_command"):
            if k in raw and not isinstance(raw[k], str):
                raise ValueError(f"\"{k}\" is a string")
        agents = {}
        for a in raw.get("agents", []):
            if not isinstance(a, dict):
                raise ValueError(f"each agent is an object with a name and a repo, not {a!r}")
            name, repo = a.get("name"), a.get("repo")
            if not isinstance(name, str) or not isinstance(repo, str) or not repo \
                    or not re.fullmatch(NAME_RE, name):
                raise ValueError(f"an agent needs a name (letters, digits, . _ -) and a repo: {a!r}")
            bad = [k for k in STR_FIELDS if k in a and not isinstance(a[k], str)]
            if bad:
                raise ValueError(f"agent {name}: {', '.join(bad)} must be text")
            if name in agents:
                raise ValueError(f"agent {name} is listed twice")
            agents[name] = dict(a, repo=_real(repo),
                                brief=_real(a.get("brief") or os.path.join(repo, "HANDOFF.md")),
                                model=a.get("model", "opus"), effort=a.get("effort", "high"),
                                handoff=a.get("handoff", True) is not False)
        raw["agents"] = agents
        return raw
    except FileNotFoundError:
        if strict:
            sys.exit(f"organism fleet: no roster at {p}\n  docs/FLEET.md shows the format "
                     "(ORGANISM_FLEET_CONFIG points elsewhere)")
    except (OSError, ValueError, TypeError, AttributeError) as ex:
        if strict:
            sys.exit(f"organism fleet: the roster {p} can't be read: {ex}")
    return None


def now():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def append(event):
    """One JSON line, one write with O_APPEND: concurrent hooks never interleave a line."""
    p = ledger_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    line = (json.dumps(dict({"ts": now()}, **event), ensure_ascii=False) + "\n").encode("utf-8")
    delay = os.environ.get("ORGANISM_FLEET_TEST_APPEND_DELAY")
    if delay:
        time.sleep(float(delay))      # tests widen the gap between the duplicate check and the write
    fd = os.open(p, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(fd, line)
    finally:
        os.close(fd)


def events():
    out = []
    try:
        with open(ledger_path(), encoding="utf-8") as fh:
            for ln in fh:
                try:
                    e = json.loads(ln)
                except ValueError:
                    continue          # a torn last line never hides the rest
                if isinstance(e, dict) and e.get("event"):
                    out.append(e)
    except OSError:
        pass
    return out


def brief_facts(path):
    """{'path', 'mtime', 'sha256'} of a brief, or None when it doesn't exist."""
    try:
        with open(path, "rb") as fh:
            digest = hashlib.sha256(fh.read()).hexdigest()[:12]
        mt = datetime.datetime.fromtimestamp(os.path.getmtime(path)).astimezone()
        return {"path": path, "mtime": mt.isoformat(timespec="seconds"), "sha256": digest}
    except OSError:
        return None


def first_line(text):
    """First non-empty line, collapsed and clipped, secrets redacted."""
    if not isinstance(text, str):
        text = json.dumps(text, ensure_ascii=False)
    line = next((l.strip() for l in text.splitlines() if l.strip()), "")
    line = re.sub(r"\s+", " ", line)
    try:
        sys.path.insert(0, os.path.join(HERE, "lib"))
        import mirror
        line = mirror.redact(line)[0]
    except Exception:
        line = "[not recorded: redaction unavailable]"
    return line if len(line) <= LINE_MAX else line[:LINE_MAX - 1] + "…"


# ---- sessions ----------------------------------------------------------------------------
def registry():
    """Live Claude Code sessions from ~/.claude/sessions/<pid>.json."""
    out = []
    for f in glob.glob(os.path.join(HOME, ".claude", "sessions", "*.json")):
        try:
            with open(f, encoding="utf-8") as fh:
                d = json.load(fh)
            os.kill(int(d["pid"]), 0)
        except (ValueError, KeyError, TypeError, OSError):
            continue
        out.append(d)
    return out


def within(path, repo):
    path = _real(path)
    return path == repo or path.startswith(repo.rstrip("/") + "/")


def agent_for(cfg, cwd):
    """The agent whose repo holds cwd (the deepest repo wins)."""
    if not cwd:
        return None
    hits = [(len(a["repo"]), n) for n, a in cfg["agents"].items() if within(cwd, a["repo"])]
    return max(hits)[1] if hits else None


def live_session(cfg, name, reg=None):
    """The newest live interactive session in the agent's repo, or None."""
    repo = cfg["agents"][name]["repo"]
    best = None
    for d in registry() if reg is None else reg:
        if d.get("kind", "interactive") != "interactive" or not within(d.get("cwd", ""), repo):
            continue
        if agent_for(cfg, d.get("cwd")) != name:
            continue
        if best is None or d.get("startedAt", 0) > best.get("startedAt", 0):
            best = d
    return best


def resolve_recipient(cfg, to):
    """(agent or None, label, known) for a SendMessage `to`. Known: an address (uds:, bridge:...),
    a live session's name or an agent in the roster. Anything else ("main", a subagent's name
    or id) is a teammate inside one session, not fleet traffic."""
    to = str(to or "")
    sock = to[4:] if to.startswith("uds:") else None
    for d in registry():
        if (sock and d.get("messagingSocketPath") == sock) or (not sock and d.get("name") == to):
            return agent_for(cfg, d.get("cwd")), d.get("name") or to, True
    if to in cfg["agents"]:
        return to, to, True
    return None, to, bool(re.match(r"[a-z][a-z0-9+.-]*:", to))


def own_session():
    """This process's Claude session from the registry (hooks run as its children)."""
    pid = os.environ.get("CLAUDE_PID") or str(os.getppid())
    try:
        with open(os.path.join(HOME, ".claude", "sessions", f"{pid}.json"), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


# ---- lifecycle ---------------------------------------------------------------------------
def history(name, evs=None):
    return [e for e in (events() if evs is None else evs) if e.get("agent") == name and e["event"] in LIFE]


def generations(name, evs=None):
    return sum(1 for e in history(name, evs) if e["event"] in ("summon", "resummon"))


def lifecycle(cfg, name, reg=None, evs=None):
    """(state, live session dict or None, last lifecycle event or None)."""
    s = live_session(cfg, name, reg)
    h = history(name, evs)
    last = h[-1] if h else None
    if s:
        return "live", s, last
    if not last:
        return "never summoned", None, None
    return {"harvest": "harvested", "close": "closed"}.get(last["event"], "ended, no record"), None, last


def open_start(h, sid):
    """The start event of this session's current run, or None when it has none or its run has ended."""
    for e in reversed(h):
        if e.get("session") != sid:
            continue
        if e["event"] in ("harvest", "close"):
            return None
        if e["event"] in ("summon", "resummon", "resume"):
            return e
    return None


def on_session_start(cfg, p):
    if (os.environ.get("CLAUDE_CODE_ENTRYPOINT") or "cli") != "cli":
        return                      # headless runs (claude -p, the SDK) are jobs, not fleet sessions
    name = agent_for(cfg, os.environ.get("CLAUDE_PROJECT_DIR") or p.get("cwd"))
    sid, source = p.get("session_id"), p.get("source", "startup")
    if not name or not sid or source not in ("startup", "resume", "clear"):
        return                      # compact keeps the session; /clear starts a new one with a new id
    h = history(name)
    if open_start(h, sid) is not None:
        return                      # a second wiring of the same hook (resume keeps the id, so only an open run counts)
    a = cfg["agents"][name]
    ev = {"agent": name, "session": sid, "repo": a["repo"], "brief": brief_facts(a["brief"])}
    me = own_session()
    if me.get("name"):
        ev["name"] = me["name"]
    if source == "resume":
        ev["event"] = "resume"
    else:
        prev = [e for e in h if e["event"] in ("summon", "resummon")]
        ev["event"] = "resummon" if prev else "summon"
        ev["generation"] = len(prev) + 1
        if prev:
            ev["continues"] = prev[-1].get("session")
        if source == "clear":
            ev["source"] = "clear"
    append(ev)


def on_session_end(cfg, p):
    name = agent_for(cfg, os.environ.get("CLAUDE_PROJECT_DIR") or p.get("cwd"))
    sid = p.get("session_id")
    if not name or not sid:
        return
    h = history(name)
    start = open_start(h, sid)
    if start is None:
        return                      # not a fleet session (headless), or its end is already recorded
    a = cfg["agents"][name]
    after = brief_facts(a["brief"])
    before = start.get("brief")
    rewritten = bool(after) and (not before or after["sha256"] != before.get("sha256")
                                 or after["mtime"] != before.get("mtime"))
    ev = {"agent": name, "session": sid, "reason": p.get("reason", "")}
    if rewritten:
        ev.update(event="harvest", brief=after)
    else:
        ev.update(event="close", why="handoff is off in the roster" if not a["handoff"]
                  else "the brief was not rewritten in this session")
    append(ev)


def on_send(cfg, p):
    if p.get("tool_name") != "SendMessage":
        return
    if (os.environ.get("CLAUDE_CODE_ENTRYPOINT") or "cli") != "cli":
        return                      # a headless run's messages are a job's, not a fleet session's
    ti = p.get("tool_input") or {}
    sender = agent_for(cfg, os.environ.get("CLAUDE_PROJECT_DIR") or p.get("cwd"))
    to_agent, to_label, known = resolve_recipient(cfg, ti.get("to"))
    if not known:
        return                      # "main" or a subagent: a message inside one session
    if to_agent and to_agent == sender:
        to_agent = None             # a session writing to its own repo's other session: not fleet traffic
    if to_agent and sender:
        kind = "message"
    elif to_agent:
        kind = "order"
    elif sender:
        kind = "report"
    else:
        return
    me = own_session()
    ev = {"event": kind, "session": p.get("session_id"), "agent": to_agent if kind == "order" else sender,
          "from": sender or me.get("name") or "outside the fleet", "to": to_agent or to_label,
          "line": first_line(ti.get("message", ""))}
    use = p.get("tool_use_id")
    if use:
        ev["tool_use"] = use
    if seen(ev):
        return                      # a second wiring of the same hook
    append(ev)


def seen(ev):
    """True when the ledger already holds this tool call: the same tool_use id, or, without
    one, the same session, kind, recipient and line in the last 10 seconds."""
    cutoff = datetime.datetime.now().astimezone() - datetime.timedelta(seconds=10)
    for e in reversed(events()[-200:]):
        if ev.get("tool_use"):
            if e.get("tool_use") == ev["tool_use"]:
                return True
            continue
        try:
            if datetime.datetime.fromisoformat(e.get("ts", "")) < cutoff:
                break
        except ValueError:
            continue
        if all(e.get(k) == ev.get(k) for k in ("event", "session", "to", "line")):
            return True
    return False


def cmd_hook():
    """Entry for the hooks: never fails the session, never prints to stdout.

    Two wirings of the hook (the plugin and an old settings.json line) can run at the same
    moment, so the duplicate check and the write happen under one lock on the ledger."""
    try:
        if not os.path.exists(config_path()):
            return 0
        cfg = load_config(strict=False)
        if not cfg:
            return 0
        p = json.load(sys.stdin)
        if not isinstance(p, dict):
            return 0
        fn = {"SessionStart": on_session_start, "SessionEnd": on_session_end,
              "PostToolUse": on_send}.get(p.get("hook_event_name"))
        if not fn:
            return 0
        lock = ledger_path() + ".lock"
        os.makedirs(os.path.dirname(lock), exist_ok=True)
        fd = os.open(lock, os.O_WRONLY | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            fn(cfg, p)
        finally:
            os.close(fd)
    except BaseException as ex:   # the ledger is a record, not a gate
        print(f"organism fleet hook: {type(ex).__name__}: {ex}", file=sys.stderr)
    return 0


# ---- herdr-core --------------------------------------------------------------------------
class Herdr:
    def __init__(self, exe):
        self.exe = exe
        self.env = dict(os.environ, PATH=os.environ.get("PATH", "") + f":/opt/homebrew/bin:{HOME}/.local/bin")

    def sh(self, *args, check=False):
        r = subprocess.run([self.exe, *args], capture_output=True, text=True, env=self.env)
        if check and r.returncode:
            sys.exit(f"failed: {self.exe} {' '.join(args)}\n{r.stderr.strip()}")
        return r.stdout

    def terminals(self, all_=False):
        try:
            d = json.loads(self.sh("ls", "--json", *(["--all"] if all_ else [])) or "[]")
        except ValueError:
            return {}
        rows = d.get("terminals", d) if isinstance(d, dict) else d
        return {t.get("name"): t for t in rows if isinstance(t, dict) and t.get("name")}

    def state(self, t):
        try:
            s = json.loads(self.sh("get", t, "--json")).get("status")
        except (ValueError, AttributeError):
            return None
        return s.get("state") if isinstance(s, dict) else s

    def screen(self, t):
        return self.sh("read", t, "--lines", "0").replace("\xa0", " ")

    def box(self, t):
        """Text in the prompt box: the last screen line that starts with the cursor (menu items are indented)."""
        lines = [l for l in self.screen(t).splitlines() if l.startswith("❯")]
        return lines[-1][1:].strip() if lines else None

    def pids(self, t):
        """(the terminal's own pid, the pid of the program in its foreground), each int or None."""
        try:
            d = json.loads(self.sh("get", t, "--json"))
        except ValueError:
            return None, None
        if not isinstance(d, dict):
            return None, None
        fg = d.get("foreground") if isinstance(d.get("foreground"), dict) else {}

        def num(v):
            try:
                return int(v)
            except (TypeError, ValueError):
                return None
        return num(d.get("pid")), num(fg.get("pid"))

    def keys(self, t, *k):
        self.sh("keys", t, *k)
        time.sleep(0.8 * TICK)

    def type_command(self, t, word):
        """Type /word into an empty box. False when the box held typed text (the slash is taken
        back) or the word didn't arrive intact (the box is emptied, so no later Enter runs it)."""
        self.keys(t, "/")
        if self.box(t) != "/":
            self.keys(t, "backspace")
            return False
        self.keys(t, *list(word))
        got = self.box(t)
        if got == "/" + word:
            return True
        if got:
            self.keys(t, *(["backspace"] * len(got)))
        return False


def herdr(cfg):
    exe = cfg.get("herdr", "hc")
    path = shutil.which(exe) or shutil.which(exe, path=f"/opt/homebrew/bin:{HOME}/.local/bin")
    return Herdr(path) if path else None


def launch_line(cfg, a, effort):
    """The launch template with each field shell-quoted (a repo path may hold spaces)."""
    tpl = a.get("launch") or cfg.get("launch") or DEFAULT_LAUNCH
    vals = {"model": a["model"], "effort": effort or a["effort"], "name": a["name"], "repo": a["repo"]}
    try:
        return tpl.format(**{k: shlex.quote(str(v)) for k, v in vals.items()})
    except (KeyError, IndexError, ValueError, AttributeError) as ex:
        sys.exit(f"organism fleet: the launch template {tpl!r} can't be filled ({type(ex).__name__}: {ex}).\n"
                 "  It may use {model} {effort} {name} {repo}; write {{ and }} for a literal brace.")


def ancestors(pid, limit=12):
    """pid's parent, grandparent, ... (ps), stopping at init."""
    out = []
    for _ in range(limit):
        try:
            r = subprocess.run(["ps", "-o", "ppid=", "-p", str(pid)], capture_output=True, text=True, timeout=5)
            pid = int(r.stdout.strip())
        except (OSError, ValueError, subprocess.SubprocessError):
            break
        if pid <= 1:
            break
        out.append(pid)
    return out


def terminal_session(cfg, name, hd):
    """The live session that runs in the agent's terminal: the registry entry whose pid is the
    terminal's foreground program, or whose ancestors include the terminal's own pid."""
    repo = cfg["agents"][name]["repo"]
    tpid, fpid = hd.pids(name)
    reg = [d for d in registry() if d.get("kind", "interactive") == "interactive"]
    hit = next((d for d in reg if fpid and int(d["pid"]) == fpid), None)
    if hit is None and tpid:
        hit = next((d for d in reg if tpid in ancestors(int(d["pid"]))), None)
    if hit is not None and not within(hit.get("cwd", ""), repo):
        return None
    return hit


# ---- commands ----------------------------------------------------------------------------
def short(ts):
    return (ts or "")[5:16].replace("T", " ")


def what(a):
    if a.get("role"):
        return a["role"]
    try:
        with open(os.path.join(a["repo"], "CLAUDE.md"), encoding="utf-8") as fh:
            for l in fh:
                l = l.strip()
                if l.startswith("# ") and re.search(r"\s[—-]\s", l):
                    return re.split(r"\s[—-]\s", l[2:], maxsplit=1)[-1][:80]
                if l and not l.startswith(("#", "---", "<!--", ">", "|")):
                    return l[:80]
    except OSError:
        pass
    return ""


def cmd_list(cfg):
    reg, evs = registry(), events()
    rows = []
    for n, a in cfg["agents"].items():
        st, s, last = lifecycle(cfg, n, reg, evs)
        if st == "live":
            st = f"live as {s.get('name') or s.get('sessionId', '?')[:8]}"
        b = brief_facts(a["brief"])
        rows.append((n, st, str(generations(n, evs)), short(b["mtime"]) if b else "none", what(a)))
    if not rows:
        print("organism fleet: the roster has no agents")
        return
    w = [max(len(r[i]) for r in rows + [("agent", "state", "gen", "brief", "")]) for i in range(4)]
    print(f"{'agent':{w[0]}}  {'state':{w[1]}}  {'gen':>{w[2]}}  {'brief':{w[3]}}  role")
    for r in rows:
        print(f"{r[0]:{w[0]}}  {r[1]:{w[1]}}  {r[2]:>{w[2]}}  {r[3]:{w[3]}}  {r[4]}")


def cmd_up(cfg, name, effort=None, dry=False):
    a = cfg["agents"].get(name) or sys.exit(f"organism fleet: no agent {name} in the roster (see fleet list)")
    a["name"] = name
    st, s, last = lifecycle(cfg, name)
    if s:
        print(f"already live: {s.get('name')}  session {s.get('sessionId')}  socket {s.get('messagingSocketPath')}")
        return 0
    verb = "resummon" if generations(name) else "summon"
    b = brief_facts(a["brief"])
    plan = (f"{verb} {name} in {a['repo']}: generation {generations(name) + 1}, from "
            + (f"the brief of {short(b['mtime'])}" if b else "no brief"))
    line = launch_line(cfg, a, effort)
    hd = herdr(cfg)
    if hd is None or dry:
        print(plan)
        print(("herdr-core (hc) is not installed; start it yourself:" if hd is None else "would run:")
              + f"\n  cd {shlex.quote(a['repo'])} && {line}")
        if hd is None:
            print("The ledger records the start when the session opens.")
        return 0
    print(plan)
    shell = cfg.get("shell") or os.environ.get("SHELL") or "/bin/sh"
    if name in hd.terminals(all_=True):
        hd.sh("rm", name)
    hd.sh("run", "--bg", "--name", name, "--cwd", a["repo"], "--label", "fleet=child", "--no-parent",
          "--no-notify", shell, "-lic", line, check=True)
    t0 = time.time()
    while time.time() - t0 < 180 * TICK:
        time.sleep(3 * TICK)
        scr = hd.screen(name)
        if re.search(r"trust (this|the files in this) folder", scr, re.I):
            opts = re.findall(r"^\s*(❯)?\s*(\d)\.\s*(.*)$", scr, re.M)
            cur = next((int(o[1]) for o in opts if o[0]), 1)
            yes = next((int(o[1]) for o in opts if re.match(r"yes", o[2], re.I)), None)
            if yes is None:
                sys.exit("a folder-trust prompt without a Yes option; answer it in the terminal:\n" + scr[-800:])
            for _ in range(abs(yes - cur)):
                hd.keys(name, "down" if yes > cur else "up")
            hd.keys(name, "enter")
            print(f"answered the folder-trust prompt with Yes ({a['repo']} is in the roster)")
            continue
        if re.search(r"I accept|Bypass Permissions mode", scr):
            sys.exit("a consent prompt is open in the terminal; it is yours to answer:\n" + scr[-800:])
        s = live_session(cfg, name)
        if s and hd.box(name) is not None:
            ev = next((e for e in reversed(history(name)) if e.get("session") == s.get("sessionId")), None)
            led = (f"ledger: {ev['event']}, generation {ev.get('generation', '?')}" if ev
                   else "ledger: no event yet (are the Organism hooks loaded?)")
            print(f"up: {s.get('name')}  session {s.get('sessionId')}  socket {s.get('messagingSocketPath')}  {led}")
            return 0
    sys.exit(f"{name} not ready after 3 minutes; screen tail:\n" + hd.screen(name)[-800:])


def cmd_down(cfg, name, handoff=True, dry=False):
    a = cfg["agents"].get(name) or sys.exit(f"organism fleet: no agent {name} in the roster")
    hd = herdr(cfg)
    if hd is None:
        sys.exit("organism fleet down needs herdr-core (hc) to type into the session. "
                 "Harvest it by hand: run /harvest in the session, then /exit.")
    if not live_session(cfg, name) or name not in hd.terminals():
        sys.exit(f"{name} is not live in a fleet terminal")
    s = terminal_session(cfg, name, hd)
    if s is None:
        sys.exit(f"no Claude session of {name} runs in its terminal {name}; nothing was typed")
    if hd.state(name) == "working":
        sys.exit(f"{name} is working; wait for its report first")
    why = None
    if not a["handoff"]:
        why = "handoff is off in the roster (its decisions are recorded elsewhere)"
    elif not handoff:
        why = "--no-handoff"
    cmd = cfg.get("handoff_command", "harvest")
    plan = (f"{'close' if why else 'harvest'} {name}: session {s.get('sessionId')} (pid {s.get('pid')}): "
            + (f"no /{cmd} ({why}), " if why else f"/{cmd}, ") + "/exit, hc kill, hc rm")
    print(plan)
    if dry:
        return 0
    if not why:
        before = brief_facts(a["brief"])
        if not hd.type_command(name, cmd):
            sys.exit(f"the prompt box of {name} held typed text, or /{cmd} didn't type cleanly; "
                     "nothing was sent")
        hd.keys(name, "enter")
        t0 = time.time()
        while time.time() - t0 < 1200 * TICK:
            time.sleep(10 * TICK)
            st = hd.state(name)
            if st == "blocked":         # a dialog: a permission request or a question. Never answered here.
                sys.exit(f"a dialog is open in {name} during /{cmd}; answer it in the terminal. "
                         "The session stays open.\n" + hd.screen(name)[-800:])
            if st in ("idle", "done") and time.time() - t0 > 20 * TICK:
                break
        after = brief_facts(a["brief"])
        if not after or (before and after["sha256"] == before["sha256"] and after["mtime"] == before["mtime"]):
            sys.exit(f"the brief of {name} was not rewritten; the session stays open")
        print(f"brief written {short(after['mtime'])}")
    if not hd.type_command(name, "exit"):
        sys.exit(f"the prompt box of {name} held typed text, or /exit didn't type cleanly; not exited")
    hd.keys(name, "enter")
    esc = False
    for _ in range(30):
        time.sleep(TICK)
        try:
            os.kill(int(s["pid"]), 0)
        except (OSError, KeyError, ValueError):
            break
        if not esc and re.search(r"unsent feedback draft", hd.screen(name), re.I):
            hd.keys(name, "esc")        # the one dialog answered here: Esc discards the draft and exits
            esc = True
            print("discarded an unsent feedback draft (Esc) so /exit could finish")
    else:
        sys.exit(f"claude pid {s['pid']} still alive 30 s after /exit")
    hd.sh("kill", name)
    hd.sh("rm", name)
    ev = next((e for e in reversed(history(name)) if e.get("session") == s.get("sessionId")
               and e["event"] in ("harvest", "close")), None)
    print(f"down: {s.get('name')} closed; " + (f"ledger: {ev['event']}" if ev else "ledger: no event (are the Organism hooks loaded?)"))
    return 0


def parse_since(v):
    m = re.fullmatch(r"(\d+)([dh])", v or "")
    if m:
        delta = datetime.timedelta(**{"days" if m.group(2) == "d" else "hours": int(m.group(1))})
        return datetime.datetime.now().astimezone() - delta
    try:
        t = datetime.datetime.fromisoformat(v)
    except (TypeError, ValueError):
        sys.exit(f"organism fleet: --since takes 3d, 12h, a date or a time, not {v!r}")
    return t if t.tzinfo else t.astimezone()


def describe(e):
    k = e["event"]
    if k in ("summon", "resummon", "resume"):
        b = e.get("brief")
        txt = f"session {str(e.get('session'))[:8]}"
        if k != "resume":
            txt += f", generation {e.get('generation', '?')}"
        if e.get("continues"):
            txt += f", continues {str(e['continues'])[:8]}"
        return txt + (f", brief of {short(b['mtime'])} ({b['sha256']})" if b else ", no brief")
    if k == "harvest":
        b = e.get("brief") or {}
        return f"session {str(e.get('session'))[:8]}, brief written {short(b.get('mtime'))} ({b.get('sha256')})"
    if k == "close":
        return f"session {str(e.get('session'))[:8]}, no harvest: {e.get('why', '')}"
    return f"{e.get('from')} → {e.get('to')}: {e.get('line', '')}"


def cmd_ledger(args):
    evs = events()
    if "--since" in args:
        t = parse_since(args[args.index("--since") + 1] if args.index("--since") + 1 < len(args) else None)
        keep = []
        for e in evs:
            try:
                if datetime.datetime.fromisoformat(e["ts"]) >= t:
                    keep.append(e)
            except (KeyError, ValueError, TypeError):
                continue
        evs = keep
    if "--agent" in args and args.index("--agent") + 1 < len(args):
        who = args[args.index("--agent") + 1]
        evs = [e for e in evs if who in (e.get("agent"), e.get("from"), e.get("to"))]
    if "--json" in args:
        for e in evs:
            print(json.dumps(e, ensure_ascii=False))
        return 0
    if "--md" in args:
        i = args.index("--md") + 1
        if i >= len(args):
            sys.exit("organism fleet: --md needs a path")
        path = os.path.expanduser(args[i])
        try:
            with open(path, encoding="utf-8") as fh:
                ours = fh.readline().strip() == MD_MARK
        except FileNotFoundError:
            ours = True
        if not ours and "--force" not in args:
            sys.exit(f"organism fleet: {path} exists and was not written by fleet ledger; --force replaces it")
        out = [MD_MARK, "", "# Fleet ledger", "", f"{len(evs)} events, written {now()[:16].replace('T', ' ')}.", "",
               "| When | Event | Agent | What |", "|---|---|---|---|"]
        for e in evs:
            cell = describe(e).replace("|", "\\|")
            out.append(f"| {e.get('ts', '')[:16].replace('T', ' ')} | {e['event']} | {e.get('agent') or ''} | {cell} |")
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write("\n".join(out) + "\n")
        os.replace(tmp, path)
        print(f"organism fleet: {len(evs)} events written to {path}")
        return 0
    if not evs:
        print("organism fleet: the ledger has no events" + (" in that range" if "--since" in args else ""))
    for e in evs:
        print(f"{e.get('ts', '')[:16].replace('T', ' ')}  {e['event']:8}  {(e.get('agent') or '-'):16}  {describe(e)}")
    return 0


def main(a):
    if not a or a[0] in ("-h", "--help", "help"):
        print(__doc__.split("\nRun:")[0])
        return 0
    if a[0] == "hook":
        return cmd_hook()
    if a[0] == "ledger":
        return cmd_ledger(a[1:])
    cfg = load_config()
    flag = lambda f: f in a
    opt = lambda f: a[a.index(f) + 1] if f in a and a.index(f) + 1 < len(a) else None
    if a[0] == "list":
        cmd_list(cfg)
        return 0
    if a[0] in ("up", "summon", "resummon") and len(a) > 1:
        return cmd_up(cfg, a[1], opt("--effort"), flag("--dry"))
    if a[0] in ("down", "harvest") and len(a) > 1:
        return cmd_down(cfg, a[1], not flag("--no-handoff"), flag("--dry"))
    print(__doc__.split("\nRun:")[0], file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
