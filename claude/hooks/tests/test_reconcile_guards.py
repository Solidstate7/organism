#!/usr/bin/env python3
"""tests for reconcile.py's write-safety guards: fence validation must
check ORDER as well as count (an END-before-BEGIN page would otherwise pass
the 1+1 count check and get its between-marker prose duplicated on write), and the
alarm-queue insertion must survive backslash-bearing alarm text (probe stdout flows verbatim into alarm text, and a raw re.sub replacement
raised re.error on any '\\1', wedging the queue until --ack).

HOME is redirected to a temp dir BEFORE the reconcile import: the module computes its
state/log/backup paths at import time, so everything (logs, page backups) lands in the
sandbox and the live ~/.claude is never touched. No probes/guards are configured on the
test pairs, so nothing shells out beyond hostid's identity lookup (read-only).
Run:  python3 claude/hooks/tests/test_reconcile_guards.py"""
import os
import shutil
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix="reconcile-test-")
os.environ["HOME"] = TMP    # before import: reconcile freezes HOME-derived paths at import

HOOKS = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, HOOKS)
import reconcile

_fails = []


def check(name, cond):
    print(("  ✓ " if cond else "  ✗ ") + name)
    if not cond:
        _fails.append(name)


def mkpair(pid, page):
    # git:false + no git_repo → the always-re-probe path; no probes/guards configured.
    return {"id": pid, "git": False, "repo": TMP, "page": page,
            "header_note": "state as of {today}",
            "rows": [{"component": "comp", "status": "ok", "evidence": "`{head}`"}]}


def write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def test_fence_order():
    print("fence guard: marker ORDER, not just count")
    page = os.path.join(TMP, "reversed.md")
    reversed_doc = ("KEEP-ABOVE\n<!-- AUTO:BUILT-STATE:END -->\nprose between\n"
                    "<!-- AUTO:BUILT-STATE:BEGIN -->\nKEEP-BELOW\n")
    write(page, reversed_doc)
    alarms = {}
    ok = reconcile.run_pair(mkpair("rev", page), {}, alarms, False, False, False)
    check("reversed markers → refused", ok is False)
    check("fence alarm raised", "ALARM-rev-fence" in alarms)
    check("alarm names the reversal",
          "END marker precedes BEGIN" in alarms.get("ALARM-rev-fence", {}).get("text", ""))
    check("page NOT rewritten (no prose duplication)", read(page) == reversed_doc)


def test_fence_count():
    print("fence guard: marker counts")
    page = os.path.join(TMP, "nofence.md")
    write(page, "no markers here\n")
    alarms = {}
    ok = reconcile.run_pair(mkpair("zero", page), {}, alarms, False, False, False)
    check("0 fences → refused", ok is False)
    check("count alarm raised", "0 BEGIN / 0 END" in alarms.get("ALARM-zero-fence", {}).get("text", ""))

    page2 = os.path.join(TMP, "double.md")
    write(page2, "<!-- AUTO:BUILT-STATE:BEGIN -->\na\n<!-- AUTO:BUILT-STATE:BEGIN -->\n"
                 "b\n<!-- AUTO:BUILT-STATE:END -->\n")
    alarms = {}
    ok = reconcile.run_pair(mkpair("dup", page2), {}, alarms, False, False, False)
    check("2 BEGINs → refused", ok is False and "ALARM-dup-fence" in alarms)


def test_fence_replace_happy_path():
    print("well-formed fence: section replaced, prose untouched, idempotent")
    page = os.path.join(TMP, "good.md")
    write(page, "KEEP-ABOVE\n\n<!-- AUTO:BUILT-STATE:BEGIN -->\nold body\n"
                "<!-- AUTO:BUILT-STATE:END -->\n\nKEEP-BELOW\n")
    alarms, state = {}, {}
    ok = reconcile.run_pair(mkpair("good", page), state, alarms, False, False, False)
    after = read(page)
    check("write accepted", ok is True and not alarms)
    check("user prose preserved", "KEEP-ABOVE" in after and "KEEP-BELOW" in after)
    check("old fenced body replaced", "old body" not in after and "| comp | ok |" in after)
    check("still exactly one BEGIN + one END",
          len(reconcile.BEGIN_RE.findall(after)) == 1 and len(reconcile.END_RE.findall(after)) == 1)
    ok2 = reconcile.run_pair(mkpair("good", page), state, alarms, False, False, False)
    check("second run is a no-change no-op (date-normalized compare)",
          ok2 is True and read(page) == after)

    # dry-run never writes
    write(page, "X\n<!-- AUTO:BUILT-STATE:BEGIN -->\nstale\n<!-- AUTO:BUILT-STATE:END -->\nY\n")
    before = read(page)
    ok3 = reconcile.run_pair(mkpair("dry", page), {}, {}, False, False, True)
    check("dry-run leaves the page untouched", ok3 is True and read(page) == before)


def test_write_alarms_backslashes():
    print("write_alarms: backslash-bearing alarm text")
    queue = os.path.join(TMP, "queue.md")
    write(queue, "# Queue\n<!-- AUTO:ALARMS:BEGIN -->\nold\n<!-- AUTO:ALARMS:END -->\ntail\n")
    evil = r"probe said \1 and \g<0> and C:\Users\path"   # raw re.sub template poison
    try:
        reconcile.write_alarms(queue, {"ALARM-x-y": {"date": "2026-07-03", "text": evil}}, False)
        raised = False
    except Exception:
        raised = True
    after = read(queue)
    check("no re.error on template metacharacters", raised is False)
    check("alarm text inserted verbatim", evil in after)
    check("markers intact after rewrite",
          after.count(reconcile.ALARM_BEGIN) == 1 and after.count(reconcile.ALARM_END) == 1)
    check("surrounding prose preserved", after.startswith("# Queue\n") and after.endswith("tail\n"))

    reconcile.write_alarms(queue, {}, False)
    check("empty alarm set renders the (none) baseline line",
          "*(none: everything matches the last acknowledged baseline)*" in read(queue))


def test_role_gate():
    """Only the SCHEDULED run (--all --announce) is limited to the machine that owns the
    shared jobs; `organism check` (--all) runs on any machine."""
    import json
    import subprocess
    print("role gate: the daily run needs the owner role, a run by hand does not")
    home = os.path.join(TMP, "gate-home")       # no host.json: a machine that claimed nothing
    os.makedirs(home)
    page = os.path.join(TMP, "gate-page.md")
    write(page, "mine\n<!-- AUTO:BUILT-STATE:BEGIN -->\nstale\n<!-- AUTO:BUILT-STATE:END -->\n")
    queue = os.path.join(TMP, "gate-queue.md")
    write(queue, "# Queue\n<!-- AUTO:ALARMS:BEGIN -->\n<!-- AUTO:ALARMS:END -->\n")
    cfg = os.path.join(TMP, "gate-pairings.json")
    pair = mkpair("gate", page)
    pair["repo"] = home
    write(cfg, json.dumps({"queue_page": queue, "pairs": [pair]}))
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE_")}
    env.update(HOME=home, RECONCILE_CONFIG=cfg)
    run = lambda *a: subprocess.run([sys.executable, os.path.join(HOOKS, "reconcile.py"), *a],
                                    env=env, capture_output=True, text=True, timeout=60)
    r = run("--all", "--announce")
    check("the scheduled run skips a machine without the role", "stale" in read(page))
    check("…and says how to run it", "organism check" in r.stderr)
    run("--all")
    check("a run by hand refreshes the page anyway", "stale" not in read(page) and "state as of" in read(page))
    check("…and keeps the prose above the fence", read(page).startswith("mine\n"))

def test_runs_take_turns():
    """Overlapping runs each load the state at the start and save it at the end, so
    they must take turns or a slower run erases an alarm a faster one raised."""
    import fcntl
    import json
    import subprocess
    import time
    print("overlapping runs take turns on the state lock")
    home = os.path.join(TMP, "lock-home")
    os.makedirs(os.path.join(home, ".claude", "state"))
    page = os.path.join(TMP, "lock-page.md")
    write(page, "<!-- AUTO:BUILT-STATE:BEGIN -->\nstale\n<!-- AUTO:BUILT-STATE:END -->\n")
    queue = os.path.join(TMP, "lock-queue.md")
    write(queue, "# Queue\n<!-- AUTO:ALARMS:BEGIN -->\n<!-- AUTO:ALARMS:END -->\n")
    cfg = os.path.join(TMP, "lock-pairings.json")
    pair = mkpair("lock", page)
    pair["repo"] = home
    write(cfg, json.dumps({"queue_page": queue, "pairs": [pair]}))
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE_")}
    env.update(HOME=home, RECONCILE_CONFIG=cfg)
    with open(os.path.join(home, ".claude", "state", "reconcile.lock"), "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        p = subprocess.Popen([sys.executable, os.path.join(HOOKS, "reconcile.py"), "--all"],
                             env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(2)
        check("a run waits while another holds the lock", p.poll() is None and "stale" in read(page))
        fcntl.flock(lf, fcntl.LOCK_UN)
    p.wait(timeout=60)
    check("…and runs once it is free", "stale" not in read(page))


def test_alarm_text_names_cli():
    print("alarm text points at the organism CLI, not chat-bot commands")
    sent = []
    orig = reconcile.alert.send
    reconcile.alert.send = lambda title, msg, **kw: sent.append(title + "\n" + msg) or True
    try:
        reconcile.notify_new_alarms({"k": {"text": "drift"}}, ["k"])
    finally:
        reconcile.alert.send = orig
    text = "".join(sent)
    check("an alarm is sent", "drift" in text)
    check("…naming organism ack and organism check", "organism ack" in text and "organism check" in text)
    import re
    check("…and no slash commands", not re.search(r"(^|\s)/(ack|detail|status)\b", text))
    src = read(os.path.join(HOOKS, "reconcile.py"))
    check("no bot command left in any alert text", "/detail reconcile" not in src and "/status or" not in src)


if __name__ == "__main__":
    try:
        test_role_gate()
        test_fence_order()
        test_fence_count()
        test_fence_replace_happy_path()
        test_write_alarms_backslashes()
        test_alarm_text_names_cli()
        test_runs_take_turns()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    print(f"\n{'ALL PASS' if not _fails else 'FAILED: ' + ', '.join(_fails)}")
    sys.exit(1 if _fails else 0)
