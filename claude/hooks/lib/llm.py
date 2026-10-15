#!/usr/bin/env python3
"""hooks/lib/llm.py: the shared headless `claude -p` engine.

One implementation, shared by every job that calls the model, of:
  • defuse():   neutralize harness magic keywords inside QUOTED content before it
                 rides in a prompt (a quoted keyword would otherwise trigger it).
  • one_shot(): a single-turn, tool-less `claude -p` call with transient-retry
                 handling and a failure-logging contract that keeps the real error text
                 (`claude -p` prints API errors to STDOUT and exits 1) without ever
                 copying the prompt into a log (a TimeoutExpired repr embeds the
                 whole prompt).
  • llm_lock(): best-effort cross-process serialisation of heavy claude -p jobs
                 (scheduled jobs that start together contend on one subscription);
                 on timeout it WARNS and yields anyway; a scheduled
                 review must degrade to unserialised, never deadlock.

Usage (consumers put hooks/lib on sys.path first, like every lib client):
    import llm
    body = llm.one_shot(prompt, model="sonnet", log=log)
"""
import fcntl
import os
import re
import subprocess
import sys
import time
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths

# Transient server-side failures (overload / rate-limit / 5xx) are RETRIED with backoff
# rather than surfacing as a hard failure: a momentary `529 Overloaded` should not
# kill a scheduled job.
_TRANSIENT_RE = re.compile(
    r"overloaded|rate.?limit|server-side issue|service unavailable|temporarily|"
    r"api error:\s*(?:408|429|5\d\d)|\b(?:408|429|500|502|503|504|529)\b", re.I)

_REMINDER_RE = re.compile(r"<system-reminder>.*?</system-reminder>", re.DOTALL)

_LOCK_POLL_S = 5


def _is_transient(text):
    return bool(text and _TRANSIENT_RE.search(text))


def defuse(text):
    """Neutralize harness magic keywords in quoted content bound for a prompt: a
    mirrored '/effort ultracode' in the user's OWN past prompts is a magic keyword
    to the harness (enables workflow orchestration mid-one-shot → the stray tool call
    that once broke the morning assessment), and an embedded <system-reminder>
    block would read as harness-injected instructions rather than data."""
    text = re.sub(r"ultracode", "ultra-code", text, flags=re.I)
    return _REMINDER_RE.sub("", text)


def load_rail(name):
    """The user-written instruction sheet for a job (hooks/rails/<name>.md), or None.

    A "rail" is prose rules the scheduled job must follow, evidence requirements,
    pre-decided edge-case rulings, when to escalate instead of guessing.

    Division of labour:
    the job prompt owns the FORMAT, the rail owns the JUDGMENT
    (evidence contract, pre-arbitrated tension rulings, closed escalation
    predicates). FAIL-OPEN by design: a missing or unreadable rail returns None and
    the caller's one_shot() renders byte-identically to the pre-rail harness, a
    rail must never be able to silence a job."""
    hooks = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    # host-local rules (gitignored local/rails/) win; a shipped rails/ dir is the fallback
    for base in (os.path.join(hooks, "local", "rails"), os.path.join(hooks, "rails")):
        path = os.path.join(base, f"{name}.md")
        if os.path.exists(path):
            break
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read().strip()
        return text or None
    except OSError:
        return None


def one_shot(prompt, *, model="sonnet", attempts=3, deadline_s=130, attempt_cap_s=100,
             log=None, append_system=None):
    """One tool-less `claude -p` turn → stripped stdout, or None after the retry budget.

    Callers clip the result themselves. `log` is a callable(str) or None.
    `append_system` (optional; typically a load_rail() text) rides as
    --append-system-prompt; when omitted the argv stays byte-identical to the pre-rail CLI contract.
    NOTE: does NOT defuse, callers defuse() the quoted DATA they embed, not their own
    instructions."""
    _log = log or (lambda msg: None)
    # Host-level model policy: CLAUDE_LLM_MODEL in paths.env overrides every caller's
    # per-call default in one place (set e.g. "claude-opus-5" to upgrade all scheduled
    # jobs without touching each script; unset = the caller's choice stands).
    model = paths.get("CLAUDE_LLM_MODEL") or model
    env = dict(os.environ)
    # bin_path() prefixes the claude/brew dirs onto the caller's PATH (one
    # precedence order for every job). Scrub API-key vars so the call rides the
    # user's subscription login, never a stray key.
    env["PATH"] = paths.bin_path()
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("ANTHROPIC_AUTH_TOKEN", None)
    # --tools "" removes all tools: a stray tool call burns the single turn and exits 1
    # with "Reached max turns (1)", that once killed the morning assessment.
    cmd = ["claude", "-p", prompt, "--model", model, "--tools", "", "--max-turns", "1"]
    if append_system:
        cmd += ["--append-system-prompt", append_system]

    deadline = time.monotonic() + deadline_s
    backoff = 6
    for attempt in range(1, attempts + 1):
        remaining = deadline - time.monotonic()
        if remaining < 12:                       # not enough budget for a real attempt
            break
        cap = min(attempt_cap_s, remaining - 2)
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=cap,
                               env=env, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            # Short message ONLY: TimeoutExpired's repr
            # embeds the full cmd incl. the prompt, which must never reach a log.
            _log(f"llm.one_shot: attempt {attempt} timed out after {int(cap)}s "
                 "(won't retry: not transient)")
            break
        except Exception as e:
            # type+message, never repr, some subprocess exceptions carry cmd/prompt.
            _log(f"llm.one_shot: attempt {attempt} error: "
                 f"{type(e).__name__}: {str(e)[:200]}")
            break
        out = (r.stdout or "").strip()
        if r.returncode == 0 and out:
            return out
        # `claude -p` prints API errors to STDOUT and exits 1, log BOTH tails
        # (stderr alone is empty and would hide every real failure as `exit 1, err=''`).
        err = (r.stderr or "").strip()
        _log(f"llm.one_shot: attempt {attempt} exit {r.returncode}: "
             f"out={out[:200]!r} err={err[:200]!r}")
        if not _is_transient(out or err):
            break                                # genuine failure, a retry won't help
        if attempt < attempts and (deadline - time.monotonic()) > backoff + 12:
            time.sleep(backoff)
            backoff *= 2
    return None


@contextmanager
def llm_lock(wait_s=2700, log=None):
    """Best-effort cross-process serialisation of heavy `claude -p` jobs via an
    exclusive flock on ~/.claude/state/llm-job.lock (scheduled
    jobs that start close together contend on one subscription). Polls up to `wait_s` seconds; on timeout (or any lock-plumbing
    error) it logs a warning and YIELDS ANYWAY, serialisation is an optimisation, a
    scheduled job must never deadlock behind it. Yields True iff the lock is held."""
    _log = log or (lambda msg: None)
    home = os.environ.get("HOME") or os.path.expanduser("~")
    lock_path = os.path.join(home, ".claude", "state", "llm-job.lock")
    fd, got = None, False
    try:
        os.makedirs(os.path.dirname(lock_path), exist_ok=True)
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
        deadline = time.monotonic() + wait_s
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                got = True
                break
            except OSError:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    _log(f"llm_lock: {lock_path} still held after {wait_s}s: "
                         "proceeding unserialised (best-effort, never deadlock)")
                    break
                time.sleep(min(_LOCK_POLL_S, max(remaining, 0.1)))
    except Exception as e:
        _log(f"llm_lock: unavailable ({type(e).__name__}: {str(e)[:120]}): "
             "proceeding unserialised")
    try:
        yield got
    finally:
        if fd is not None:
            try:
                if got:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)
            except Exception:
                pass
