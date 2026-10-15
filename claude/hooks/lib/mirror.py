#!/usr/bin/env python3
"""hooks/lib/mirror.py: SINGLE source of the Obsidian session-mirror on-disk format.

The writer (obsidian-mirror.py) and any reader (scheduled reviews that read the
notes back) share the format through this file, so a writer-side tweak cannot
silently turn every reader's input into a confident, false "no activity"
(gather()==[] is a valid no-activity day, so a format drift would not raise). Writers must render through the
template functions here; readers must parse with the regexes here; running this module
(`python3 mirror.py`) round-trips a synthetic session through both as a self-check.
It also holds what must never reach the notes: redact() (secrets) and notes_excluded()
(projects that opted out).

On-disk format, sessions/<id>.md (DO NOT change one side without the other):

    # Claude session `<short>`, <project>        H1 (readers tolerate a missing project)
    🖥️ *<machine>*                                 optional host label under the H1
    ⚙️ *headless · <entrypoint>*                   optional machine-session marker
    <!-- project-dir: <path> -->                    optional start-directory marker (hidden in
                                                   Obsidian's reading view; read by sessions_since)
    ---
    ### 🕐 YYYY-MM-DD HH:MM:SS                     turn heading (local time)
    **🧑 You**
    <rendered human block>
    **🤖 Claude**
    <rendered reply>
"""
import datetime
import glob
import json
import os
import re
import shlex
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths

# ---- reader regexes --------------------------------------------------------------
TURN_RE = re.compile(r"^### 🕐 (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s*$", re.M)
HEADER_RE = re.compile(r"^# Claude session `[^`]+`\s*(?:—\s*(.+))?$", re.M)
USER_RE = re.compile(r"\*\*🧑 You\*\*(.*?)(?:\*\*🤖 Claude\*\*|$)", re.S)

# ---- the writer's own tolerant parsers (verbatim obsidian-mirror._MD_*) -------------
# Kept DISTINCT from the reader trio on purpose: their tolerances already diverged
# (MD_H1 requires the ",  project" suffix that HEADER_RE treats as optional; MD_TS
# accepts any stamp where TURN_RE demands the exact strftime shape) and unifying them
# would silently change what each consumer accepts from already-synced files.
MD_H1 = re.compile(r"^# Claude session `[^`]*` — (.+)$", re.M)
MD_MACHINE = re.compile(r"^🖥️ \*(.+?)\*\s*$", re.M)
MD_TS = re.compile(r"^### 🕐 (.+?)\s*$", re.M)
# Machine-session marker: written by headless_line(), honored by gather()
# ONLY in a file's header region (above the first turn): a mirrored prompt that merely
# QUOTES the marker must never get a user session dropped from the reviews.
MD_HEADLESS = re.compile(r"^⚙️ \*headless(?: · [^*\n]+)?\*\s*$", re.M)
# Start-directory marker: honored ONLY in the header region, like
# MD_HEADLESS: a prompt quoting the line must not move a session to another project.
MD_PROJECT_DIR = re.compile(r"^<!-- project-dir: (.+?) -->\s*$", re.M)

# Prompt-cleaning defaults.
MAX_PROMPTS = 50           # digest-layer: most-recent prompts listed verbatim
MAX_PROMPT_CHARS = 240     # per-prompt clip
MAX_DIGEST_CHARS = 12000   # digest-layer: total digest cap

TS_FMT = "%Y-%m-%d %H:%M:%S"   # what TURN_RE's group parses back with strptime


# ---- writer templates (verbatim from obsidian-mirror.py) ----------------------------
def short_model(model):
    """Display form of a transcript model id for the reply tag: strip the vendor
    prefix (`claude-opus-4-8` → `opus-4-8`) and drop synthetic/empty. Returns ''
    when there is nothing to show, so turn_block renders byte-identically to the
    pre-tag format for legacy/synthetic turns."""
    if not model or model == "<synthetic>":
        return ""
    return model[len("claude-"):] if model.startswith("claude-") else model


def turn_block(ts, human, reply, model=None):
    """The turn skeleton (without the leading `---` separator).
    `human` is the already-rendered user block. `model` (a transcript model id)
    badges the reply line as `**🤖 Claude** · \\`opus-4-8\\``, appended AFTER the
    literal `**🤖 Claude**`, so the reader trio (USER_RE terminates on that exact
    substring) and the writer's _parse_session_md (splits on it) are unaffected.
    model=None / '<synthetic>' renders exactly the pre-tag line (byte-identical)."""
    tag = short_model(model)
    claude = "**🤖 Claude**" + (f" · `{tag}`" if tag else "")
    return (f"### 🕐 {ts}\n\n"
            f"**🧑 You**\n\n{human}\n\n"
            f"{claude}\n\n{reply}")


def session_h1(short, project):
    """The sessions/<id>.md H1 (matched by HEADER_RE / MD_H1)."""
    return f"# Claude session `{short}` — {project}"


def machine_line(machine):
    """The optional host-label line under the H1 (matched by MD_MACHINE)."""
    return f"🖥️ *{machine}*"


def headless_line(entrypoint=None):
    """The optional machine-session marker under the H1 (matched by MD_HEADLESS):
    stamped when the transcript's `entrypoint` says this session was a
    headless `claude -p`/SDK run (a scheduled job, a product's server-side call, a
    probe) rather than the user's interactive TUI. The mirror still records the
    session in full; the marker only routes it out of the BEHAVIORAL corpus the
    reviews read (instruments measure the user, not the machine's
    automation)."""
    return f"⚙️ *headless{f' · {entrypoint}' if entrypoint else ''}*"


def project_dir_line(path):
    """The optional start-directory marker under the H1 (matched by MD_PROJECT_DIR):
    the directory the session was STARTED in (its transcript's first `cwd`, later
    entries follow the shell's `cd`), i.e. the key inject-handoff.sh resolves a brief
    from. The H1 carries only the basename, so without this a machine that never ran
    the session can't tell which project a synced note belongs to. An HTML comment, so
    Obsidian's reading view hides it. '' for a path that can't sit inside a comment."""
    if not path or "\n" in path or "-->" in path:
        return ""
    return f"<!-- project-dir: {path} -->"


def sessions_dir():
    """The synced per-session markdown directory, via the ONE mirror-root resolution
    (paths.mirror_dir, env var wins, then paths.env, then <vault>/_claude-live).
    "" when the notes are off (no vault configured): callers must check, because a
    relative "sessions" would resolve against whatever the current directory is."""
    root = paths.mirror_dir()
    return os.path.join(root, "sessions") if root else ""


# ---- per-project opt-out -------------------------------------------------------------
NO_NOTES_FILE = ".organism-no-notes"


def notes_excluded(start, home=None):
    """True when a session started in `start` must not be copied at all (no note, no
    table row): `start` or a folder above it, short of $HOME, holds a .organism-no-notes
    file, or `start` lies inside a folder listed in the paths.env key
    CLAUDE_NOTES_EXCLUDE (space-separated absolute paths; quote one that holds a space).
    Both the path as given and its resolved form are checked, so a symlinked project
    folder can't slip past either rule."""
    if not start:
        return False
    home = home or os.environ.get("HOME") or os.path.expanduser("~")
    homes = {os.path.abspath(home), os.path.realpath(home)}
    starts = {os.path.abspath(start), os.path.realpath(start)}
    for d in starts:
        while d not in homes and d != os.path.dirname(d):
            if os.path.exists(os.path.join(d, NO_NOTES_FILE)):
                return True
            d = os.path.dirname(d)
    raw = paths.get("CLAUDE_NOTES_EXCLUDE") or ""
    try:
        listed = shlex.split(raw)
    except ValueError:
        listed = raw.split()
    for p in listed:
        p = os.path.expanduser(os.path.expandvars(p))
        if not os.path.isabs(p):
            continue
        for root in {os.path.abspath(p), os.path.realpath(p)}:
            prefix = root.rstrip(os.sep) + os.sep
            if any(d == root or d.startswith(prefix) for d in starts):
                return True
    return False


# ---- redaction -----------------------------------------------------------------------
# Every string the writer emits passes through redact() first: prompts, Claude's prose,
# tool titles and output, latest.md's featured turn and its table cells. Each rule
# replaces only the secret itself with [redacted:<kind>], so the text around it, the
# callout prefixes and the code fences stay as they were; only a private key block,
# which spans lines, collapses to one line. A replaced value is never matched again,
# so redact() is idempotent. This is a best-effort pattern list: a secret in a shape
# it doesn't know gets through.
_B64 = r"[A-Za-z0-9+/=]"
_PEM_TAG = r"[A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----"
_PRIVATE_KEY = re.compile(
    r"-----BEGIN" + _PEM_TAG
    # the whole block, when its END line is there ...
    + r"(?:[A-Za-z0-9+/=\s:,.\\>-]*?-----END" + _PEM_TAG
    # ... else every following line that is base64 or a header (a cut-off block)
    + r"|(?:(?:\r?\n[ \t>]*)+(?:" + _B64 + r"+|[A-Za-z][A-Za-z-]*:[^\n]*)"
    + r"(?=[ \t]*(?:\r?\n|\Z)))*)")

# Key names that mark a value as secret. TOKENS / TOKENIZER are counts and tools.
_SECRET_WORD = re.compile(
    r"secret|token(?!iz|s(?![a-z0-9]))|passw(?:or)?d|api[_-]?key|private[_-]?key"
    r"|access[_-]?key", re.I)
# ...unless the name says the value is a location or a label, not the secret itself.
_NOT_SECRET_NAME = re.compile(r"(?:file|path|dir|url|uri|endpoint|name|type)$", re.I)
_NAME_BODY = r"(?P<name>[A-Za-z0-9_.-]{1,64})"
_NAME = r"(?<![A-Za-z0-9_.-])" + _NAME_BODY
_PLACEHOLDER_WORDS = {"changeme", "placeholder", "example", "your", "here", "dummy",
                      "fake", "sample", "redacted", "replace", "todo", "fixme", "xxx",
                      "none", "null", "undefined"}


def _placeholder(v):
    """True for a value that stands in for a secret: $VAR, ${VAR}, {var}, <...>,
    %s, xxxx, ****, or one built from words like your / here / changeme."""
    s = (v or "").strip()
    if not s or s.startswith(("$", "<", "{", "%", "[redacted:")):
        return True
    if re.fullmatch(r"[xX*•.\-_ ]+", s):
        return True
    low = s.lower()
    if low.startswith(("changeme", "placeholder")):
        return True
    words = [w for w in re.split(r"[^a-z0-9]+", re.sub(r"([a-z])([A-Z])", r"\1 \2", s).lower()) if w]
    if words and words[0] in _PLACEHOLDER_WORDS | {"insert", "enter"}:
        return True
    # a word like none or sample inside a value with digits is part of a real secret
    return not re.search(r"[0-9]", s) and any(w in _PLACEHOLDER_WORDS for w in words)


def _keep_value(m, v):
    """Too short to be a secret, or a placeholder."""
    return len(v) < 8 or _placeholder(v)


def _keep_url_password(m, v):
    """A URL password of any length is secret, unless it is a placeholder."""
    return _placeholder(v)


def _keep_user_password(m, v):
    """A number (`docker run -u 1000:1000`) or a placeholder is not a password."""
    return v.isdigit() or _placeholder(v)


def _keep_quoted(m, v):
    """A name that doesn't say secret (or says where the secret is kept), or a value
    that is too short or a placeholder."""
    name = m.group("name")
    return (not _SECRET_WORD.search(name) or bool(_NOT_SECRET_NAME.search(name))
            or _keep_value(m, v))


def _keep_bare(m, v):
    """An unquoted value is often code, not a secret: a variable (api_key,
    self.token, settings.SECRET_KEY), a number (max_token_count=100000000), or a
    value that runs into a call or an index (get_token(), os.environ[...])."""
    if _keep_quoted(m, v) or v.isdigit():
        return True
    if m.string[m.end():m.end() + 1] in ("(", "[", "{"):
        return True
    # a whole-line value that is a call or an index: get_token(), os.environ["X"]
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*[(\[].*[)\]][;,]?", v):
        return True
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*", v)
                and _SECRET_WORD.search(v))


def _keep_flag(m, v):
    """A flag value that is the next flag, a number or a plain lowercase word is a
    setting or usage text (`--token-count 4096`, `the --token flag`), not a secret."""
    return (_keep_quoted(m, v) or v.startswith("-") or v.isdigit()
            or bool(re.fullmatch(r"[a-z]+", v)))


def _lit(lit, boundary):
    """`lit` where the char before it is not in `boundary`. The literal comes first so
    the regex engine can jump between occurrences instead of trying every position."""
    return re.escape(lit) + "(?<!" + boundary + re.escape(lit) + ")"


def _keep_telegram(m, v):
    """The digits run on from a longer word or number: not a bot token. The Bot API
    writes the token right after "bot" (https://api.telegram.org/bot<token>/getMe)."""
    i = m.start()
    if i >= 3 and m.string[i - 3:i].lower() == "bot" and (
            i == 3 or not re.match(r"[A-Za-z0-9_]", m.string[i - 4])):
        return False
    return i > 0 and re.match(r"[A-Za-z0-9_:-]", m.string[i - 1]) is not None


# (kind, triggers, regex, the group holding the secret (0 = the whole match), keep-check).
# A rule with triggers runs only when one of them is in the lowercased text: a cheap
# skip for the rules whose regex can't start from a literal. Order matters only for
# the kind a value is labelled with: the specific shapes run before the name rules.
_RULES = [
    ("private-key", None, _PRIVATE_KEY, 0, None),
    ("anthropic-key", None, re.compile(
        _lit("sk-ant-", "[A-Za-z0-9_-]") + r"[A-Za-z0-9_-]{20,}"), 0, None),
    ("openai-key", None, re.compile(
        _lit("sk-", "[A-Za-z0-9_-]") + r"(?:proj|svcacct|admin)-[A-Za-z0-9_-]{20,}"), 0, None),
    ("stripe-key", ("_live_",), re.compile(
        r"(?<![A-Za-z0-9_])[sr]k_live_[A-Za-z0-9]{10,}"), 0, None),
    ("openai-key", None, re.compile(
        _lit("sk-", "[A-Za-z0-9_-]") + r"(?=[A-Za-z0-9_-]*[0-9])[A-Za-z0-9_-]{20,}"), 0, None),
    ("github-token", None, re.compile(
        _lit("g", "[A-Za-z0-9_]") + r"(?:h[pousr]_[A-Za-z0-9]{30,}|ithub_pat_[A-Za-z0-9_]{30,})"),
     0, None),
    ("aws-key-id", None, re.compile(
        _lit("A", "[A-Za-z0-9]") + r"(?:KIA|SIA)[A-Z0-9]{16}(?![A-Za-z0-9])"), 0, None),
    ("aws-secret", ("secretaccesskey", "secret_access_key", "secret_accesskey",
                    "secretaccess_key"), re.compile(
        r"secret_?access_?key[\"']?(?:[ \t]*[:=][ \t]*|[ \t]+)[\"']?"
        r"(?P<v>[A-Za-z0-9/+=]{40})(?![A-Za-z0-9/+=])", re.I), "v", None),
    ("npm-token", None, re.compile(
        _lit("npm_", "[A-Za-z0-9_]") + r"[A-Za-z0-9]{36}(?![A-Za-z0-9])"), 0, None),
    ("slack-token", None, re.compile(
        _lit("xox", "[A-Za-z0-9-]") + r"[abprs]-[A-Za-z0-9-]{10,}"), 0, None),
    ("telegram-token", None, re.compile(
        r"[0-9]{8,10}:[A-Za-z0-9_-]{35}(?![A-Za-z0-9_-])"), 0, _keep_telegram),
    ("google-key", None, re.compile(
        _lit("AIza", "[A-Za-z0-9_-]") + r"[A-Za-z0-9_-]{35}(?![A-Za-z0-9_-])"), 0, None),
    ("jwt", None, re.compile(
        _lit("eyJ", "[A-Za-z0-9_-]") + r"[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
     0, None),
    ("auth-header", ("authorization",), re.compile(
        r"authorization[\"']?[ \t]*[:=][ \t]*[\"']?(?:bearer|basic|token)[ \t]+"
        r"(?P<v>[^\s\"'`<>]+)", re.I), "v", _keep_value),
    ("url-password", None, re.compile(
        r"://[^\s:@/?#\[\]\"'`<>]*:(?P<v>[^\s@/?#\"'`<>]+)(?=@)"), "v", _keep_url_password),
    # curl -u user:password, --user user:password (also -uuser:pw and --user=user:pw)
    ("user-password", ("-u",), re.compile(
        r"(?<![A-Za-z0-9_-])(?:-u[ \t]*|--user(?:[ \t]+|=))[\"']?[^\s:\"'`<>|;&]*:"
        r"(?P<v>[^\s\"'`<>|;&]+)"), "v", _keep_user_password),
]

# The name-based rules: a value is secret because its key name says so. None of them
# spans lines, so they run only on the lines holding one of these words.
_SECRET_TRIGGERS = ("secret", "token", "passw", "api_key", "apikey", "api-key",
                    "private_key", "privatekey", "private-key", "access_key", "accesskey",
                    "access-key")
_NAME_RULES = [
    # NAME = "value" / "name": "value" / name: 'value' (any spacing)
    ("secret", None, re.compile(
        _NAME + r"[\"']?\]?[ \t]*(?::=|[:=])[ \t]*"
        r"(?:\"(?P<dq>(?:[^\"\\\n]|\\.)*)\"|'(?P<sq>(?:[^'\\\n]|\\.)*)')"),
     ("dq", "sq"), _keep_quoted),
    # an HTTP header inside quotes: -H "X-Api-Key: value"
    ("secret", None, re.compile(
        r"(?<=[\"'])" + _NAME_BODY + r":[ \t]*(?P<v>[^\s\"'`]+)(?=[\"'])"),
     "v", _keep_quoted),
    # NAME=value alone on its line (env files, INI, .properties): the value may hold
    # commas, semicolons, brackets and #, which the next rule stops at
    ("secret", None, re.compile(
        r"^[ \t>]*(?:export[ \t]+)?" + _NAME_BODY
        + r"[ \t]*=[ \t]*(?P<v>[^\s\"'`<>\\]+)[ \t]*$", re.M),
     "v", _keep_bare),
    # NAME=value or name = value, unquoted (env files, shell, flags, INI, TOML, AWS
    # credentials); _keep_bare leaves code alone (get_token(), os.environ[...])
    ("secret", None, re.compile(
        _NAME + r"[ \t]*=[ \t]*(?P<v>[^\s\"'`<>()\[\]{},;\\]+)"),
     "v", _keep_bare),
    # name: value alone on its line (YAML, config dumps), also inside a blockquote
    ("secret", None, re.compile(
        r"^[ \t>]*(?:[-*][ \t]+)?" + _NAME_BODY
        + r"[ \t]*:[ \t]+(?P<v>[^\s\"'`]+)(?:[ \t]+#[^\n]*)?[ \t]*$", re.M),
     "v", _keep_bare),
    # a command-line flag and its value after a space: --password value, --token 'value'
    ("secret", None, re.compile(
        r"(?<![A-Za-z0-9_-])--?" + _NAME_BODY + r"[ \t]+"
        r"(?:\"(?P<dq>(?:[^\"\\\n]|\\.)*)\"|'(?P<sq>(?:[^'\\\n]|\\.)*)'"
        r"|(?P<v>[^\s\"'`<>|;&]+))"),
     ("dq", "sq", "v"), _keep_flag),
]


_RESCAN = 256   # a kept match up to this long is searched again from its second char


def _apply(rules, text):
    """Run each rule over `text` in order; returns (text, replacements)."""
    total = 0
    for kind, _triggers, rx, group, keep in rules:
        out, last, pos = [], 0, 0
        while True:
            m = rx.search(text, pos)
            if not m:
                break
            g = group if isinstance(group, (int, str)) else next(
                x for x in group if m.group(x) is not None)
            if keep and keep(m, m.group(g)):
                # a kept match must not hide a secret that starts inside it, so the
                # search goes on from the next char (re.sub would skip the whole match);
                # past a long one, so a long run of kept matches stays linear
                pos = m.start() + 1 if m.end() - m.start() <= _RESCAN else m.end()
                continue
            out += [text[last:m.start(g)], f"[redacted:{kind}]"]
            last = pos = m.end(g)
            total += 1
        if out:
            text = "".join(out) + text[last:]
    return text, total


def _secret_lines(text):
    """(start, end) of every line of `text` holding a secret trigger word, in order."""
    low = text.lower()
    if len(low) != len(text):     # a char whose lowercase is longer: positions differ
        return [(0, len(text))]
    starts = set()
    for t in _SECRET_TRIGGERS:
        i = low.find(t)
        while i >= 0:
            starts.add(low.rfind("\n", 0, i) + 1)
            e = low.find("\n", i)
            i = -1 if e < 0 else low.find(t, e)
    out = []
    for a in sorted(starts):
        b = text.find("\n", a)
        out.append((a, len(text) if b < 0 else b))
    return out


def redact(text):
    """(text with every secret the rules know replaced by [redacted:<kind>], count).
    Idempotent: redact(redact(t)[0]) == (redact(t)[0], 0)."""
    if not text:
        return text, 0
    low = text.lower()   # a regex can't match without its trigger, so a stale copy is safe
    rules = [r for r in _RULES if not r[1] or any(t in low for t in r[1])]
    text, total = _apply(rules, text)
    if not any(t in low for t in _SECRET_TRIGGERS):
        return text, total
    out, last = [], 0
    for a, b in _secret_lines(text):
        line, n = _apply(_NAME_RULES, text[a:b])
        out += [text[last:a], line]
        last, total = b, total + n
    out.append(text[last:])
    return "".join(out), total


_SPLIT_GUARD = 2048   # longer than any secret the rules know (a long JWT included)


def redact_head(text, limit):
    """redact() on at most the first `limit` chars of `text`, for output that is
    clipped anyway, so a huge tool output costs bounded work. Returns (text, cut).
    When the input was longer, the cut may have split a secret that no rule can
    recognise any more, so the last _SPLIT_GUARD chars of the result are dropped too."""
    text = text or ""
    if len(text) <= limit:
        return redact(text)[0], False
    head = redact(text[:limit])[0]
    return head[:max(0, len(head) - _SPLIT_GUARD)], True


# ---- shared reader ---------------------------------------------------------------
def clean_prompt(block, clip=MAX_PROMPT_CHARS):
    """First real typed text from a user block, skip folded-payload callouts, quote
    bars, rules, images, injected headers; collapse whitespace; clip."""
    out = []
    for ln in block.splitlines():
        s = ln.strip()
        if not s:
            continue
        if s.startswith((">", "[!", "---", "```", "━", "**", "[Image:", "#", "|", "<!--")):
            continue
        if s.startswith("## User Request") or s.lower().startswith("system-reminder"):
            continue
        out.append(s)
        if sum(len(x) for x in out) > clip:
            break
    return re.sub(r"\s+", " ", " ".join(out)).strip()[:clip]


def gather(hours=24, max_prompts=MAX_PROMPTS, max_prompt_chars=MAX_PROMPT_CHARS,
           max_digest_chars=MAX_DIGEST_CHARS, machine_counts=None):
    """List of (project, datetime, prompt) for user turns in the window, newest first,
    A per-project grouping is a regroup of this; for a monthly window call with
    hours=days*24, max_prompt_chars=200.

    max_prompts / max_digest_chars are accepted for the shared review call-shape but
    are DIGEST-layer knobs: gather never truncates the item list, because the reviews'
    per-project counts must reflect the full window even when the rendered digest
    lists only the latest N prompts.

    Headless sessions (machine-run `claude -p`/SDK jobs, stamped by the writer)
    are EXCLUDED from the returned items: the reviews assess the user,
    not the machine's automation (machine prompts can be ~2/3 of a day's digest). Pass a dict as `machine_counts` to
    receive {project: n_turns_in_window} for what was excluded, so digests can
    disclose the exclusion instead of silently narrowing (no silent caps)."""
    del max_prompts, max_digest_chars       # digest-layer knobs (see docstring)
    cutoff = datetime.datetime.now() - datetime.timedelta(hours=hours)
    items = []
    sdir = sessions_dir()
    for path in (glob.glob(os.path.join(sdir, "*.md")) if sdir else []):
        try:
            if datetime.datetime.fromtimestamp(os.path.getmtime(path)) < cutoff:
                continue
            with open(path, encoding="utf-8") as f:
                txt = f.read()
        except Exception:
            continue
        mh = HEADER_RE.search(txt)
        project = (mh.group(1).strip() if mh and mh.group(1) else "—")
        marks = list(TURN_RE.finditer(txt))
        # The machine-session marker counts ONLY in the header region
        # (above the first turn); see MD_HEADLESS. Excluded turns are tallied for
        # the caller's disclosure line, then the session contributes no items.
        head = txt[:marks[0].start()] if marks else txt
        if MD_HEADLESS.search(head):
            if machine_counts is not None:
                n = 0
                for m in marks:
                    try:
                        if datetime.datetime.strptime(m.group(1), TS_FMT) >= cutoff:
                            n += 1
                    except Exception:
                        continue
                if n:
                    machine_counts[project] = machine_counts.get(project, 0) + n
            continue
        for i, m in enumerate(marks):
            try:
                ts = datetime.datetime.strptime(m.group(1), TS_FMT)
            except Exception:
                continue
            if ts < cutoff:
                continue
            seg = txt[m.end(): (marks[i + 1].start() if i + 1 < len(marks) else len(txt))]
            um = USER_RE.search(seg)
            if not um:
                continue
            prompt = clean_prompt(um.group(1), max_prompt_chars)
            if len(prompt) >= 6:
                items.append((project, ts, prompt))
    items.sort(key=lambda x: x[1], reverse=True)
    return items


def machine_summary(counts):
    """One plain-text disclosure line for digest tails: what gather() excluded as
    machine-run sessions ('' when nothing was, callers append unconditionally).
    Kept here so the daily/weekly and monthly digests disclose identically."""
    if not counts:
        return ""
    parts = ", ".join(f"{p}: {n}"
                      for p, n in sorted(counts.items(), key=lambda x: (-x[1], x[0])))
    total = sum(counts.values())
    return (f"🤖 Excluded from this review: {total} machine-run turn(s) from headless "
            f"claude jobs ({parts}): scheduled automation, not your own "
            "work. Many near-identical machine turns can still mean a stuck job.")


# ---- SessionStart pointer --------------------------------------
# Replaces the SessionEnd HANDOFF.md snapshot: instead of COPYING a forgotten session's
# last exchange into the brief, inject-handoff.sh POINTS the next session at the mirror
# notes of the sessions its brief doesn't cover. The mirror already holds them in full.
SESSION_ID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
POINTER_PROMPT_CHARS = 140
_HEAD_BYTES = 4096        # the header region (H1 + marker lines) is ~200 bytes
_TAIL_BYTES = 262144      # enough to hold the last turn of a long tool-heavy session


def _transcript_start_dir(sid, transcripts):
    """The directory a LOCAL session started in: the first `cwd` its raw transcript
    records. Fallback for notes rendered before the project-dir marker existed; None
    for another machine's session (raw transcripts don't sync)."""
    for path in glob.glob(os.path.join(transcripts, "*", f"{sid}.jsonl")):
        try:
            with open(path, encoding="utf-8") as f:
                for i, line in enumerate(f):
                    if i > 50:
                        break
                    if '"cwd"' not in line:
                        continue
                    try:
                        cwd = json.loads(line).get("cwd")
                    except Exception:
                        continue
                    if cwd:
                        return cwd
        except OSError:
            continue
    return None


def _covered_by(d, root, home):
    """True when inject-handoff.sh's walk resolves `d` to `root`: climbing from `d`
    (stopping before $HOME), the first directory that is `root` or holds its own
    HANDOFF.md is `root`. A subdir with its own brief is not counted against the
    parent's; a session outside `root` never is."""
    d = os.path.realpath(d)
    while d and d != os.path.dirname(d) and d != home:
        if d == root:
            return True
        if os.path.isfile(os.path.join(d, "HANDOFF.md")):
            return False
        d = os.path.dirname(d)
    return False


def sessions_since(root, since, exclude=None, home=None, transcripts=None):
    """Interactive (user) sessions of project `root` (see _covered_by) active after `since`
    (epoch seconds), newest first, what ran here since the brief was last written.
    Each item: {id, path, epoch, ts, machine, prompt}; `prompt` is the last turn's
    prompt, cleaned and clipped (quoted DATA, the caller defuses before injecting).

    Reads only the synced mirror, so incognito turns (never mirrored) can't surface,
    and headless jobs (header marker) are skipped. A note's project comes
    from its project-dir marker, else from the local raw transcript; another machine's
    pre-marker notes can't be placed and are skipped. mtime filters first, so the
    start-up scan opens only the notes that changed since `since`."""
    home = os.path.realpath(home or os.environ.get("HOME") or os.path.expanduser("~"))
    root = os.path.realpath(root)
    transcripts = transcripts or os.path.join(home, ".claude", "projects")
    sdir = sessions_dir()
    if not sdir:
        return []
    try:
        entries = list(os.scandir(sdir))
    except OSError:
        return []
    out = []
    for de in entries:
        sid = de.name[:-3] if de.name.endswith(".md") else ""
        if not SESSION_ID_RE.fullmatch(sid) or sid == exclude:
            continue
        try:
            mtime = de.stat().st_mtime
            if mtime <= since:
                continue
            with open(de.path, "rb") as f:
                head = f.read(_HEAD_BYTES).decode("utf-8", "replace")
                f.seek(max(0, os.fstat(f.fileno()).st_size - _TAIL_BYTES))
                tail = f.read().decode("utf-8", "replace")
        except OSError:
            continue
        first = TURN_RE.search(head)
        region = head[:first.start()] if first else head
        if MD_HEADLESS.search(region):
            continue
        m = MD_PROJECT_DIR.search(region)
        start_dir = m.group(1).strip() if m else _transcript_start_dir(sid, transcripts)
        if not start_dir or not _covered_by(start_dir, root, home):
            continue
        marks = list(TURN_RE.finditer(tail))
        ts, prompt = "", ""
        if marks:
            ts = marks[-1].group(1)
            # Judge by when the LAST TURN STARTED, not the note's mtime: the session
            # that ran /handoff writes the brief mid-turn and its note lands after, so
            # an mtime test would list the very session the brief came from.
            try:
                if datetime.datetime.strptime(ts, TS_FMT).timestamp() <= since:
                    continue
            except ValueError:
                pass
            um = USER_RE.search(tail[marks[-1].end():])
            if um:
                prompt = clean_prompt(um.group(1), POINTER_PROMPT_CHARS)
        mm = MD_MACHINE.search(region)
        out.append({
            "id": sid, "path": de.path, "epoch": mtime,
            "ts": ts or datetime.datetime.fromtimestamp(mtime).strftime(TS_FMT),
            "machine": mm.group(1).strip() if mm else "", "prompt": prompt,
        })
    out.sort(key=lambda x: x["epoch"], reverse=True)
    return out


# ---- digest fairness helpers -----------------------
def stratified_sample(items, cap):
    """Pick ≤cap items spread across the window's DAYS instead of `items[:cap]`.

    gather() returns newest-first, so a plain head-slice renders a 7-day review from
    its last ~2 days verbatim (measured on one weekly: 125 gathered, the
    newest 50 shown, about Sunday + Monday morning); the older days survive only as
    counts. Here every active day gets a round-robin share of the cap (newest day
    first, so a leftover slot still favors recency) and items are spread EVENLY
    within each day (endpoints kept), killing both the cross-day and within-day tail
    bias. Deterministic; returns newest-first like gather()."""
    items = list(items)
    if cap <= 0 or len(items) <= cap:
        return items
    by_day = {}
    for it in items:
        by_day.setdefault(it[1].date(), []).append(it)
    days = sorted(by_day, reverse=True)
    quota = {d: 0 for d in days}
    left = cap
    while left > 0:
        gave = False
        for d in days:
            if left > 0 and quota[d] < len(by_day[d]):
                quota[d] += 1
                left -= 1
                gave = True
        if not gave:
            break
    picked = []
    for d in days:
        rows = sorted(by_day[d], key=lambda x: x[1])      # chronological within the day
        q = quota[d]
        if q >= len(rows):
            picked.extend(rows)
        elif q == 1:
            picked.append(rows[-1])
        elif q > 1:
            idx = sorted({round(i * (len(rows) - 1) / (q - 1)) for i in range(q)})
            picked.extend(rows[i] for i in idx)
    picked.sort(key=lambda x: x[1], reverse=True)
    return picked


def day_histogram(items):
    """One deterministic per-day activity line ('📊 Turns/day: 07-08 ×12 · …',
    oldest→newest) so the review model sees the SHAPE of the whole window even when
    the rendered prompts are a sample, an instrument line the assessment must not
    contradict (numbers come from instruments)."""
    if not items:
        return ""
    counts = {}
    for _, ts, _ in items:
        d = ts.date()
        counts[d] = counts.get(d, 0) + 1
    return "📊 Turns/day: " + " · ".join(
        f"{d.strftime('%m-%d')} ×{counts[d]}" for d in sorted(counts))


# ---- round-trip self-check (writer output must survive the parsers) --
def _render_synthetic_session(short, project, machine, turns, headless=None,
                              project_dir=None):
    """Assemble a session doc exactly the way obsidian-mirror.render_session_file
    does: H1, blank, machine line, blank, headless marker, blank, project-dir marker,
    blank, then per turn `---`, blank, turn_block, blank."""
    out = [session_h1(short, project), ""]
    if machine:
        out += [machine_line(machine), ""]
    if headless:
        out += [headless_line(headless), ""]
    pd = project_dir_line(project_dir) if project_dir else ""
    if pd:
        out += [pd, ""]
    for ts, human, reply in turns:
        out += ["---", "", turn_block(ts, human, reply), ""]
    return "\n".join(out)


def _selfcheck():
    turns = [
        ("2026-07-03 08:15:00", "fix the reconcile fence guard\nplease", "Done, patched."),
        ("2026-07-03 09:30:45", "> quoted noise\nnow write the round-trip test", "Written."),
    ]
    doc = _render_synthetic_session("abcd1234", "myproject", "mini", turns)

    # Reader trio (daily/monthly gather layer).
    mh = HEADER_RE.search(doc)
    assert mh and mh.group(1) == "myproject", f"HEADER_RE lost the project: {mh!r}"
    marks = list(TURN_RE.finditer(doc))
    assert [m.group(1) for m in marks] == [t[0] for t in turns], "TURN_RE lost a stamp"
    for i, m in enumerate(marks):
        seg = doc[m.end(): (marks[i + 1].start() if i + 1 < len(marks) else len(doc))]
        um = USER_RE.search(seg)
        assert um, f"USER_RE found no human block in turn {i}"
        got = clean_prompt(um.group(1))
        want = clean_prompt(turns[i][1])
        assert got == want and len(got) >= 6, f"prompt did not survive: {got!r} != {want!r}"
        assert datetime.datetime.strptime(marks[i].group(1), TS_FMT)  # stamp parses back

    # Writer's own tolerant parsers (dashboard-row recovery layer).
    assert MD_H1.search(doc).group(1) == "myproject"
    assert MD_MACHINE.search(doc).group(1) == "mini"
    assert MD_TS.findall(doc) == [t[0] for t in turns]

    # A projectless H1 still matches the tolerant reader (documented divergence).
    assert HEADER_RE.search("# Claude session `beef0000`") is not None
    assert MD_H1.search("# Claude session `beef0000`") is None

    # Model tag: the reply badge must (a) map ids to their short form,
    # (b) be byte-identical to the pre-tag line for None/synthetic, and (c) leave the
    # human-block terminator + stamp fully parseable when present.
    assert short_model("claude-opus-4-8") == "opus-4-8"
    assert short_model("claude-fable-5") == "fable-5"
    assert short_model("") == "" and short_model(None) == "" and short_model("<synthetic>") == ""
    plain = turn_block("2026-07-06 10:00:00", "one two three four", "reply body")
    assert turn_block("2026-07-06 10:00:00", "one two three four", "reply body", None) == plain
    assert turn_block("2026-07-06 10:00:00", "one two three four", "reply body", "<synthetic>") == plain
    tagged = turn_block("2026-07-06 10:00:00", "one two three four", "reply body", "claude-opus-4-8")
    assert "**🤖 Claude** · `opus-4-8`" in tagged, f"tag not rendered: {tagged!r}"
    um = USER_RE.search(tagged)
    assert um and clean_prompt(um.group(1)) == "one two three four", f"tag broke USER_RE: {um!r}"
    assert TURN_RE.search(tagged).group(1) == "2026-07-06 10:00:00", "tag broke the stamp"

    # Headless marker: the writer line must round-trip through
    # MD_HEADLESS (with and without the entrypoint suffix), never collide with the
    # host-label line, and the stamped doc must keep every other parser working.
    assert MD_HEADLESS.search(headless_line("sdk-cli")), "headless_line ↛ MD_HEADLESS"
    assert MD_HEADLESS.search(headless_line()), "bare headless_line ↛ MD_HEADLESS"
    assert not MD_HEADLESS.search(machine_line("mini")), "host label matched MD_HEADLESS"
    assert not MD_MACHINE.search(headless_line("sdk-cli")), "headless matched MD_MACHINE"
    hdoc = _render_synthetic_session("abcd1234", "myproject", "mini", turns,
                                     headless="sdk-cli")
    hmarks = list(TURN_RE.finditer(hdoc))
    assert MD_HEADLESS.search(hdoc[:hmarks[0].start()]), "marker missing from header region"
    assert MD_MACHINE.search(hdoc).group(1) == "mini", "marker displaced the host label"
    assert [m.group(1) for m in hmarks] == [t[0] for t in turns], "marker broke TURN_RE"
    # A turn that merely QUOTES the marker leaves the header region clean (the
    # gather()-side scoping rule: quoting the line must not exclude a session).
    qdoc = _render_synthetic_session("abcd1234", "myproject", "mini",
                                     [("2026-07-03 08:15:00",
                                       "look at this line\n" + headless_line("sdk-cli"),
                                       "Seen.")])
    qmarks = list(TURN_RE.finditer(qdoc))
    assert not MD_HEADLESS.search(qdoc[:qmarks[0].start()]), "quoted marker leaked into header"

    # Project-dir marker: round-trips through MD_PROJECT_DIR, keeps every
    # other parser working, stays out of the prompt text, and refuses unsafe paths.
    pdoc = _render_synthetic_session("abcd1234", "myproject", "mini", turns,
                                     headless="sdk-cli", project_dir="/Users/x/My Proj")
    pmarks = list(TURN_RE.finditer(pdoc))
    assert MD_PROJECT_DIR.search(pdoc[:pmarks[0].start()]).group(1) == "/Users/x/My Proj"
    assert MD_MACHINE.search(pdoc).group(1) == "mini", "project-dir displaced the host label"
    assert MD_HEADLESS.search(pdoc[:pmarks[0].start()]), "project-dir displaced the marker"
    assert [m.group(1) for m in pmarks] == [t[0] for t in turns], "project-dir broke TURN_RE"
    assert clean_prompt(project_dir_line("/a/b")) == "", "marker leaked into prompt text"
    assert project_dir_line("/a/-->/b") == "" and project_dir_line("/a\nb") == ""
    assert project_dir_line("") == "" and project_dir_line(None) == ""
    print("mirror.py round-trip: OK (writer templates ↔ reader regexes)")


if __name__ == "__main__":
    _selfcheck()
