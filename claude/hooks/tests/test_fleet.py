#!/usr/bin/env python3
"""tests for claude/hooks/fleet.py: the roster, the ledger the hooks write, and the CLI.
  - no roster: the hook does nothing, prints nothing, writes no ledger
  - SessionStart in an agent's repo: summon, then resummon with the session it continues,
    the brief it starts from and its generation; resume for `claude --resume`
  - headless runs, other folders, a second wiring and compact add nothing; /clear starts
    the next session (new id); parallel wirings of one hook still record one event
  - a roster of the wrong shape: the hook exits 0, silent; list says it can't be read
  - SessionEnd: harvest when the brief was rewritten, close (and why) when not, and
    "handoff is off" for an agent whose roster says so
  - SendMessage: order, report and agent-to-agent message, first line only, redacted, clipped;
    in-process recipients (main, a subagent) and headless senders add nothing; one tool
    call is recorded once however many times the hook runs
  - orchestrate roster: live, harvested, closed, never summoned, generations
  - orchestrate history: --since, --agent, --json, --md (never over a file it didn't write)
  - summon without herdr-core prints the command (shell-quoted; a bad template is named);
    down without it refuses; down --dry plans
  - down against a fake hc: it picks the session in the agent's terminal, stops on a
    dialog without answering it, clears a garbled command from the box, and presses Esc
    only on the unsent-feedback dialog
Every case uses a temp HOME. Run:  python3 claude/hooks/tests/test_fleet.py"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HOOKS = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
FLEET = os.path.join(HOOKS, "fleet.py")
ROOT = os.path.dirname(os.path.dirname(HOOKS))

_fails = []


def check(name, cond):
    print(("  ✓ " if cond else "  ✗ ") + name)
    if not cond:
        _fails.append(name)


def j(*parts):
    return "".join(parts)


TOKEN = j("sk-", "ant-api03-", "Qx7" * 10, "_k-9")
FAKE_PID = "4242424"       # the hook's own Claude session (only its file is read, never kill(0))


class Box:
    def __init__(self, herdr="no-such-herdr-core"):
        self.home = os.path.realpath(tempfile.mkdtemp(prefix="fleet-"))
        self.state = os.path.join(self.home, ".claude", "state")
        self.sessions = os.path.join(self.home, ".claude", "sessions")
        os.makedirs(self.state)
        os.makedirs(self.sessions)
        self.repos = {}
        for n in ("web", "api", "notes"):
            r = os.path.join(self.home, "dev", n)
            os.makedirs(os.path.join(r, "src"))
            self.repos[n] = r
        with open(os.path.join(self.repos["web"], "CLAUDE.md"), "w") as fh:
            fh.write("# web — the shop front\n")
        self.config = os.path.join(self.state, "organism-fleet.json")
        self.ledger = os.path.join(self.state, "fleet-ledger.jsonl")
        self.herdr = herdr
        self.write_config()

    def write_config(self, extra=None):
        cfg = {"herdr": self.herdr, "launch": "claude --model {model} --effort {effort} --name {name}",
               "agents": [{"name": "web", "repo": "~/dev/web"},
                          {"name": "api", "repo": "~/dev/api", "model": "sonnet", "effort": "xhigh"},
                          {"name": "notes", "repo": "~/dev/notes", "handoff": False, "role": "keeps notes"}]}
        cfg.update(extra or {})
        with open(self.config, "w") as fh:
            json.dump(cfg, fh)

    def env(self, **kw):
        e = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE", "ORGANISM_FLEET"))}
        e.update(HOME=self.home, CLAUDE_CODE_ENTRYPOINT="cli", CLAUDE_PID=FAKE_PID,
                 PATH="/usr/bin:/bin", ORGANISM_FLEET_TICK="0.01")
        e.update(kw)
        return {k: v for k, v in e.items() if v is not None}

    def run(self, *args, stdin=None, **kw):
        return subprocess.run([sys.executable, FLEET, *args], input=stdin, capture_output=True,
                              text=True, env=self.env(**kw), timeout=60)

    def hook(self, payload, **kw):
        return self.run("hook", stdin=json.dumps(payload), **kw)

    def start(self, agent, sid, source="startup", sub="", **kw):
        return self.hook({"hook_event_name": "SessionStart", "session_id": sid, "source": source,
                          "cwd": os.path.join(self.repos[agent], sub)}, **kw)

    def end(self, agent, sid, reason="prompt_input_exit"):
        return self.hook({"hook_event_name": "SessionEnd", "session_id": sid, "reason": reason,
                          "cwd": self.repos[agent]})

    def send(self, cwd, to, message, sid="s-send", tool="SendMessage", use_id=None, **kw):
        self.n_sends = getattr(self, "n_sends", 0) + 1
        return self.hook({"hook_event_name": "PostToolUse", "session_id": sid, "cwd": cwd,
                          "tool_name": tool, "tool_input": {"to": to, "message": message},
                          "tool_use_id": use_id or f"toolu_{self.n_sends:04d}",
                          "tool_response": {}}, **kw)

    def events(self):
        out = []
        try:
            with open(self.ledger) as fh:
                for l in fh:
                    try:
                        out.append(json.loads(l))
                    except ValueError:
                        pass                # the torn line a test writes on purpose
        except FileNotFoundError:
            pass
        return out

    def session_file(self, pid, **d):
        with open(os.path.join(self.sessions, f"{pid}.json"), "w") as fh:
            json.dump(dict({"pid": int(pid), "kind": "interactive"}, **d), fh)

    def brief(self, agent, text):
        p = os.path.join(self.repos[agent], "HANDOFF.md")
        with open(p, "w") as fh:
            fh.write(text)
        st = os.stat(p)
        os.utime(p, (st.st_atime, st.st_mtime + 2))     # a later mtime even on a coarse clock

    def done(self):
        shutil.rmtree(self.home, ignore_errors=True)


def test_no_roster():
    print("hook: no roster means no work")
    b = Box()
    os.remove(b.config)
    r = b.start("web", "s1")
    check("exits 0 and prints nothing", r.returncode == 0 and r.stdout == "" and r.stderr == "")
    check("no ledger is created", not os.path.exists(b.ledger))
    with open(b.config, "w") as fh:
        fh.write("{ not json")
    r = b.start("web", "s1")
    check("a broken roster: the hook still exits 0, silent, no ledger",
          r.returncode == 0 and r.stdout == "" and not os.path.exists(b.ledger))
    r = b.run("roster")
    check("…but orchestrate roster says the roster can't be read", r.returncode != 0 and "can't be read" in r.stderr)
    for label, text in (("agents as an object", '{"agents": {"web": {"repo": "~/dev/web"}}}'),
                        ("null", "null"), ("a list", "[]"),
                        ("a number as a name", '{"agents": [{"name": 5, "repo": "~/dev/web"}]}'),
                        ("a number as a model", '{"agents": [{"name": "web", "repo": "~/dev/web", "model": 4}]}'),
                        ("an agent that is a string", '{"agents": ["web"]}')):
        with open(b.config, "w") as fh:
            fh.write(text)
        hooks = [b.start("web", "s1"), b.end("web", "s1"), b.send(b.repos["web"], "uds:/x.sock", "hi")]
        check(f"roster with {label}: every hook exits 0, silent",
              all(r.returncode == 0 and r.stdout == "" and "Traceback" not in r.stderr for r in hooks))
        r = b.run("roster")
        check(f"roster with {label}: orchestrate roster says it can't be read, no traceback",
              r.returncode != 0 and "can't be read" in r.stderr and "Traceback" not in r.stderr)
    check("no ledger from a roster of the wrong shape", not os.path.exists(b.ledger))
    b.done()


def test_lifecycle():
    print("hook: summon, close, resummon, harvest, resume")
    b = Box()
    b.session_file(FAKE_PID, sessionId="s1", name="web-7")
    b.brief("web", "brief one\n")
    r = b.start("web", "s1", sub="src")
    ev = b.events()
    check("SessionStart in a subfolder of the repo: one summon", r.returncode == 0 and [e["event"] for e in ev] == ["summon"])
    e = ev[0]
    check("summon: agent, session, generation 1, no continues",
          e.get("agent") == "web" and e.get("session") == "s1" and e.get("generation") == 1 and "continues" not in e)
    check("summon: the brief it starts from (path, mtime, hash)",
          e.get("brief", {}).get("path") == os.path.join(b.repos["web"], "HANDOFF.md")
          and len(e["brief"].get("sha256", "")) == 12 and e["brief"].get("mtime"))
    check("summon: the session's name from the registry", e.get("name") == "web-7")
    check("summon: a timestamp with its offset", len(e.get("ts", "")) >= 25 and e["ts"][19] in "+-")
    check("the ledger is private (0600)", oct(os.stat(b.ledger).st_mode & 0o777) == "0o600")

    b.start("web", "s1")
    check("a second wiring of the same SessionStart adds nothing", len(b.events()) == 1)
    b.start("web", "s1", source="compact")
    check("compact adds nothing", len(b.events()) == 1)
    b.start("web", "s1", source="clear")
    check("clear with the same session id adds nothing", len(b.events()) == 1)
    b.start("web", "s-headless", CLAUDE_CODE_ENTRYPOINT="sdk-cli")
    check("a headless run (claude -p) is not a fleet session", len(b.events()) == 1)
    b.hook({"hook_event_name": "SessionStart", "session_id": "s-out", "source": "startup",
            "cwd": os.path.join(b.home, "dev")})
    check("a folder outside every agent's repo adds nothing", len(b.events()) == 1)

    b.end("web", "s1")
    e = b.events()[-1]
    check("SessionEnd with the brief untouched: close, and why",
          e["event"] == "close" and e.get("why") == "the brief was not rewritten in this session"
          and e.get("reason") == "prompt_input_exit")
    b.end("web", "s1")
    check("a second SessionEnd adds nothing", len(b.events()) == 2)
    b.end("web", "s-headless")
    check("SessionEnd of a session that never started here adds nothing", len(b.events()) == 2)

    b.start("web", "s2")
    e = b.events()[-1]
    check("the next session: resummon, generation 2, continues s1",
          e["event"] == "resummon" and e.get("generation") == 2 and e.get("continues") == "s1")
    b.brief("web", "brief two\n")
    b.end("web", "s2")
    e = b.events()[-1]
    check("SessionEnd after the brief was rewritten: harvest with the new brief",
          e["event"] == "harvest" and e.get("brief", {}).get("sha256") != b.events()[-2]["brief"]["sha256"])

    b.start("web", "s2", source="resume")
    e = b.events()[-1]
    check("claude --resume: a resume event, no new generation", e["event"] == "resume" and "generation" not in e)
    b.end("web", "s2")
    check("…and its end is recorded once more", b.events()[-1]["event"] == "close")

    b.start("web", "s2", source="resume")
    b.brief("web", "brief three\n")
    b.end("web", "s2", reason="clear")
    check("/clear ends the session: harvest when the brief was rewritten", b.events()[-1]["event"] == "harvest")
    b.start("web", "s3", source="clear")
    e = b.events()[-1]
    check("/clear starts the next session: resummon, generation 3, continues s2, source clear",
          e["event"] == "resummon" and e.get("generation") == 3 and e.get("continues") == "s2"
          and e.get("source") == "clear")
    b.end("web", "s3")
    check("…and its end is recorded", b.events()[-1]["event"] == "close" and b.events()[-1]["session"] == "s3")
    b.start("api", "a1")
    check("a repo whose brief doesn't exist yet: summon with brief null",
          b.events()[-1]["event"] == "summon" and b.events()[-1].get("brief") is None)
    b.start("notes", "n1")
    b.brief("notes", "rewritten by hand\n")
    b.end("notes", "n1")
    e = b.events()[-1]
    check("handoff off in the roster: harvest only if the brief changed anyway", e["event"] == "harvest")
    b.start("notes", "n2")
    b.end("notes", "n2")
    e = b.events()[-1]
    check("handoff off in the roster: the close says so",
          e["event"] == "close" and e.get("why") == "handoff is off in the roster")

    # two wirings of the hook (the plugin and an old settings.json line) run in parallel;
    # the delay widens the gap between reading the ledger and writing it
    def parallel(payload, n=4):
        env = b.env(ORGANISM_FLEET_TEST_APPEND_DELAY="0.3")
        procs = [subprocess.Popen([sys.executable, FLEET, "hook"], stdin=subprocess.PIPE, env=env,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True)
                 for _ in range(n)]
        for pr in procs:
            pr.stdin.write(json.dumps(payload))
            pr.stdin.close()
        for pr in procs:
            pr.wait(timeout=60)
    n = len(b.events())
    parallel({"hook_event_name": "SessionStart", "session_id": "p1", "source": "startup", "cwd": b.repos["api"]})
    check("four parallel SessionStart hooks record one start", len(b.events()) == n + 1)
    parallel({"hook_event_name": "SessionEnd", "session_id": "p1", "reason": "other", "cwd": b.repos["api"]})
    check("four parallel SessionEnd hooks record one end", len(b.events()) == n + 2)
    parallel({"hook_event_name": "PostToolUse", "session_id": "p1", "cwd": b.repos["api"], "tool_name": "SendMessage",
              "tool_use_id": "toolu_par", "tool_input": {"to": "uds:/x.sock", "message": "done"}})
    check("four parallel PostToolUse hooks record one report", len(b.events()) == n + 3)
    b.done()


def test_messages():
    print("hook: orders, reports and messages on SendMessage")
    b = Box()
    live = str(os.getpid())                       # a live process stands in for a child session
    b.session_file(live, sessionId="w1", name="web-3", cwd=b.repos["web"],
                   messagingSocketPath="/tmp/socks/9.sock")
    b.session_file(FAKE_PID, sessionId="m1", name="master-1", cwd=b.home)
    outside = b.home
    b.send(outside, "uds:/tmp/socks/9.sock", f"\n\n[master] Order: ship it {TOKEN}\nsecond line")
    e = b.events()[-1]
    check("to an agent's socket: an order to that agent", e["event"] == "order" and e.get("agent") == "web"
          and e.get("to") == "web")
    check("…from the sender's session name", e.get("from") == "master-1")
    check("…first line only, secrets redacted", e.get("line", "").startswith("[master] Order: ship it")
          and "second line" not in e["line"] and "sk-ant" not in e["line"] and "[redacted" in e["line"])
    b.send(outside, "web-3", "by name")
    check("to an agent's session name: an order", b.events()[-1]["event"] == "order")
    b.send(outside, "api", "x" * 500)
    e = b.events()[-1]
    check("to an agent's roster name: an order, clipped to 200", e["event"] == "order" and e.get("agent") == "api"
          and len(e["line"]) == 200 and e["line"].endswith("…"))
    b.send(b.repos["api"], "uds:/tmp/socks/elsewhere.sock", "Done: 3 commits")
    e = b.events()[-1]
    check("from an agent's repo to someone outside: a report", e["event"] == "report" and e.get("agent") == "api"
          and e.get("from") == "api" and e.get("to") == "uds:/tmp/socks/elsewhere.sock")
    b.send(b.repos["api"], "uds:/tmp/socks/9.sock", "a question for web")
    e = b.events()[-1]
    check("from one agent to another: a message", e["event"] == "message" and e.get("from") == "api" and e.get("to") == "web")
    n = len(b.events())
    b.send(outside, "someone-else", "hello")
    check("outside to outside: nothing", len(b.events()) == n)
    b.send(b.repos["web"], "uds:/tmp/socks/9.sock", "to my own repo's session")
    check("an agent writing to its own repo counts as a report", b.events()[-1]["event"] == "report")
    n = len(b.events())
    b.send(outside, "web", "not a send", tool="Bash")
    check("another tool: nothing", len(b.events()) == n)
    b.send(b.repos["api"], "main", "to the lead of my own session")
    b.send(b.repos["api"], "a3f9c2e1", "to a subagent")
    b.send(b.repos["api"], "researcher", "to a named subagent")
    check("an agent writing to an in-process recipient (main, a subagent): nothing", len(b.events()) == n)
    b.send(b.repos["api"], "uds:/tmp/socks/elsewhere.sock", "a headless report", CLAUDE_CODE_ENTRYPOINT="sdk-cli")
    check("a headless run (claude -p) sending a message: nothing", len(b.events()) == n)
    b.send(b.repos["api"], "uds:/tmp/socks/elsewhere.sock", "once", use_id="toolu_same")
    b.send(b.repos["api"], "uds:/tmp/socks/elsewhere.sock", "once", use_id="toolu_same")
    check("the same tool call twice (two wirings): one event", len(b.events()) == n + 1)
    b.send(b.repos["api"], "uds:/tmp/socks/elsewhere.sock", "once", use_id="toolu_other")
    check("…but a second call with the same text is its own event", len(b.events()) == n + 2)
    b.done()


def test_list_and_ledger():
    print("cli: orchestrate roster and orchestrate history")
    b = Box()
    b.session_file(FAKE_PID, sessionId="x", name="me")
    b.brief("web", "w\n")
    b.start("web", "w1")
    b.brief("web", "w2\n")
    b.end("web", "w1")
    b.start("web", "w2")
    b.brief("web", "w3\n")
    b.end("web", "w2")
    b.start("notes", "n1")
    b.end("notes", "n1")
    live = str(os.getpid())
    b.session_file(live, sessionId="api-live-session", name="api-9", cwd=b.repos["api"])
    with open(b.ledger, "a") as fh:
        fh.write('{"ts": "2026-01-01T00:00:00+00:00", "event": "order", "agent": "api", "from": "x", "to": "api", "line": "old"}\n')
        fh.write('{"torn line\n')
    r = b.run("roster")
    out = r.stdout
    rows = {l.split()[0]: l for l in out.splitlines()[1:] if l.strip()}
    check("roster runs", r.returncode == 0 and set(rows) == {"web", "api", "notes"})
    check("list, the old name, prints the same", b.run("list").stdout == out)
    check("web: harvested, 2 generations, its CLAUDE.md role",
          "harvested" in rows.get("web", "") and " 2 " in rows["web"] and "the shop front" in rows["web"])
    check("api: live as its session name, 0 generations recorded", "live as api-9" in rows.get("api", ""))
    check("notes: closed, its roster role", "closed" in rows.get("notes", "") and "keeps notes" in rows["notes"])
    os.remove(os.path.join(b.sessions, f"{live}.json"))
    b.write_config({"agents": [{"name": "web", "repo": "~/dev/web"}, {"name": "spare", "repo": "~/dev/spare"}]})
    check("an agent with no events: never summoned", "never summoned" in b.run("roster").stdout)
    b.write_config()

    r = b.run("history")
    check("history prints every event and skips the torn line",
          r.returncode == 0 and len(r.stdout.splitlines()) == len(b.events()) and "old" in r.stdout)
    r = b.run("history", "--since", "1h")
    check("--since 1h drops the old event", "old" not in r.stdout and "harvest" in r.stdout)
    r = b.run("history", "--since", "2025-12-31")
    check("--since a date keeps it", "old" in r.stdout)
    r = b.run("history", "--since", "soon")
    check("--since nonsense is refused", r.returncode != 0 and "--since" in r.stderr)
    r = b.run("history", "--agent", "notes", "--json")
    lines = [json.loads(l) for l in r.stdout.splitlines()]
    check("--agent notes --json: only notes events, as JSON",
          lines and all(e.get("agent") == "notes" for e in lines) and [e["event"] for e in lines] == ["summon", "close"])
    md = os.path.join(b.home, "History.md")
    r = b.run("history", "--md", md)
    with open(md) as fh:
        text = fh.read()
    check("--md writes a table with the mark", r.returncode == 0 and text.startswith("<!-- written by organism orchestrate")
          and "| When | Event | Agent | What |" in text and "continues w1" in text)
    r = b.run("history", "--md", md)
    check("…and rewrites its own file", r.returncode == 0)
    check("the table's heading is History", "# History" in text)
    for old in ("<!-- written by organism orchestrate ledger; rewritten on each run -->",
                "<!-- written by organism fleet ledger; rewritten on each run -->"):
        with open(md, "w") as fh:
            fh.write(old + "\n# Ledger\n")
        r = b.run("history", "--md", md)
        with open(md) as fh:
            new = fh.read()
        check("a table with an older first line is replaced without --force",
              r.returncode == 0 and new.startswith("<!-- written by organism orchestrate history"))
    r = b.run("ledger")
    check("the old name ledger still works",
          r.returncode == 0 and len(r.stdout.splitlines()) == len(b.events()))
    other = os.path.join(b.home, "Mine.md")
    with open(other, "w") as fh:
        fh.write("my notes\n")
    r = b.run("history", "--md", other)
    with open(other) as fh:
        kept = fh.read()
    check("--md never replaces a file it didn't write", r.returncode != 0 and kept == "my notes\n")
    r = b.run("history", "--md", other, "--force")
    check("…unless --force", r.returncode == 0)
    b.done()


def fake_hc(home, terminals, state="idle", fg=None):
    """A shell hc; get names fg (default: this test process, the pid of its fake sessions)."""
    fg = os.getpid() if fg is None else fg
    p = os.path.join(home, "fake-hc")
    with open(p, "w") as fh:
        fh.write("#!/bin/sh\n"
                 f'echo "$@" >> "{home}/hc.log"\n'
                 'case "$1" in\n'
                 f"  ls) echo '{json.dumps({'terminals': [{'name': t} for t in terminals]})}' ;;\n"
                 f"  get) echo '{json.dumps({'pid': 1, 'foreground': {'pid': fg}, 'status': {'state': state}})}' ;;\n"
                 "  read) echo '❯ ' ;;\n"
                 "esac\n")
    os.chmod(p, 0o755)
    return p


def test_up_down():
    print("cli: summon and unsummon")
    b = Box()
    r = b.run("summon", "api")
    check("summon without herdr-core prints the command for the agent's repo",
          r.returncode == 0 and f"cd {b.repos['api']} && claude --model sonnet --effort xhigh --name api" in r.stdout)
    check("…and calls it a summon, generation 1", "summon api" in r.stdout and "generation 1" in r.stdout
          and "resummon" not in r.stdout)
    r = b.run("summon", "api", "--effort", "low")
    check("--effort overrides the roster", "--effort low" in r.stdout)
    b.session_file(FAKE_PID, sessionId="x", name="me")
    b.brief("api", "b\n")
    b.start("api", "a1")
    b.brief("api", "c\n")
    b.end("api", "a1")
    r = b.run("up", "api")
    check("up, the old name, on a harvested agent: a resummon, generation 2, from its brief",
          "resummon api" in r.stdout and "generation 2" in r.stdout and "from the brief of" in r.stdout)
    r = b.run("summon", "nobody")
    check("summon on an unknown agent is refused", r.returncode != 0 and "no agent nobody" in r.stderr)
    r = b.run("unsummon", "api")
    check("unsummon without herdr-core refuses and says how to harvest by hand",
          r.returncode != 0 and "/harvest" in r.stderr and "/exit" in r.stderr)

    hc = fake_hc(b.home, ["notes", "web"])
    b.herdr = hc
    b.write_config()
    live = str(os.getpid())
    b.session_file(live, sessionId="n-live", name="notes-2", cwd=b.repos["notes"])
    r = b.run("unsummon", "notes", "--dry")
    check("unsummon --dry on a handoff-off agent: plans a close without /harvest",
          r.returncode == 0 and "close notes" in r.stdout and "no /harvest" in r.stdout and "handoff is off" in r.stdout)
    with open(os.path.join(b.home, "hc.log")) as fh:
        log = fh.read()
    check("…and types nothing", "keys" not in log and "kill" not in log)
    b.session_file(live, sessionId="w-live", name="web-2", cwd=b.repos["web"])
    r = b.run("harvest", "web", "--dry")
    check("harvest --dry on a normal agent: plans /harvest, /exit, kill, rm",
          r.returncode == 0 and "harvest web" in r.stdout and "/harvest, /exit" in r.stdout)
    r = b.run("unsummon", "web", "--no-handoff", "--dry")
    check("--no-handoff: plans a close", "close web" in r.stdout and "--no-handoff" in r.stdout)
    r = b.run("summon", "web", "--dry")
    check("summon on a live agent says where it lives and starts nothing",
          r.returncode == 0 and "already live: web-2" in r.stdout)
    os.remove(os.path.join(b.sessions, f"{live}.json"))
    r = b.run("summon", "web", "--dry")
    check("summon --dry with herdr-core prints what it would run", r.returncode == 0 and "would run:" in r.stdout)
    r = b.run("unsummon", "web")
    check("unsummon on an agent that isn't live is refused", r.returncode != 0 and "not live" in r.stderr)

    hc = fake_hc(b.home, ["web"], state="working")
    b.session_file(live, sessionId="w-live", name="web-2", cwd=b.repos["web"])
    r = b.run("unsummon", "web")
    check("unsummon on a working agent is refused", r.returncode != 0 and "working" in r.stderr)
    b.done()


FAKE_HC2 = r'''#!/usr/bin/env python3
# a stateful hc: one prompt box, a scripted state, a log of every call
import json, os, signal, sys
ST = os.path.join(os.path.dirname(os.path.realpath(__file__)), "hc-state.json")
st = json.load(open(ST))
a = sys.argv[1:]
with open(st["log"], "a") as fh:
    fh.write(" ".join(a) + "\n")
def save():
    json.dump(st, open(ST, "w"))
def kill():
    try:
        os.kill(st["claude_pid"], signal.SIGTERM)
    except OSError:
        pass
if a[0] == "ls":
    print(json.dumps({"terminals": [{"name": n} for n in st["terminals"]]}))
elif a[0] == "get":
    t = st["terminals"].get(a[1], {})
    print(json.dumps({"pid": t.get("pid"), "foreground": {"pid": t.get("fg")}, "status": {"state": st["state"]}}))
elif a[0] == "read":
    print("\n".join(st.get("screen", []) + ["", "\u276f " + st["box"], ""]))
elif a[0] == "keys":
    for k in a[2:]:
        if k == "backspace":
            st["box"] = st["box"][:-1]
        elif k == "esc":
            if st.get("screen"):
                st["screen"] = []
                kill()
        elif k == "enter":
            cmd, st["box"] = st["box"], ""
            if st.get("screen"):
                st["entered_on_dialog"] = True
            elif cmd == "/harvest":
                if st.get("handoff") == "blocked":
                    st["state"] = "blocked"
                    st["screen"] = ["Do you want to proceed?", "  \u276f 1. Yes", "    2. No"]
                else:
                    open(st["brief"], "w").write("the new brief\n")
            elif cmd == "/exit":
                if st.get("feedback"):
                    st["screen"] = ["You have 1 unsent feedback draft \u00b7 Enter to review & send \u00b7 Esc to discard and exit"]
                else:
                    kill()
        elif st.get("drop") == k:
            st["drop"] = None
        else:
            st["box"] += k
    save()
'''


def stateful_hc(b, terminals, claude_pid, **st):
    p = os.path.join(b.home, "fake-hc2")
    with open(p, "w") as fh:
        fh.write(FAKE_HC2)
    os.chmod(p, 0o755)
    state = dict({"log": os.path.join(b.home, "hc2.log"), "terminals": terminals, "box": "", "state": "idle",
                  "claude_pid": claude_pid, "brief": os.path.join(b.repos["web"], "HANDOFF.md")}, **st)
    with open(os.path.join(b.home, "hc-state.json"), "w") as fh:
        json.dump(state, fh)
    if os.path.exists(state["log"]):
        os.remove(state["log"])
    b.herdr = p
    b.write_config()
    return p


def hc_state(b):
    with open(os.path.join(b.home, "hc-state.json")) as fh:
        return json.load(fh)


def hc_log(b):
    try:
        with open(os.path.join(b.home, "hc2.log")) as fh:
            return [l.split() for l in fh.read().splitlines()]
    except FileNotFoundError:
        return []


def orphan():
    """A process this test doesn't parent, so a kill leaves no zombie that still answers kill(0)."""
    out = subprocess.run(["sh", "-c", "sleep 300 >/dev/null 2>&1 & echo $!"], capture_output=True, text=True)
    return int(out.stdout.strip())


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def test_launch_quoting():
    print("cli: launch lines are shell-quoted; a bad template is named")
    b = Box()
    spaced = os.path.join(b.home, "dev", "my shop")
    os.makedirs(spaced)
    b.write_config({"agents": [{"name": "shop", "repo": spaced}]})
    r = b.run("summon", "shop")
    check("a repo path with a space is quoted in the cd and in {repo}",
          r.returncode == 0 and f"cd '{spaced}' && claude" in r.stdout)
    b.write_config({"agents": [{"name": "shop", "repo": spaced, "launch": "claude --add-dir {repo} --name {name}"}]})
    r = b.run("summon", "shop")
    check("…and in the template's {repo}", f"--add-dir '{spaced}' --name shop" in r.stdout)
    b.write_config({"launch": "claude --settings '{\"x\": 1}' --model {model}"})
    r = b.run("summon", "web")
    check("literal braces in the template: a clear error, no traceback",
          r.returncode != 0 and "Traceback" not in r.stderr and "{{" in r.stderr)
    b.write_config({"launch": "claude --model {modle}"})
    r = b.run("summon", "web")
    check("an unknown field in the template is named, no traceback",
          r.returncode != 0 and "Traceback" not in r.stderr and "modle" in r.stderr)
    b.done()


def test_down_driver():
    print("cli: unsummon against a stateful fake hc")
    b = Box()
    # finding: down waits on the session in the agent's terminal, not the newest one in the repo
    term_pid = orphan()
    b.session_file(term_pid, sessionId="w-term", name="web-term", cwd=b.repos["web"], startedAt=1)
    b.session_file(os.getpid(), sessionId="w-hand", name="web-by-hand", cwd=b.repos["web"], startedAt=2)
    stateful_hc(b, {"web": {"pid": 1, "fg": term_pid}}, term_pid)
    r = b.run("unsummon", "web", "--dry")
    check("two live sessions in the repo: unsummon picks the one in the agent's terminal",
          r.returncode == 0 and "w-term" in r.stdout and "w-hand" not in r.stdout)
    stateful_hc(b, {"web": {"pid": 1, "fg": 999999}}, term_pid)
    r = b.run("unsummon", "web", "--dry")
    check("no session runs in the agent's terminal: unsummon refuses", r.returncode != 0 and "terminal" in r.stderr)
    os.remove(os.path.join(b.sessions, f"{os.getpid()}.json"))

    # a garbled /harvest is cleared from the box
    stateful_hc(b, {"web": {"pid": 1, "fg": term_pid}}, term_pid, drop="v")
    r = b.run("unsummon", "web")
    st = hc_state(b)
    check("a command that didn't type cleanly: unsummon stops", r.returncode != 0)
    check("…and the box is empty again, so the next Enter runs nothing", st["box"] == "")
    check("…and no Enter was pressed", not any(l[:1] == ["keys"] and "enter" in l for l in hc_log(b)))

    # a dialog during /harvest is never answered
    stateful_hc(b, {"web": {"pid": 1, "fg": term_pid}}, term_pid, handoff="blocked")
    r = b.run("unsummon", "web")
    enters = [l for l in hc_log(b) if l[:1] == ["keys"] and "enter" in l]
    check("a dialog during /harvest: unsummon stops and says so",
          r.returncode != 0 and "dialog" in r.stderr and "Traceback" not in r.stderr)
    check("…without pressing Enter on it", len(enters) == 1 and not hc_state(b).get("entered_on_dialog"))
    check("…and the session stays", alive(term_pid))

    # the unsent-feedback dialog after /exit gets Esc, and only Esc
    stateful_hc(b, {"web": {"pid": 1, "fg": term_pid}}, term_pid, feedback=True)
    r = b.run("unsummon", "web")
    log = hc_log(b)
    keys = [k for l in log if l[:1] == ["keys"] for k in l[2:]]
    check("the unsent-feedback dialog: unsummon presses Esc and finishes",
          r.returncode == 0 and keys.count("esc") == 1 and not hc_state(b).get("entered_on_dialog"))
    check("…says the draft was discarded", "feedback" in r.stdout)
    check("…and the session is gone, its terminal killed and removed",
          not alive(term_pid) and ["kill", "web"] in log and ["rm", "web"] in log)
    if alive(term_pid):
        os.kill(term_pid, 9)
    b.done()


def test_dispatch():
    print("cli: organism orchestrate")
    b = Box()
    r = subprocess.run(["sh", os.path.join(ROOT, "bin", "organism"), "orchestrate", "--help"], capture_output=True,
                       text=True, env=b.env(), timeout=30)
    check("organism orchestrate --help names the commands",
          r.returncode == 0 and "orchestrate summon" in r.stdout and "orchestrate unsummon" in r.stdout
          and "/harvest" in r.stdout)
    old = subprocess.run(["sh", os.path.join(ROOT, "bin", "organism"), "fleet", "--help"], capture_output=True,
                         text=True, env=b.env(), timeout=30)
    check("organism fleet, the old name, still runs it", old.returncode == 0 and old.stdout == r.stdout)
    r = subprocess.run(["sh", os.path.join(ROOT, "bin", "organism"), "help"], capture_output=True,
                       text=True, env=b.env(), timeout=30)
    check("organism help lists orchestrate", "organism orchestrate" in r.stdout and "organism fleet" not in r.stdout)
    r = b.run("bogus")
    check("an unknown command exits 2 with the usage", r.returncode == 2 and "orchestrate roster" in r.stderr)
    b.done()


for t in (test_no_roster, test_lifecycle, test_messages, test_list_and_ledger, test_up_down, test_launch_quoting,
          test_down_driver, test_dispatch):
    t()

if _fails:
    print(f"FAIL: {len(_fails)} check(s)")
    sys.exit(1)
print("ALL PASS")
