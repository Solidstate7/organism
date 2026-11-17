#!/usr/bin/env python3
"""tests for claude/hooks/fleet.py: the roster, the ledger the hooks write, and the CLI.
  - no roster: the hook does nothing, prints nothing, writes no ledger
  - SessionStart in an agent's repo: summon, then resummon with the session it continues,
    the brief it starts from and its generation; resume for `claude --resume`
  - headless runs, other folders, a second wiring and clear/compact add nothing
  - SessionEnd: harvest when the brief was rewritten, close (and why) when not, and
    "handoff is off" for an agent whose roster says so
  - SendMessage: order, report and agent-to-agent message, first line only, redacted, clipped
  - fleet list: live, harvested, closed, never summoned, generations
  - fleet ledger: --since, --agent, --json, --md (never over a file it didn't write)
  - up without herdr-core prints the command; down without it refuses; down --dry plans
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
                 PATH="/usr/bin:/bin")
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

    def send(self, cwd, to, message, sid="s-send", tool="SendMessage"):
        return self.hook({"hook_event_name": "PostToolUse", "session_id": sid, "cwd": cwd,
                          "tool_name": tool, "tool_input": {"to": to, "message": message},
                          "tool_response": {}})

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
    r = b.run("list")
    check("…but fleet list says the roster can't be read", r.returncode != 0 and "can't be read" in r.stderr)
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
    b.start("web", "s1", source="clear")
    b.start("web", "s1", source="compact")
    check("clear and compact add nothing", len(b.events()) == 1)
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
    b.done()


def test_list_and_ledger():
    print("cli: fleet list and fleet ledger")
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
    r = b.run("list")
    out = r.stdout
    rows = {l.split()[0]: l for l in out.splitlines()[1:] if l.strip()}
    check("list runs", r.returncode == 0 and set(rows) == {"web", "api", "notes"})
    check("web: harvested, 2 generations, its CLAUDE.md role",
          "harvested" in rows.get("web", "") and " 2 " in rows["web"] and "the shop front" in rows["web"])
    check("api: live as its session name, 0 generations recorded", "live as api-9" in rows.get("api", ""))
    check("notes: closed, its roster role", "closed" in rows.get("notes", "") and "keeps notes" in rows["notes"])
    os.remove(os.path.join(b.sessions, f"{live}.json"))
    b.write_config({"agents": [{"name": "web", "repo": "~/dev/web"}, {"name": "spare", "repo": "~/dev/spare"}]})
    check("an agent with no events: never summoned", "never summoned" in b.run("list").stdout)
    b.write_config()

    r = b.run("ledger")
    check("ledger prints every event and skips the torn line",
          r.returncode == 0 and len(r.stdout.splitlines()) == len(b.events()) and "old" in r.stdout)
    r = b.run("ledger", "--since", "1h")
    check("--since 1h drops the old event", "old" not in r.stdout and "harvest" in r.stdout)
    r = b.run("ledger", "--since", "2025-12-31")
    check("--since a date keeps it", "old" in r.stdout)
    r = b.run("ledger", "--since", "soon")
    check("--since nonsense is refused", r.returncode != 0 and "--since" in r.stderr)
    r = b.run("ledger", "--agent", "notes", "--json")
    lines = [json.loads(l) for l in r.stdout.splitlines()]
    check("--agent notes --json: only notes events, as JSON",
          lines and all(e.get("agent") == "notes" for e in lines) and [e["event"] for e in lines] == ["summon", "close"])
    md = os.path.join(b.home, "Ledger.md")
    r = b.run("ledger", "--md", md)
    with open(md) as fh:
        text = fh.read()
    check("--md writes a table with the mark", r.returncode == 0 and text.startswith("<!-- written by organism fleet")
          and "| When | Event | Agent | What |" in text and "continues w1" in text)
    b.run("ledger", "--md", md)
    check("…and rewrites its own file", r.returncode == 0)
    other = os.path.join(b.home, "Mine.md")
    with open(other, "w") as fh:
        fh.write("my notes\n")
    r = b.run("ledger", "--md", other)
    with open(other) as fh:
        kept = fh.read()
    check("--md never replaces a file it didn't write", r.returncode != 0 and kept == "my notes\n")
    r = b.run("ledger", "--md", other, "--force")
    check("…unless --force", r.returncode == 0)
    b.done()


def fake_hc(home, terminals, state="idle"):
    p = os.path.join(home, "fake-hc")
    with open(p, "w") as fh:
        fh.write("#!/bin/sh\n"
                 f'echo "$@" >> "{home}/hc.log"\n'
                 'case "$1" in\n'
                 f"  ls) echo '{json.dumps({'terminals': [{'name': t} for t in terminals]})}' ;;\n"
                 f"  get) echo '{json.dumps({'status': {'state': state}})}' ;;\n"
                 "  read) echo '❯ ' ;;\n"
                 "esac\n")
    os.chmod(p, 0o755)
    return p


def test_up_down():
    print("cli: up and down")
    b = Box()
    r = b.run("up", "api")
    check("up without herdr-core prints the command for the agent's repo",
          r.returncode == 0 and f"cd {b.repos['api']} && claude --model sonnet --effort xhigh --name api" in r.stdout)
    check("…and calls it a summon, generation 1", "summon api" in r.stdout and "generation 1" in r.stdout
          and "resummon" not in r.stdout)
    r = b.run("up", "api", "--effort", "low")
    check("--effort overrides the roster", "--effort low" in r.stdout)
    b.session_file(FAKE_PID, sessionId="x", name="me")
    b.brief("api", "b\n")
    b.start("api", "a1")
    b.brief("api", "c\n")
    b.end("api", "a1")
    r = b.run("summon", "api")
    check("up on a harvested agent: a resummon, generation 2, from its brief",
          "resummon api" in r.stdout and "generation 2" in r.stdout and "from the brief of" in r.stdout)
    r = b.run("up", "nobody")
    check("up on an unknown agent is refused", r.returncode != 0 and "no agent nobody" in r.stderr)
    r = b.run("down", "api")
    check("down without herdr-core refuses and says how to harvest by hand",
          r.returncode != 0 and "/handoff" in r.stderr and "/exit" in r.stderr)

    hc = fake_hc(b.home, ["notes", "web"])
    b.herdr = hc
    b.write_config()
    live = str(os.getpid())
    b.session_file(live, sessionId="n-live", name="notes-2", cwd=b.repos["notes"])
    r = b.run("down", "notes", "--dry")
    check("down --dry on a handoff-off agent: plans a close without /handoff",
          r.returncode == 0 and "close notes" in r.stdout and "no /handoff" in r.stdout and "handoff is off" in r.stdout)
    with open(os.path.join(b.home, "hc.log")) as fh:
        log = fh.read()
    check("…and types nothing", "keys" not in log and "kill" not in log)
    b.session_file(live, sessionId="w-live", name="web-2", cwd=b.repos["web"])
    r = b.run("harvest", "web", "--dry")
    check("harvest --dry on a normal agent: plans /handoff, /exit, kill, rm",
          r.returncode == 0 and "harvest web" in r.stdout and "/handoff, /exit" in r.stdout)
    r = b.run("down", "web", "--no-handoff", "--dry")
    check("--no-handoff: plans a close", "close web" in r.stdout and "--no-handoff" in r.stdout)
    r = b.run("up", "web", "--dry")
    check("up on a live agent says where it lives and starts nothing",
          r.returncode == 0 and "already live: web-2" in r.stdout)
    os.remove(os.path.join(b.sessions, f"{live}.json"))
    r = b.run("up", "web", "--dry")
    check("up --dry with herdr-core prints what it would run", r.returncode == 0 and "would run:" in r.stdout)
    r = b.run("down", "web")
    check("down on an agent that isn't live is refused", r.returncode != 0 and "not live" in r.stderr)

    hc = fake_hc(b.home, ["web"], state="working")
    b.session_file(live, sessionId="w-live", name="web-2", cwd=b.repos["web"])
    r = b.run("down", "web")
    check("down on a working agent is refused", r.returncode != 0 and "working" in r.stderr)
    b.done()


def test_dispatch():
    print("cli: organism fleet")
    b = Box()
    r = subprocess.run(["sh", os.path.join(ROOT, "bin", "organism"), "fleet", "--help"], capture_output=True,
                       text=True, env=b.env(), timeout=30)
    check("organism fleet --help names the lifecycle",
          r.returncode == 0 and "summon" in r.stdout and "harvest" in r.stdout and "resummon" in r.stdout)
    r = subprocess.run(["sh", os.path.join(ROOT, "bin", "organism"), "help"], capture_output=True,
                       text=True, env=b.env(), timeout=30)
    check("organism help lists fleet", "organism fleet" in r.stdout)
    r = b.run("bogus")
    check("an unknown fleet command exits 2 with the usage", r.returncode == 2 and "fleet list" in r.stderr)
    b.done()


for t in (test_no_roster, test_lifecycle, test_messages, test_list_and_ledger, test_up_down, test_dispatch):
    t()

if _fails:
    print(f"FAIL: {len(_fails)} check(s)")
    sys.exit(1)
print("ALL PASS")
