#!/usr/bin/env python3
"""Stop hook, mirror the just-finished Claude turn into the Obsidian vault.

Reads the Stop-hook JSON on stdin (needs `transcript_path` + `session_id`,
optionally `cwd`), renders the session transcript, every user prompt plus
the whole assistant turn: every text block AND every tool call interleaved
with its result, and writes Markdown so it reads as a conversation in
Obsidian.

Format (the "new session format", spec: a SAMPLE note kept vault-side, not in this repo;
templates + parse regexes are shared from lib/mirror.py, the single
source both review readers also import, so writer/reader drift is structural,
not accidental):
  - Turn skeleton: `---` / `### 🕐 YYYY-MM-DD HH:MM:SS` (transcript-local time)
    / `**🧑 You**` / `**🤖 Claude**`.
  - Human turns: plain prose, no blockquote bar. Huge pastes / injected skill
    payloads (> FOLD_THRESHOLD lines) fold into one collapsed [!quote]-
    callout, surfacing the `## User Request` tail as the visible prompt.
    Skill payloads are titled by provenance (`📄 Injected \\`/x\\` skill
    payload`), resolved from the Skill tool_use via sourceToolUseID or from a
    `<command-name>` entry in the same turn.
  - Consecutive user entries with no assistant reply between them are ONE
    turn (text+image prompts write a synthetic `[Image: source: …]` isMeta
    stub alongside the real entry; slash commands write a command entry plus
    an isMeta payload entry). Typed text comes first; an image stub stays as
    the image's placeholder; payloads fold.
  - A compaction summary (isCompactSummary) is written by Claude Code, not
    typed: it folds into a collapsed [!info]- callout after the prompt.
  - Claude prose: kept in full, inline (the spine of the recording).
  - Tool calls: ONE collapsed callout each (`[!note]- <icon> <Tool> · <detail>`).
    Body is output only, clipped to MAX_TOOL_OUTPUT/MAX_TOOL_OUTPUT_LINES with
    a `… (truncated)` marker. Tool INPUT is dropped entirely (the Bash
    `description` parameter serves as the title). Errors use [!failure]-.
  - Secrets: every prompt, reply, tool title and tool output passes through
    lib/mirror.redact before it is written, so a known key or token shape
    lands as `[redacted:<kind>]` (best effort, a fixed pattern list).
  - Opt-out: a session started in a folder with a `.organism-no-notes` file
    (or under one, short of $HOME), or inside a folder listed in paths.env's
    CLAUDE_NOTES_EXCLUDE, is not mirrored at all (lib/mirror.notes_excluded).

Layout (under <vault>):

  latest.md             dashboard, latest turn in full + index of ALL sessions
                        (pin this in Obsidian; never clobbered into uselessness)
  sessions/<id>.md      per-session full Q&A history, REGENERATED from the
                        transcript each turn (idempotent and self-healing, so
                        a backfill racing a live session can't duplicate turns)

Multiple concurrent CLI sessions (e.g. one in Terminal, one in Antigravity)
all run this same global hook. Per-session files are race-free (the Stop hook
is sequential within a session); the shared dashboard/index is guarded by a
file lock so simultaneous sessions can't corrupt it.

As a hook it always exits 0 and never blocks the turn.

Backfill mode (manual, not run by the hook machinery):

  obsidian-mirror.py --backfill          regenerate every sessions/<id>.md
                                         (plus latest.md) from its transcript,
                                         in the new format

Sessions whose transcript .jsonl no longer exists are left untouched.
"""
import sys, os, json, time, re, fcntl, glob, datetime
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))
import hostid  # canonical machine identity (host_label for the per-session marker)
import mirror  # SINGLE source of the on-disk session format + parse regexes
import paths   # machine-local paths.env (mirror-root resolution)

# One mirror-root resolution shared with every reader (paths.mirror_dir: env var wins,
# then paths.env, then <vault>/_claude-live). The old direct os.environ read left the
# wizard-written CLAUDE_OBSIDIAN_MIRROR_DIR paths.env key silently dead here.
# SESS == mirror.sessions_dir() by construction. VAULT is ""
# when no vault is configured: then nothing is written (main and backfill check it).
VAULT = paths.mirror_dir()
SESS = os.path.join(VAULT, "sessions") if VAULT else ""
INDEX = os.path.join(VAULT, ".sessions.json") if VAULT else ""
LOCK = os.path.join(VAULT, ".mirror.lock") if VAULT else ""
TRANSCRIPTS = os.path.expanduser("~/.claude/projects")
INCOGNITO = os.path.expanduser("~/.claude/state/incognito.json")  # toggled by incognito.py / `/incognito`
ERRLOG = os.path.expanduser("~/.claude/logs/obsidian-mirror-errors.log")
MAX_ROWS = 30  # cap dashboard table; index keeps everything
MAX_TOOL_OUTPUT = 3000      # chars of a tool's output shown before truncating
MAX_TOOL_OUTPUT_LINES = 60  # …or this many lines, whichever trips first
REDACT_SCAN = 12000         # chars of a tool's output redacted before the clip (bounded work)
FOLD_THRESHOLD = 40         # human-turn lines above this fold away

# Transcript `entrypoint` values that mark a MACHINE session, headless `claude -p`
# (sdk-cli) and SDK-driven service calls (sdk-ts), as opposed to the user's
# interactive TUI ("cli"). Measured across machines: every scheduled
# job/probe/product call carried sdk-cli or sdk-ts; every user-typed session
# carried cli. The session is still mirrored in full; the marker only routes it out
# of the reviews' behavioral corpus (lib/mirror.gather). A missing or unrecognized
# entrypoint fails OPEN to user, so a CLI rename can never silently drop the
# user's work from the reviews (the no-silent-failure rule).
MACHINE_ENTRYPOINTS = {"sdk-cli", "sdk-ts"}

# The user's known interactive entrypoints. Anything
# outside BOTH sets is a NEW session class: it still counts as user (the fail-OPEN
# rule above stands), but fail-open must not mean invisible, so it is tallied below
# and the reviews disclose it for classification instead of letting a new automation
# class silently pollute the behavioral corpus.
OPERATOR_ENTRYPOINTS = {"cli"}


def session_entrypoint(entries):
    """The transcript's first recorded `entrypoint`, or None (legacy transcripts)."""
    for e in entries:
        ep = e.get("entrypoint")
        if ep:
            return str(ep)
    return None


def start_dir(entries):
    """The directory the session was started in: the transcript's first recorded
    `cwd`. Not the hook's `cwd`, which follows the shell's `cd` (a session started in
    ~/Projects that cd'd into a subfolder would otherwise be filed under the subfolder)."""
    for e in entries:
        cwd = e.get("cwd")
        if cwd:
            return str(cwd)
    return None


def census_unknown_entrypoint(ep):
    """Tally an unrecognized transcript entrypoint (per mirrored turn) into
    state/entrypoint-census.json, read by the reviews' disclosure line. Routing is
    NEVER affected here (unknown stays user). Best-effort: this is
    the Stop-hook path, so it must never raise."""
    if not ep or ep in MACHINE_ENTRYPOINTS or ep in OPERATOR_ENTRYPOINTS:
        return
    try:
        f = os.path.join(os.environ.get("HOME") or os.path.expanduser("~"),
                         ".claude", "state", "entrypoint-census.json")
        os.makedirs(os.path.dirname(f), exist_ok=True)
        try:
            with open(f, encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            data = {}
        rec = data.get(ep) if isinstance(data.get(ep), dict) else {}
        rec["count"] = int(rec.get("count", 0)) + 1
        rec["last"] = datetime.date.today().isoformat()
        data[ep] = rec
        atomic_write(f, json.dumps(data, indent=1, sort_keys=True))
    except Exception:
        pass

TOOL_ICON = {
    "Bash": "🖥️", "Read": "📖", "Edit": "✏️", "Write": "📝", "Grep": "🔎",
    "Glob": "🗂️", "Task": "🤖", "Agent": "🤖", "WebFetch": "🌐",
    "WebSearch": "🌐", "TodoWrite": "✅", "NotebookEdit": "📓",
}

_REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.DOTALL)
_IMAGE_STUB = re.compile(r"\[Image: source: [^\]]*\]")
_CMD_NAME = re.compile(r"<command-name>(.*?)</command-name>", re.DOTALL)
# Header of the folded callout that holds a compaction summary. Claude Code writes
# that summary as a user entry (isCompactSummary), but nobody typed it.
COMPACT_CALLOUT = "> [!info]- 🗜️ Conversation compacted · summary written by Claude Code"
_CMD_MSG = re.compile(r"<command-message>.*?</command-message>\s*", re.DOTALL)
_CMD_ARGS = re.compile(r"<command-args>(.*?)</command-args>", re.DOTALL)
_CMD_STDOUT = re.compile(r"<local-command-stdout>(.*?)</local-command-stdout>", re.DOTALL)
_CAVEAT = re.compile(r"<local-command-caveat>.*?</local-command-caveat>", re.DOTALL)


def _breadcrumb(e):
    """One line (ts + repr) to the error log; never raises, never blocks the turn.
    The old top-level `except Exception: pass` left a persistently broken mirror
    (bad vault path, lock perms) with NO trace anywhere, silence must be
    distinguishable from failure."""
    try:
        os.makedirs(os.path.dirname(ERRLOG), exist_ok=True)
        with open(ERRLOG, "a", encoding="utf-8") as f:
            ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            f.write(f"[{ts}] {repr(e)[:300]}\n")
    except Exception:
        pass


def atomic_write(path, text):
    """tmp-in-same-dir + os.replace (the reconcile.save_state convention): the Stop
    hook is killed at settings.json's 10s timeout, so a mid-write kill must truncate
    the tmp, never the live index/dashboard/session file.
    The fixed .tmp name self-cleans, a later run overwrites any stray from a kill."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def is_user_prompt(content):
    """True for a real typed prompt (a turn boundary); False for a
    tool_result carrier that belongs to the current turn."""
    if isinstance(content, str):
        return True
    if isinstance(content, list):
        has_tool_result = any(
            isinstance(b, dict) and b.get("type") == "tool_result" for b in content
        )
        has_text = any(
            isinstance(b, dict) and b.get("type") == "text" for b in content
        )
        return has_text and not has_tool_result
    return False


def text_blocks(content):
    parts = []
    if isinstance(content, list):
        for b in content:
            if isinstance(b, dict) and b.get("type") == "text" and b.get("text"):
                parts.append(b["text"])
    elif isinstance(content, str):
        parts.append(content)
    return "\n\n".join(parts).strip()


def clean_prompt(text):
    """Drop injected system-reminder/caveat boilerplate so the prompt reads as typed."""
    return _CAVEAT.sub("", _REMINDER.sub("", text or "")).strip()


def redact(text):
    """`text` with every secret lib/mirror.redact knows replaced (its count dropped)."""
    return mirror.redact(text)[0]


def tidy_commands(text):
    """Render slash-command machinery as a readable line: the XML command
    block becomes `` `/name args` `` (in place) and `<local-command-stdout>`
    unwraps to its content. (Caveat boilerplate dies earlier, in clean_prompt.)"""
    if "<command-name>" not in text and "<local-command-" not in text:
        return text
    name = _CMD_NAME.search(text)
    if name:
        a = _CMD_ARGS.search(text)
        args = a.group(1).strip() if a else ""
        line = f"`{name.group(1).strip()}{' ' + args if args else ''}`"
        text = _CMD_NAME.sub(line, text, count=1)
        text = _CMD_MSG.sub("", text)
        text = _CMD_ARGS.sub("", text)
    text = _CMD_STDOUT.sub(lambda m: m.group(1).strip(), text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def blockquote(text):
    return "\n".join("> " + ln if ln else ">" for ln in (text or "").splitlines())


def cell(text, limit=90):
    """One-line, table-safe snippet."""
    s = " ".join((text or "").split())
    if len(s) > limit:
        s = s[: limit - 1] + "…"
    return s.replace("|", "\\|")


def stored_cell(prompt):
    """A prompt cell read back from the index, redacted again. A cell that was cut
    to fit (it ends in …) also loses its last, cut-off word: a row stored before
    redaction existed may end in the start of a token that no rule recognises once
    cut short."""
    s = redact(prompt or "")
    return re.sub(r"(?<!\S)(?!\[redacted:)\S+…$", "…", s)


def clip(text, max_chars, max_lines=None, cut=False):
    """Truncate long tool output, marking where it was cut. `cut` adds the marker even
    when nothing more is cut here, because the caller already dropped the rest."""
    text = text or ""
    if max_lines is not None:
        lines = text.splitlines()
        if len(lines) > max_lines:
            text = "\n".join(lines[:max_lines])
            cut = True
    if len(text) > max_chars:
        text = text[:max_chars]
        cut = True
    if cut:
        text = text.rstrip() + "\n… (truncated)"
    return text


def fence(body, lang=""):
    """Wrap body in a code fence long enough to survive any backtick run
    inside it (CommonMark: the fence must exceed the longest internal run)."""
    body = body or ""
    longest = max((len(m.group()) for m in re.finditer(r"`+", body)), default=0)
    bars = "`" * max(3, longest + 1)
    return f"{bars}{lang}\n{body}\n{bars}"


def result_text(content):
    """Flatten a tool_result's content (string or block list) to plain text.

    Unknown block types are dumped as JSON rather than dropped, so results
    like ToolSearch's `tool_reference` blocks still get recorded."""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        content = [content]
    parts = []
    if isinstance(content, list):
        for b in content:
            if isinstance(b, str):
                parts.append(b)
            elif isinstance(b, dict):
                t = b.get("type")
                if t == "text" or (t is None and "text" in b):
                    parts.append(b.get("text") or "")
                elif t == "image":
                    parts.append("_[image]_")
                else:
                    try:
                        parts.append(fence(json.dumps(b, ensure_ascii=False, indent=2)))
                    except Exception:
                        parts.append(str(b))
    return "\n".join(p for p in parts if p)


def tool_title(name, inp):
    """A one-line, human label for the tool-call callout header."""
    if isinstance(inp, dict):
        if name in ("Grep", "Glob") and inp.get("pattern"):
            # a search is about what it looks for; the path is only where
            return f"{name} · {cell(mirror.redact_head(str(inp['pattern']), 4096)[0], 70)}"
        for key in ("description", "command", "file_path", "path",
                    "pattern", "query", "url", "prompt"):
            if inp.get(key):
                # redacted before cell() cuts it, so the cut can't keep part of a secret
                return f"{name} · {cell(mirror.redact_head(str(inp[key]), 4096)[0], 70)}"
    return name


def local_ts(iso):
    """Transcript ISO timestamp -> local 'YYYY-MM-DD HH:MM:SS'."""
    if not iso:
        return ""
    try:
        dt = datetime.datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone()
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return iso


def render_tool(name, inp, out, is_error):
    """One collapsed callout; body is output-only (input dropped). The output is
    redacted before it is clipped, so the clip can't leave part of a secret behind."""
    icon = TOOL_ICON.get(name, "🔧")
    ctype = "failure" if is_error else "note"
    if (out or "").strip():
        text, cut = mirror.redact_head(out, REDACT_SCAN)
        body = fence(clip(text, MAX_TOOL_OUTPUT, MAX_TOOL_OUTPUT_LINES, cut))
    else:
        body = "_(no output)_"
    return blockquote(f"[!{ctype}]- {icon} {tool_title(name, inp)}\n\n{body}")


def _fold_title(lines, n, skill=None):
    if skill:
        return f"📄 Injected `/{skill}` skill payload · {n:,} lines"
    for ln in lines:
        m = re.search(r"Base directory for this skill:\s*(.+)", ln)
        if m:
            return (f"📄 Injected `/{m.group(1).rstrip('/').split('/')[-1]}` "
                    f"skill payload · {n:,} lines")
    for ln in lines:
        if ln.strip():
            return f"📄 Pasted context · {re.sub(r'^#+ *', '', ln.strip())[:60]} · {n:,} lines"
    return f"📄 Pasted context · {n:,} lines"


def render_human(prompt, skill=None):
    """Plain prose; fold huge pastes, surfacing a `## User Request` tail."""
    if not prompt:
        return "_(no prompt captured)_"
    lines = prompt.split("\n")
    if len(lines) <= FOLD_THRESHOLD:
        return prompt
    idx = next((i for i, ln in enumerate(lines)
                if ln.strip().lower() == "## user request"), None)
    request = lines[idx + 1:] if idx is not None else []
    scaffold = lines[:idx] if idx is not None else lines
    trim = lambda xs: "\n".join(xs).strip()
    out = []
    if trim(request):
        out.append(trim(request))
        out.append("")
    out.append(f"> [!quote]- {_fold_title(lines, len(lines), skill)}")
    out.append(">")
    out.append(blockquote(trim(scaffold)))
    return "\n".join(out)


def render_prompt_group(prompt_entries, skill_map):
    """One turn's user block from its (possibly several) prompt entries.

    Text+image prompts write a synthetic isMeta `[Image: source: …]` stub
    alongside the real entry: the typed text comes first, the stub stays as the
    image's placeholder (`_[image]_` when an image has no stub). isMeta
    skill/command payloads fold, titled by provenance. A compaction summary is
    not a prompt: it folds into an info callout after everything else and never
    becomes the gist. Returns (markdown, gist) where gist is a plain one-liner
    for the index table."""
    reals, metas, summaries = [], [], []
    images = 0
    for e in prompt_entries:
        content = (e.get("message") or {}).get("content")
        if not e.get("isMeta") and isinstance(content, list):
            images += sum(1 for b in content
                          if isinstance(b, dict) and b.get("type") == "image")
        t = redact(clean_prompt(text_blocks(content)))
        if not t:
            continue
        if e.get("isCompactSummary"):
            summaries.append(t)
        else:
            (metas if e.get("isMeta") else reals).append((e, t))

    # Skill name for payload folds: the entry's sourceToolUseID points at the
    # Skill tool_use; slash-command payloads ride a <command-name> entry.
    group_cmd = next(
        (m.group(1).strip().lstrip("/")
         for _, t in reals for m in [_CMD_NAME.search(t)] if m), None)

    parts = []
    for _, t in reals:
        tidied = tidy_commands(t)
        if tidied:
            parts.append(render_human(tidied))
    stubs = 0
    for e, t in metas:
        if _IMAGE_STUB.fullmatch(t.strip()):
            stubs += 1
            parts.append(t.strip())
            continue
        skill = skill_map.get(e.get("sourceToolUseID")) or group_cmd
        parts.append(render_human(t, skill=skill))
    parts.extend(["_[image]_"] * max(0, images - stubs))
    for t in summaries:
        n = len(t.split("\n"))
        parts.append(f"{COMPACT_CALLOUT} · {n:,} lines\n>\n{blockquote(t)}")
    if not parts:
        return "_(no prompt captured)_", ""
    if not reals and not metas:
        return "\n\n".join(parts), ""

    gist = tidy_commands(reals[0][1]) if reals else metas[0][1]
    return "\n\n".join(parts), gist


def load_entries(tpath):
    entries = []
    with open(tpath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except Exception:
                continue
    return entries


def build_skill_map(entries):
    """tool_use id -> skill name, for every Skill tool call in the session."""
    m = {}
    for e in entries:
        msg = e.get("message") or {}
        if (msg.get("role") or e.get("type")) != "assistant":
            continue
        c = msg.get("content")
        if not isinstance(c, list):
            continue
        for b in c:
            if (isinstance(b, dict) and b.get("type") == "tool_use"
                    and b.get("name") == "Skill"):
                skill = (b.get("input") or {}).get("skill")
                if skill:
                    m[b.get("id")] = str(skill)
    return m


def turn_starts(entries):
    """Indexes of real typed prompts, the turn boundaries."""
    starts = []
    for i, e in enumerate(entries):
        if e.get("isSidechain"):
            continue
        msg = e.get("message") or {}
        if (msg.get("role") or e.get("type")) == "user" and is_user_prompt(msg.get("content")):
            starts.append(i)
    return starts


def has_assistant_content(entries, a, b):
    for e in entries[a:b]:
        if e.get("isSidechain"):
            continue
        msg = e.get("message") or {}
        if (msg.get("role") or e.get("type")) != "assistant":
            continue
        c = msg.get("content")
        if isinstance(c, str) and c.strip():
            return True
        if isinstance(c, list) and any(
            isinstance(x, dict) and (
                x.get("type") == "tool_use"
                or (x.get("type") == "text" and (x.get("text") or "").strip())
            ) for x in c
        ):
            return True
    return False


def turn_complete(entries):
    """True when the transcript already ends with the assistant's closing
    text, i.e. the final message has been flushed.

    The Stop hook races the harness's write of the final assistant message
    into the transcript JSONL: tool calls are flushed mid-turn, but the
    trailing text (or a whole text-only reply) can land a beat AFTER the hook
    fires. A finished turn always ends with an assistant text block; while
    the flush is in flight the file instead ends with a tool_result carrier,
    a tool_use, or the prompt itself."""
    for e in reversed(entries):
        if e.get("isSidechain"):
            continue
        msg = e.get("message") or {}
        role = msg.get("role") or e.get("type")
        if role == "assistant":
            c = msg.get("content")
            if isinstance(c, str):
                return bool(c.strip())
            if isinstance(c, list):
                return any(isinstance(b, dict) and b.get("type") == "text"
                           and (b.get("text") or "").strip() for b in c)
            return False
        if role == "user":
            return False
        # other entry kinds (summary, system, …), keep scanning backwards
    return True  # empty/odd transcript: don't spin, just render what's there


def group_turns(entries):
    """Group turn boundaries so consecutive prompts with no assistant reply
    between them render as ONE turn (image stubs, command+payload pairs,
    queued messages). Each group: {'prompts': [idx, …], 'end': idx}."""
    starts = turn_starts(entries)
    groups = []
    for k, s in enumerate(starts):
        end = starts[k + 1] if k + 1 < len(starts) else len(entries)
        replied = has_assistant_content(entries, s, end)
        if groups and not groups[-1]["replied"]:
            groups[-1]["prompts"].append(s)
            groups[-1]["end"] = end
            groups[-1]["replied"] = replied
        else:
            groups.append({"prompts": [s], "end": end, "replied": replied})
    return groups


def render_turn(turn):
    """Assistant prose + tool callouts for one turn's entries, in order."""
    # Pass 1: index every tool result in this turn by its tool_use_id
    # (parallel calls return several results, possibly across messages).
    results = {}
    for e in turn:
        if e.get("isSidechain"):
            continue
        msg = e.get("message") or {}
        if (msg.get("role") or e.get("type")) != "user":
            continue
        content = msg.get("content")
        if isinstance(content, list):
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    results[b.get("tool_use_id")] = (
                        result_text(b.get("content")), bool(b.get("is_error"))
                    )

    # Pass 2: render assistant prose + tool calls forward, in order.
    out = []
    for e in turn:
        if e.get("isSidechain"):
            continue
        msg = e.get("message") or {}
        if (msg.get("role") or e.get("type")) != "assistant":
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            t = text_blocks(content)
            if t:
                out.append(redact(t))
            continue
        for b in content:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "text" and b.get("text"):
                out.append(redact(b["text"].strip()))
            elif b.get("type") == "tool_use":
                rtext, is_err = results.get(b.get("id"), ("", False))
                out.append(render_tool(b.get("name") or "tool",
                                        b.get("input"), rtext, is_err))

    return "\n\n".join(p for p in out if p).strip()


def turn_model(turn):
    """The model that produced this turn's reply, the last real assistant
    message's model in the group, so a mid-turn /model switch tags with the
    model that wrote the closing text. None when the turn has no real model
    (synthetic-only, or a legacy transcript predating the model field); turn_block
    then renders the pre-tag reply line unchanged."""
    model = None
    for e in turn:
        if e.get("isSidechain"):
            continue
        msg = e.get("message") or {}
        if (msg.get("role") or e.get("type")) != "assistant":
            continue
        m = msg.get("model") or e.get("model")
        if m and m != "<synthetic>":
            model = m
    return model


# Turn skeleton / H1 / machine-line templates live in lib/mirror.py: the writer renders through the same module the review readers
# parse with, so a format tweak can no longer silently zero the reviews.
turn_block = mirror.turn_block


def _load_incognito():
    """The incognito ledger, or None when it exists but can't be trusted.

    Fail-CLOSED: a missing file just means incognito
    was never used ({}, mirror on); an EXISTING but unreadable/corrupt file
    returns None, and callers must skip mirror writes for the run, rendering with
    an empty ledger would re-expose sealed/purged turns into the synced vault."""
    try:
        with open(INCOGNITO, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
        raise ValueError(f"incognito.json: expected object, got {type(data).__name__}")
    except FileNotFoundError:
        return {}
    except Exception as e:
        _breadcrumb(e)
        return None


def _incognito_checker(rec):
    """Return is_incog(ts_iso)->bool for ONE session's incognito record `rec`
    ({active, since_ms, windows}). A turn is incognito if its timestamp falls
    inside a sealed window [s,e], or after `since_ms` while active, so incognito
    turns stay excluded even after the session regenerates from the full
    transcript. Per-session: a record only ever gates its OWN session's turns,
    so one session's incognito never touches another's. Cheap no-op when empty."""
    rec = rec or {}
    wins = rec.get("windows") or []
    active = bool(rec.get("active"))
    since = rec.get("since_ms")
    if not wins and not (active and since is not None):
        return lambda ts: False

    def to_ms(ts):
        try:
            return datetime.datetime.fromisoformat((ts or "").replace("Z", "+00:00")).timestamp() * 1000
        except Exception:
            return None

    def is_incog(ts):
        m = to_ms(ts)
        if m is None:
            return False
        for w in wins:
            if len(w) == 2 and w[0] <= m <= w[1]:
                return True
        return bool(active and since is not None and m >= since)

    return is_incog


def render_session_file(entries, short, project, incog_rec=None, machine=None,
                        headless=None, project_dir=None):
    """Full sessions/<id>.md body. Returns (markdown, n_turns, last_turn)
    where last_turn = (ts, human_md, reply, gist, model) or None.

    `incog_rec` is THIS session's incognito record; its turns are dropped here,
    so they never appear in the mirror. `machine` is the recorded host label for
    this session (stamped under the H1); None on legacy sessions that predate it.
    `headless` is the transcript entrypoint when this was a machine session
    (marker under the host label); None renders byte-identically to the
    pre-marker format. `project_dir` is the directory the session started in
    (mirror.project_dir_line, the SessionStart pointer's key); None renders
    byte-identically to the pre-marker format."""
    skill_map = build_skill_map(entries)
    is_incog = _incognito_checker(incog_rec)
    out = [mirror.session_h1(short, project), ""]
    if machine:
        out += [mirror.machine_line(machine), ""]
    if headless:
        out += [mirror.headless_line(headless), ""]
    pd = mirror.project_dir_line(project_dir) if project_dir else ""
    if pd:
        out += [pd, ""]
    n, last = 0, None
    for g in group_turns(entries):
        start = g["prompts"][0]
        if is_incog(entries[start].get("timestamp")):
            continue
        reply = render_turn(entries[start:g["end"]])
        if not reply:
            continue
        model = turn_model(entries[start:g["end"]])
        human, gist = render_prompt_group(
            [entries[i] for i in g["prompts"]], skill_map)
        ts = local_ts(entries[start].get("timestamp"))
        out.append("---")
        out.append("")
        out.append(turn_block(ts, human, reply, model))
        out.append("")
        n += 1
        last = (ts, human, reply, gist, model)
    return "\n".join(out), n, last


def render_dashboard(index, current):
    """current = the entry for the turn that just finished (shown in full on top).
    The featured turn and every prompt cell are redacted here too, whoever rendered
    them: rows stored before redaction existed would otherwise show their old text."""
    out = [
        f"**Latest reply** · {current['project']} · session `{current['short']}`",
        "",
        turn_block(current["ts"], redact(current["human"]),
                   redact(current["reply"]) or "_(empty)_", current.get("model")),
        "",
        "---",
        "",
    ]

    rows = sorted(index.values(), key=lambda v: v.get("epoch", 0), reverse=True)
    out.append(f"## 🗂 All sessions ({len(rows)})")
    out.append("")
    out.append("| Updated | Session | Project | Last prompt |")
    out.append("|---|---|---|---|")
    for r in rows[:MAX_ROWS]:
        s = r["short"]
        link = f"[{s}](sessions/{r['id']}.md)"
        # `prompt` is cell()-escaped ONCE at store time (main / _parse_session_md);
        # cell()ing it again turned a stored '\|' into '\\|', a literal backslash
        # plus a LIVE column separator that misaligned the row.
        out.append(f"| {r['ts']} | {link} | {cell(r['project'], 24)} | {stored_cell(r['prompt'])} |")
    if len(rows) > MAX_ROWS:
        out.append("")
        out.append(f"_…and {len(rows) - MAX_ROWS} older session(s)._")
    out.append("")
    return "\n".join(out)


def load_index():
    if os.path.exists(INDEX):
        try:
            with open(INDEX, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _epoch_from_ts(ts):
    """Local 'YYYY-MM-DD HH:MM:SS' -> epoch seconds (for the dashboard sort)."""
    try:
        return datetime.datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").timestamp()
    except Exception:
        return 0.0


# Parsers for a rendered sessions/<id>.md (the only cross-machine-synced
# artifact), shared from lib/mirror.py so they can never
# drift from the templates render_session_file writes with. These are the
# writer's TOLERANT trio, deliberately distinct from the reviews' strict
# TURN_RE/HEADER_RE (divergence documented in lib/mirror.py).
_MD_H1, _MD_MACHINE, _MD_TS = mirror.MD_H1, mirror.MD_MACHINE, mirror.MD_TS
# Session files are named by UUID session_id; this skips stray non-session
# markdown in sessions/ (e.g. HANDOFF.md) so the merge never re-reads them.
_SESSION_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def _parse_session_md(path):
    """Best-effort dashboard row reconstructed from a rendered sessions/<id>.md.

    The per-session markdown is the ONLY artifact synced across machines (raw
    transcripts and .sessions.json are per-machine), so a machine that never ran
    a session can still recover its dashboard row from the file. Returns a row
    dict (id/short/project/ts/epoch/prompt/machine) or None if the file is
    unreadable or has no rendered turn yet (empty/partial sync)."""
    sid = os.path.basename(path)[:-3]
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except Exception:
        return None
    stamps = _MD_TS.findall(text)
    if not stamps:
        return None  # nothing rendered, don't surface an empty row
    ts = stamps[-1].strip()
    m = _MD_H1.search(text)
    project = m.group(1).strip() if m else "—"
    m = _MD_MACHINE.search(text)
    machine = m.group(1).strip() if m else None
    # Last turn's gist: first real line of the final human block. A request tail
    # comes before its [!quote]- fold; a fold with no tail gives its first line,
    # as the native row (the whole prompt) does.
    prompt = ""
    tail = text.rsplit("**🧑 You**", 1)
    if len(tail) == 2:
        human = tail[1].split("**🤖 Claude**", 1)[0]
        in_fold = False
        for ln in human.splitlines():
            if ln.startswith(COMPACT_CALLOUT):
                break  # the compaction fold comes last and is not a prompt
            if ln.startswith("> [!quote]- "):
                in_fold = True
                continue
            s = ln.strip()
            if in_fold:
                s = s[1:].strip() if s.startswith(">") else s
            if s:
                prompt = s
                break
    return {
        "id": sid, "short": sid[:8], "project": project, "ts": ts,
        "epoch": _epoch_from_ts(ts), "prompt": cell(redact(prompt)), "machine": machine,
    }


def merge_session_files(index):
    """Fold OTHER machines' sessions into the dashboard index. Mutates+returns it.

    The "All sessions" table is rebuilt from .sessions.json, which is per-machine
    and NOT synced; the per-session sessions/<id>.md files ARE synced. Without
    this merge each machine rebuilds latest.md from only its own index and
    clobbers the other machine's rows on every turn. Here
    we pull every synced markdown not already covered by a native (locally-run)
    index entry into the index, so each rebuild reconstructs the full
    cross-machine list and neither machine can drop the other's rows.

    Reads are bounded so this stays cheap on the Stop-hook path: a session this
    machine ran has a native index entry and is never re-read; a merged entry is
    re-parsed only when its file's mtime changes (Obsidian Sync touches mtime on
    download), so a steady-state turn reads zero markdown."""
    try:
        paths = glob.glob(os.path.join(SESS, "*.md"))
    except Exception:
        return index
    # A row goes when its note goes: a synced row when the machine that ran it purges
    # or deletes the note, and this machine's own row when its note is deleted here
    # (how an opted-out project's old notes are cleaned up). Without this the row, with
    # its last prompt, was written back into latest.md on every turn. An empty folder
    # (the vault not mounted yet) drops only synced rows, so a passing outage can't
    # empty this machine's table.
    present = {os.path.basename(p)[:-3] for p in paths}
    for sid in [k for k, v in index.items() if k not in present
                and (v.get("_synced_from_md") or present)]:
        del index[sid]
    for path in paths:
        sid = os.path.basename(path)[:-3]
        if not _SESSION_ID.match(sid):
            continue  # stray non-session markdown (e.g. HANDOFF.md)
        existing = index.get(sid)
        if existing and not existing.get("_synced_from_md"):
            continue  # native, locally-run entry, authoritative, never re-read
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        if existing and existing.get("_mtime") == mtime:
            continue  # already parsed this exact version of the file
        row = _parse_session_md(path)
        if row:
            row["_mtime"] = mtime
            row["_synced_from_md"] = True
            index[sid] = row
    return index


def _turns_in_note(path):
    """How many turns the note at `path` holds (0 when it is missing or unreadable)."""
    try:
        with open(path, encoding="utf-8") as f:
            return len(mirror.TURN_RE.findall(f.read()))
    except Exception:
        return 0


def main():
    # Read stdin as UTF-8 explicitly (not the locale default): in a non-UTF-8
    # locale json.load(sys.stdin) would mangle non-ASCII payload, e.g. a
    # non-Latin project-folder name in `cwd` (→ the dashboard/index `project`).
    data = json.loads(sys.stdin.buffer.read().decode("utf-8", "replace"))
    if not VAULT:
        return  # no vault configured: session notes are off
    tpath = data.get("transcript_path")
    session = str(data.get("session_id") or "session")
    cwd = data.get("cwd") or ""
    if not tpath or not os.path.exists(tpath):
        return

    # Per-session incognito: if THIS session is active, skip its mirror entirely
    # (other sessions are unaffected). Durability, keeping this session's sealed
    # windows out of later regenerations, is handled in render_session_file.
    incog = _load_incognito()
    if incog is None:
        return  # corrupt ledger (breadcrumbed): fail CLOSED, every session sealed this run
    rec = (incog.get("sessions") or {}).get(session, {})
    if rec.get("active"):
        return

    short = session[:8]
    project = os.path.basename(cwd.rstrip("/")) or cwd or "—"
    machine = hostid.host_label()  # record THIS machine, for the per-session marker

    entries = load_entries(tpath)
    # A project that opted out (a .organism-no-notes file, or paths.env's
    # CLAUDE_NOTES_EXCLUDE) gets no note and no table row, judged on the directory the
    # session started in.
    if mirror.notes_excluded(start_dir(entries) or cwd):
        return
    # Bounded wait for the final-message flush (see turn_complete docstring):
    # up to 5 × 0.4 s, then proceed with whatever is there. The session file
    # self-heals next turn regardless; the SessionEnd re-mirror closes the
    # last-turn-of-the-session hole.
    for _ in range(5):
        if turn_complete(entries):
            break
        time.sleep(0.4)
        entries = load_entries(tpath)
    ep = session_entrypoint(entries)
    census_unknown_entrypoint(ep)   # visibility only, never changes routing
    body, n, last = render_session_file(
        entries, short, project, rec, machine=machine,
        headless=ep if ep in MACHINE_ENTRYPOINTS else None,
        project_dir=start_dir(entries))
    if not last:
        return
    ts, human, reply, gist, model = last
    if not ts:
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    os.makedirs(SESS, exist_ok=True)
    note = os.path.join(SESS, f"{session}.md")
    current = {
        "id": session, "short": short, "project": project, "cwd": cwd,
        "ts": ts, "epoch": time.time(), "prompt": cell(gist), "machine": machine,
    }
    # The lock covers the session note too: the Stop and SessionEnd passes of one
    # session can run at once and would otherwise race on the same .tmp file.
    with open(LOCK, "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            # An /incognito purge or on that landed after the render must win: the
            # body above was rendered with the older record.
            fresh = _load_incognito()
            if fresh is None or (fresh.get("sessions") or {}).get(session, {}) != rec:
                return
            # A Stop run that rendered before the final flush must not overwrite the
            # fuller note the SessionEnd pass wrote meanwhile: keep the note with more
            # turns. Both sides are counted the same way, so a reply that quotes a
            # turn header counts on both and never freezes the note.
            if _turns_in_note(note) > len(mirror.TURN_RE.findall(body)):
                return
            atomic_write(note, body)

            index = load_index()
            index[session] = current
            merge_session_files(index)  # fold in other machines' synced sessions
            atomic_write(INDEX, json.dumps(index))
            atomic_write(os.path.join(VAULT, "latest.md"),
                         render_dashboard(index, {**current, "human": human, "reply": reply,
                                                  "model": model}))
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


def backfill():
    """Regenerate every existing sessions/<id>.md (and latest.md) from
    its transcript in the new format. Manual CLI mode; prints a report."""
    if not VAULT:
        print("no vault configured (CLAUDE_VAULT_ROOT / CLAUDE_OBSIDIAN_MIRROR_DIR): "
              "session notes are off; nothing to backfill")
        return
    os.makedirs(SESS, exist_ok=True)
    index = load_index()
    incog = _load_incognito()
    if incog is None:
        # A backfill with an unreadable ledger would
        # regenerate EVERY session with sealed/purged turns re-exposed, refuse.
        print("incognito.json exists but is unreadable/corrupt: refusing to backfill "
              "(fail closed); see ~/.claude/logs/obsidian-mirror-errors.log")
        return
    incog_sessions = incog.get("sessions") or {}
    ids = sorted(f[:-3] for f in os.listdir(SESS) if f.endswith(".md"))
    done, empty, missing, excluded = [], [], [], []
    last_by_id = {}

    for sid in ids:
        matches = glob.glob(os.path.join(TRANSCRIPTS, "*", f"{sid}.jsonl"))
        if not matches:
            missing.append(sid)
            continue
        entries = load_entries(matches[0])
        meta = index.get(sid) or {}
        cwd = meta.get("cwd") or next(
            (e.get("cwd") for e in entries if e.get("cwd")), "")
        project = (meta.get("project")
                   or os.path.basename((cwd or "").rstrip("/")) or "—")
        if mirror.notes_excluded(start_dir(entries) or cwd):
            excluded.append(sid)
            continue
        short = sid[:8]
        ep = session_entrypoint(entries)
        body, n, last = render_session_file(
            entries, short, project, incog_sessions.get(sid, {}),
            machine=meta.get("machine"),
            headless=ep if ep in MACHINE_ENTRYPOINTS else None,
            project_dir=start_dir(entries))
        if not n:
            empty.append(sid)
            continue
        atomic_write(os.path.join(SESS, f"{sid}.md"), body)
        if last:
            ts, human, reply, gist, model = last
            last_by_id[sid] = (project, ts, human, reply, model)
        done.append((sid, n))
        print(f"  {sid[:8]}  {n:3d} turns  ({project})")

    # Regenerate the dashboard from the newest indexed session we rebuilt.
    newest = max(
        (v for v in index.values() if v.get("id") in last_by_id),
        key=lambda v: v.get("epoch", 0), default=None,
    )
    if newest:
        project, ts, human, reply, model = last_by_id[newest["id"]]
        merge_session_files(index)  # include other machines' synced sessions
        with open(LOCK, "w") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            try:
                atomic_write(os.path.join(VAULT, "latest.md"), render_dashboard(index, {
                    **newest, "ts": ts, "human": human, "reply": reply, "model": model,
                }))
            finally:
                fcntl.flock(lf, fcntl.LOCK_UN)

    print(f"\nbackfilled {len(done)} session(s)"
          + (f", {len(empty)} with no renderable turns (left untouched)" if empty else "")
          + (f", {len(missing)} transcript(s) missing (left untouched)" if missing else "")
          + (f", {len(excluded)} in an opted-out project (left untouched)" if excluded else ""))
    for sid in missing:
        print(f"  missing transcript: {sid}")
    for sid in empty:
        print(f"  no turns: {sid}")


if __name__ == "__main__":
    if "--backfill" in sys.argv[1:]:
        backfill()  # manual mode: let errors surface
        sys.exit(0)
    try:
        main()
    except Exception as e:
        _breadcrumb(e)  # never blocks the turn, but a broken mirror leaves a trace now
    sys.exit(0)
