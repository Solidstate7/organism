#!/usr/bin/env python3
"""Docs-vs-reality reconciler: refreshes the machine-written sections of wiki pages
from live repo probes, and raises mechanical alarms when reality drifts from the record.

Deterministic. No LLM, no guessing. For each pair in reconcile-pairings.json:

  1. Read mechanical signals ("probes") from the repo, shell commands whose
     stdout IS the displayed value. Expensive probes (test suites) only re-run
     when the repo HEAD changed since the last run (or --force).
  2. Re-render the fenced AUTO:BUILT-STATE section of the paired wiki page and
     replace ONLY the text between the fence markers. The user's prose is
     never touched. A page without exactly one well-formed fence is skipped
     with an alarm, this script never creates fences, only refreshes them.
  3. Compare probe values and pinned-row guards against the previous run
     (state file). Any transition raises a machine alarm with a stable ID,
     rendered into the fenced AUTO:ALARMS block of the Reconcile Queue.
     Volatile probes (e.g. event counters that grow constantly) display but
     never alarm. `--ack` clears acknowledged alarms and re-baselines.

Usage:
  reconcile.py --all [--force|--fast] [--dry-run]
  reconcile.py --pair <id> [...]
  reconcile.py --ack

Always exits 0 unless --strict (so it is safe to call from hooks/launchd).
Writes a line-per-event log to ~/.claude/logs/reconcile.log and a pre-change
backup of every page it edits to ~/.claude/logs/reconcile-backups/.
"""
import sys, os, json, re, glob, shutil, subprocess, datetime, argparse, fcntl, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # alert.py lives beside us, explicit so test imports from another cwd resolve it too
import hostid  # host_id() to host-key the non-git self-pairing
import role as _role  # per-device role gate
import alert  # single fan-out: ledger + delivery-confirmed Telegram (fallback IPs) + banner fallback

HOME = os.environ.get("HOME") or os.path.expanduser("~")
import where  # noqa: E402  (lib/ is on sys.path above)
HOOKS = where.HOOKS
CONFIG = where.pairings() or where.pairings_write_path()   # $RECONCILE_CONFIG wins inside both
STATE = os.path.join(HOME, ".claude", "state", "reconcile-state.json")
LOGDIR = os.path.join(HOME, ".claude", "logs")
LOGFILE = os.path.join(LOGDIR, "reconcile.log")
BACKUPS = os.path.join(LOGDIR, "reconcile-backups")

# TODAY is refreshed at the top of main(), a process that crosses midnight between
# import and run must not stamp yesterday into pages/alarms/state. The module-level default keeps helpers usable from bare test imports.
TODAY = datetime.date.today().isoformat()

BEGIN_RE = re.compile(r"^<!-- AUTO:BUILT-STATE:BEGIN[^>]*-->[ \t]*$", re.M)
END_RE = re.compile(r"^<!-- AUTO:BUILT-STATE:END -->[ \t]*$", re.M)
ALARM_BEGIN = "<!-- AUTO:ALARMS:BEGIN -->"
ALARM_END = "<!-- AUTO:ALARMS:END -->"
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
PLACEHOLDER = re.compile(r"\{([a-z0-9_]+(?:__at)?)\}")


def log(msg):
    os.makedirs(LOGDIR, exist_ok=True)
    # Stamp computed per call: an import-time NOW constant would mark every line of a
    # run identically (hiding probe duration/order, the only diagnostics) and carry the
    # previous day across midnight.
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOGFILE, "a", encoding="utf-8") as f:
        f.write(f"[{ts}] {msg}\n")


def notify_new_alarms(alarms, new_keys):
    """Push new alarms the first time they appear (only keys absent before this run,
    so a persistent unacked alarm pings once, not every day). Routed
    through alert.send(), the single fan-out (ledger, delivery-
    confirmed Telegram with fallback-IP retry, banner fallback). Returns the delivery
    bool so the caller can log truthfully. The ✅ Ack-all inline button rides through
    alert.send(reply_markup=…) with the callback data 'ack'."""
    if not new_keys:
        return True

    def clean(s):
        s = re.sub(r"[`*]", "", s or "")
        s = s.replace("\\", "").replace('"', "'")
        return " ".join(s.split())

    n = len(new_keys)
    lines = [clean(alarms[k].get("text", "")) for k in new_keys]
    title = "🔔 Reconciler: 1 new alarm" if n == 1 else f"🔔 Reconciler: {n} new alarms"
    msg = "\n".join(f"• {l[:200]}" for l in lines[:6])
    if n > 6:
        msg += f"\n…(+{n - 6} more)"
    msg += "\nAck: organism ack · Verify: organism check"
    # banner=True keeps the local ping the old code fired alongside the push
    return alert.send(title, msg, banner=True,
                      reply_markup={"inline_keyboard": [[{"text": "✅ Ack all", "callback_data": "ack"}]]})


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def load_state(dry=False):
    """State loader that refuses to mistake corruption for a fresh install. A
    truncated/corrupt reconcile-state.json silently treated as first-run discards
    every probe baseline and all open unacked alarms, drift since the last good run
    could then never alarm. Returns (state, corrupt_note):
    corrupt_note is None on a clean load or a true first run, else a string naming
    where the corpse was stashed so main() raises a visible machine alarm recording
    the state loss, never a silent wipe."""
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f), None
    except FileNotFoundError:
        return {}, None  # true first run, silent seed is correct
    except Exception as e:
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        corpse = f"{STATE}.corrupt-{stamp}"
        if dry:
            log(f"STATE CORRUPT ({e!r}), dry-run: file left in place, proceeding as first-run in memory")
            return {}, "(dry-run: corrupt file left in place)"
        try:
            os.replace(STATE, corpse)
            note = corpse
        except Exception as e2:
            note = f"(stash failed: {e2.__class__.__name__}; corrupt file left at {STATE})"
        log(f"STATE CORRUPT ({e!r}): stashed to {note}; proceeding as first-run WITH alarm")
        delivered = alert.send("⚠️ Reconciler state corrupt",
                               f"reconcile-state.json was unreadable ({e.__class__.__name__}) and stashed to {note}. "
                               "All probe baselines and open unacked alarms were lost: re-baselining from scratch this run.",
                               banner=False)
        log(f"state-corrupt alert delivered={delivered}")
        return {}, note


def save_state(state):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=1, ensure_ascii=False)
    os.replace(tmp, STATE)


def sh(cmd, cwd, timeout=30):
    """Run a shell command; return (ok, stdout_stripped)."""
    try:
        r = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True,
                           text=True, timeout=timeout)
        return r.returncode == 0, (r.stdout or "").strip()
    except Exception as e:
        return False, f"(probe error: {e.__class__.__name__})"


def git_facts(repo):
    ok, head = sh("git log -1 --format='%h\t%ad' --date=short", repo)
    ok2, commits = sh("git rev-list --count HEAD", repo)
    if not ok or "\t" not in head:
        return None, None, None
    h, d = head.split("\t", 1)
    return h.strip(), d.strip(), (commits.strip() if ok2 else "?")


def render(template, values):
    def repl(m):
        key = m.group(1)
        v = values.get(key)
        return v if v is not None else m.group(0)
    return PLACEHOLDER.sub(repl, template)


def normalized(text):
    """Compare-form: dates stripped so daily stamps alone never force a write."""
    return DATE_RE.sub("DATE", text)


def run_pair(pair, state, alarms, force, fast, dry):
    pid = pair["id"]
    repo = pair["repo"]
    page = pair["page"]
    # The non-git self-watch (the Organism install) has different built-state per machine
    # (a different `launchctl list`), so key ITS baseline by host, else two machines
    # cross-alarm on each other's inventory. git-backed pairs stay keyed by id (the
    # repo content is shared). First run on a new host seeds silently.
    skey = pid if pair.get("git", True) else f"{pid}@{hostid.host_id()}"
    pstate = state.setdefault("pairs", {}).setdefault(skey, {})
    probes_state = pstate.setdefault("probes", {})

    if pair.get("git", True):
        head, head_date, commits = git_facts(repo)
        if head is None:
            alarms[f"ALARM-{pid}-git"] = {"date": TODAY, "text": f"`{pid}`: repo unreadable or not a git repo at `{repo}`"}
            log(f"{pid}: SKIP, git facts unavailable")
            return False
        head_changed = pstate.get("head") != head
    elif pair.get("git_repo"):
        # A non-git folder backed by a git repo (the Organism install: ~/.claude is symlinks into
        # ~/.organism). Anchor drift-detection on the backing repo's HEAD, so an inventory change
        # that rode in on a commit/pull reads as INTENDED, not drift.
        head, head_date, commits = git_facts(os.path.expanduser(pair["git_repo"]))
        head_changed = (pstate.get("head") != head) if head is not None else True
        if head is None:
            head, head_date, commits = "—", TODAY, "—"   # anchor unreadable → fail safe to always-probe
    else:
        # a non-git folder with no HEAD anchor: nothing to diff, always re-probe.
        head, head_date, commits = "—", TODAY, "—"
        head_changed = True

    values = {"head": head, "head_date": head_date, "commits": commits, "today": TODAY}

    # --- probes ---
    for name, spec in pair.get("probes", {}).items():
        expensive = spec.get("expensive", False)
        cached = probes_state.get(name, {})
        if expensive and not force and (fast or not head_changed) and cached.get("value") is not None:
            values[name] = cached["value"]
            values[name + "__at"] = cached.get("at", "?")
            continue
        ok, out = sh(spec["cmd"], repo, timeout=spec.get("timeout", 30))
        value = out if ok and out else (out or "(no output)")
        if not ok:
            value = f"⚠️ {value}" if "probe error" in value else f"⚠️ probe failed: {value or 'exit != 0'}"
        # alarm on transition (skip volatile, skip first-ever measurement). A head_gated pair
        # (the Organism install) suppresses a repo-backed probe change that rode in on a HEAD move, an
        # intended commit/pull, not drift; runtime probes (loaded agents, global hook) still alarm.
        old = cached.get("value")
        intended = pair.get("head_gated") and head_changed and not spec.get("runtime", False)
        changed = old is not None and old != value and not intended
        # Benign-transition filter: a probe may declare
        # `alarm_only_if`, a regex over the NEW value. Its transitions alarm only when
        # the new value matches (i.e. looks like a regression); anything else is normal
        # progress, re-baselined and logged, never a user-facing alarm.
        aoi = spec.get("alarm_only_if")
        if changed and aoi and not re.search(aoi, value):
            log(f"{pid}/{name}: benign transition (“{old}” → “{value}”): re-baselined, no alarm")
            changed = False
        if (not spec.get("volatile", False)) and changed:
            alarms[f"ALARM-{pid}-{name}"] = {"date": TODAY,
                "text": f"`{pid}`/{name} changed: “{old}” → “{value}”"}
        probes_state[name] = {"value": value, "at": TODAY}
        values[name] = value
        values[name + "__at"] = TODAY

    # --- guards (pinned-row verifications; boolean by exit code) ---
    guards_state = pstate.setdefault("guards", {})
    guard_ok = {}
    for name, cmd in pair.get("guards", {}).items():
        ok, _ = sh(cmd, repo, timeout=20)
        guard_ok[name] = ok
        old = guards_state.get(name)
        if old is not None and old != ok and not ok:
            alarms[f"ALARM-{pid}-guard-{name}"] = {"date": TODAY,
                "text": f"`{pid}`: pinned-row guard **{name}** now FAILS: the pinned fact may no longer be true"}
        guards_state[name] = ok

    # --- render section ---
    lines = [f"<!-- AUTO:BUILT-STATE:BEGIN repo={pair.get('repo_short', pid)} commit={head} derived={TODAY} -->",
             pair.get("heading", "## ⚙️ Built State: auto-derived from the repo"), ""]
    lines.append(render(pair["header_note"], values))
    lines += ["", "| Component | Current status | Evidence (repo) |", "| --- | --- | --- |"]
    for row in pair["rows"]:
        status = render(row["status"], values)
        evidence = render(row["evidence"], values)
        g = row.get("guard")
        if g is not None and not guard_ok.get(g, True):
            status = f"⚠️ UNVERIFIED ({TODAY}): " + status
        lines.append(f"| {row['component']} | {status} | {evidence} |")
    footer = pair.get("footer", [])
    if footer:
        lines.append("")
        lines += [render(l, values) for l in footer]
    lines.append("<!-- AUTO:BUILT-STATE:END -->")
    section = "\n".join(lines)

    # --- fence replace ---
    try:
        with open(page, encoding="utf-8") as f:
            content = f.read()
    except Exception:
        alarms[f"ALARM-{pid}-page"] = {"date": TODAY, "text": f"`{pid}`: paired page unreadable: `{page}`"}
        return False
    begins, ends = BEGIN_RE.findall(content), END_RE.findall(content)
    if len(begins) != 1 or len(ends) != 1:
        alarms[f"ALARM-{pid}-fence"] = {"date": TODAY,
            "text": f"`{pid}`: page has {len(begins)} BEGIN / {len(ends)} END fence markers (need exactly 1+1): refusing to write"}
        log(f"{pid}: SKIP, malformed fence on {page}")
        return False
    start = BEGIN_RE.search(content).start()
    end = END_RE.search(content).end()
    if start >= end:
        # The count check alone passes an END-before-BEGIN page, and slicing
        # with start>end duplicated the between-marker prose and doubled both markers on
        # write, treat reversed markers as a malformed fence, same as wrong counts.
        # Never rewrite such a page.
        alarms[f"ALARM-{pid}-fence"] = {"date": TODAY,
            "text": f"`{pid}`: fence END marker precedes BEGIN: malformed fence, refusing to write"}
        log(f"{pid}: SKIP, reversed fence markers on {page}")
        return False
    old_section = content[start:end]
    if normalized(old_section) == normalized(section):
        pstate["head"] = head
        log(f"{pid}: no change (head {head})")
        return True
    new_content = content[:start] + section + content[end:]
    if not new_content.endswith("\n"):
        new_content += "\n"
    # bump frontmatter `updated:` (first occurrence inside leading frontmatter)
    m = re.match(r"^---\n.*?\n---\n", new_content, re.S)
    if m:
        fm = m.group(0)
        fm2 = re.sub(r"^updated: \d{4}-\d{2}-\d{2}$", f"updated: {TODAY}", fm, count=1, flags=re.M)
        new_content = fm2 + new_content[len(fm):]
    if dry:
        log(f"{pid}: DRY-RUN, would update {page}")
        print(f"--- {pid}: section would become ---\n{section}\n")
        return True
    os.makedirs(BACKUPS, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    shutil.copy2(page, os.path.join(BACKUPS, f"{stamp}-{os.path.basename(page)}"))
    tmp = page + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(new_content)
    os.replace(tmp, page)
    pstate["head"] = head
    log(f"{pid}: UPDATED {os.path.basename(page)} (head {head})")
    return True


def write_alarms(queue_page, alarms, dry):
    if not os.path.exists(queue_page):
        log("alarms: queue page missing: skipped")
        return
    with open(queue_page, encoding="utf-8") as f:
        content = f.read()
    if ALARM_BEGIN not in content or ALARM_END not in content:
        log("alarms: no AUTO:ALARMS fence in queue: skipped")
        return
    if alarms:
        body = "\n".join(f"- **{aid}** ({a['date']}): {a['text']}"
                         for aid, a in sorted(alarms.items()))
        body += "\n\n*Run `reconcile.py --ack` after triaging to clear these and re-baseline.*"
    else:
        body = "*(none: everything matches the last acknowledged baseline)*"
    block = f"{ALARM_BEGIN}\n{body}\n{ALARM_END}"
    # Function replacement, not a template: alarm text carries raw probe
    # stdout, and a raw string re.sub raises re.error on any '\1'/'\<letter>' in it,
    # wedging the queue + notifications on every run until --ack.
    new = re.sub(re.escape(ALARM_BEGIN) + r".*?" + re.escape(ALARM_END),
                 lambda m: block, content, flags=re.S)
    if new != content and not dry:
        tmp = queue_page + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(new)
        os.replace(tmp, queue_page)
        log(f"alarms: queue updated ({len(alarms)} open)")


def main():
    # Refresh the run-scoped date/time here: the import-time values stamp
    # yesterday into pages/alarms/state when the process crosses midnight.
    global TODAY
    TODAY = datetime.date.today().isoformat()
    run_started = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--pair")
    ap.add_argument("--force", action="store_true", help="re-run expensive probes")
    ap.add_argument("--fast", action="store_true", help="never run expensive probes")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--ack", action="store_true", help="acknowledge+clear alarms, re-baseline")
    ap.add_argument("--announce", action="store_true",
                    help="push a 'reconcile ran' heartbeat to Telegram at the end (wire on the daily run only)")
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args()

    cfg = load_json(CONFIG, None)
    if cfg is None:
        log("FATAL: config unreadable"); sys.exit(1 if args.strict else 0)
    state, corrupt_note = load_state(dry=args.dry_run)
    alarms = {} if args.ack else state.get("alarms", {})

    if args.ack:
        state["alarms"] = {}
        save_state(state)
        write_alarms(cfg["queue_page"], {}, False)
        log("alarms acknowledged & cleared")
        return

    # Role gate: the SCHEDULED run (the daily job is the only caller of --announce) belongs
    # to the one machine that owns the shared jobs, so two Macs never both run it. A run
    # by hand (`organism check` = --all) and the session-end --pair run on any machine,
    # like --ack, so `organism check` works on a machine that has not claimed the role
    # (setup does not claim it by default).
    if args.all and args.announce and not _role.should_run("singleton_bookkeeping"):
        log("role not assigned (singleton_bookkeeping); skipping scheduled reconcile")
        print("this machine doesn't own the shared jobs, so the scheduled doc check did not run here.\n"
              "To run it daily on this Mac (only one Mac should):  organism setup  → role: claim owner\n"
              "To run it once now, on any Mac:  organism check",
              file=sys.stderr)
        return

    if corrupt_note:
        # Visible machine alarm so the forced re-baseline is an EVENT in the queue, not
        # a silent wipe. load_state already pushed the
        # Telegram alert, so this key is added BEFORE the prior_alarm_keys snapshot on
        # purpose, it lands in the queue page without a second push.
        alarms["ALARM-reconciler-state"] = {"date": TODAY,
            "text": f"reconciler state file was corrupt: stashed to `{corrupt_note}`; "
                    "every probe baseline and open alarm was lost and re-seeded this run"}

    pairs = cfg["pairs"]
    if args.pair:
        pairs = [p for p in pairs if p["id"] == args.pair]
        if not pairs:
            log(f"unknown pair: {args.pair}"); sys.exit(1 if args.strict else 0)
    elif not args.all:
        print(__doc__); return

    prior_alarm_keys = set(alarms.keys())
    prior_flags = set(state["flag_ids"]) if "flag_ids" in state else None
    for pair in pairs:
        try:
            run_pair(pair, state, alarms, args.force, args.fast, args.dry_run)
        except Exception as e:
            log(f"{pair.get('id','?')}: ERROR {e!r}")
    state["alarms"] = alarms
    state["last_run"] = run_started
    if not args.dry_run:
        save_state(state)
        write_alarms(cfg["queue_page"], alarms, args.dry_run)
        new_keys = [k for k in alarms if k not in prior_alarm_keys]
        if new_keys:
            delivered = notify_new_alarms(alarms, new_keys)
            log(f"notified: {len(new_keys)} new alarm(s) (delivered={delivered})")
        # new judgment flags (added to the queue by maintainer sessions) → alert too
        n_new_flags = 0
        try:
            qtext = open(cfg["queue_page"], encoding="utf-8").read()
            current_flags = set(re.findall(r'^- [🔴🟡🟢] \*\*([A-Za-z]+-\d+)\*\*', qtext, re.M))
            if prior_flags is not None:
                new_flags = sorted(current_flags - prior_flags)
                n_new_flags = len(new_flags)
                if new_flags:
                    delivered = alert.send("", "🟡 New Reconcile Queue flag(s): " + ", ".join(new_flags)
                                           + "; needs your verdict. See: organism check",
                                           banner=False)
                    log(f"notified: {len(new_flags)} new flag(s) (delivered={delivered})")
            state["flag_ids"] = sorted(current_flags)
            save_state(state)
        except Exception as e:
            log(f"flag-detect error: {e!r}")
        # heartbeat: a daily 'reconcile ran' confirmation so silence means it FAILED to run
        if args.announce:
            open_alarms = len(alarms)
            if new_keys or n_new_flags:
                summary = f"{len(new_keys)} new alarm(s), {n_new_flags} new flag(s): see above"
            elif open_alarms:
                summary = f"clean run; {open_alarms} alarm(s) still open & unacked"
            else:
                summary = "✅ clean: everything matches baseline"
            delivered = alert.send("", f"🔧 Reconcile ran ({TODAY}): {summary}.\nVerify: organism check",
                                   banner=False)
            # Log the CONFIRMED delivery bool: logging
            # 'heartbeat sent' unconditionally after a fire-and-forget urlopen would make
            # the dead-man's-switch lie during a Telegram outage: silence
            # AND a success log at once. alert.send shows a
            # local banner fallback when delivery fails, so False here is never silent.
            log(f"announce: heartbeat delivered={delivered}")
            # Nothing else runs here: the reconciler stays deterministic. Host-local jobs
            # run as their own launchd agents, registered via the gitignored
            # ~/.claude/state/agents.local manifest (see launchd/install-agents.sh).


def locked_main(wait=600):
    """Run main() with the state lock held. Runs overlap (one per ending session plus
    the daily run), and each one loads the state at the start and saves it at the
    end, so without the lock a slower run would erase an alarm a faster one raised."""
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    with open(os.path.join(os.path.dirname(STATE), "reconcile.lock"), "w") as lf:
        deadline = time.time() + wait
        while True:
            try:
                fcntl.flock(lf, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.time() > deadline:
                    log(f"another reconcile run held the lock for {wait} s; skipped")
                    return
                time.sleep(0.5)
        try:
            main()
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


if __name__ == "__main__":
    try:
        locked_main()
    except SystemExit:
        raise
    except Exception as e:
        log(f"FATAL: {e!r}")
        sys.exit(0)
