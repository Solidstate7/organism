#!/usr/bin/env python3
"""tests for secret redaction in the session notes (lib/mirror.redact and where the
writer applies it).

  - every pattern is replaced by [redacted:<kind>], and only the secret itself
  - placeholders and ordinary code are left alone
  - redact() is idempotent
  - a rendered session keeps its markdown structure (turns, callouts, code fences) and
    still parses with the reader regexes; a clip or a cut never leaves part of a secret
  - latest.md's featured turn and table cells are redacted, even from an old index row

Every fixture is assembled from fragments at run time, so no token-shaped literal
sits in this file for a secret scanner to flag. Temp dirs only; never touches ~/.claude.
Run:  python3 claude/hooks/tests/test_redaction.py"""
import importlib.util
import os
import re
import shutil
import sys
import tempfile

HOOKS = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, os.path.join(HOOKS, "lib"))

_TMP = tempfile.mkdtemp(prefix="redaction-test-")
for k in [k for k in os.environ if k.startswith("CLAUDE_")]:
    del os.environ[k]
os.environ["HOME"] = _TMP          # the writer reads paths.env at import: keep it empty

import mirror  # noqa: E402

_spec = importlib.util.spec_from_file_location("obsidian_mirror",
                                               os.path.join(HOOKS, "obsidian-mirror.py"))
om = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(om)

_fails = []


def check(name, cond):
    print(("  ✓ " if cond else "  ✗ ") + name)
    if not cond:
        _fails.append(name)


def j(*parts):
    return "".join(parts)


# ---- fixtures ----------------------------------------------------------------------
ANTHROPIC = j("sk-", "ant-api03-", "Qx7" * 10, "_k-9")
OPENAI_PROJ = j("sk-", "proj-", "a1B2" * 8)
OPENAI_OLD = j("sk-", "T3Blbk", "FJ", "a1" * 20)
GH_CLASSIC = j("gh", "p_", "A1b2" * 9)
GH_SERVER = j("gh", "s_", "Z9y8" * 9)
GH_FINE = j("github", "_pat_", "11AB" * 10)
AWS_ID = j("AK", "IA", "ABCDEFGH23456789")
AWS_STS = j("AS", "IA", "QRSTUVWX23456789")
AWS_SECRET = j("wJalrXUtnFEMI/K7", "MDENG/bPxRfiCY", "0123456789")
SLACK = j("xo", "xb-", "1234567890-", "abcdefghijKL")
TELEGRAM = j("123456789", ":", "AAH", "d4" * 16)
GOOGLE = j("AI", "za", "Sy", "B7" * 16, "x")
STRIPE = j("sk", "_live_", "4eC39HqLyjWD" * 2)
STRIPE_R = j("rk", "_live_", "9fG27KpLmnQR" * 2)
JWT = j("ey", "JhbGciOiJIUzI1NiJ9", ".", "ey", "JzdWIiOiIxMjM0NTY3ODkwIn0", ".",
        "dozjgNryP4J3jVmNHl0w5N_XgL0n3I9P")
PEM_BODY = "\n".join(j("MIIEpAIBAAKCAQEA", c * 48) for c in "abcd")
PEM = j("-----BEGIN ", "RSA PRIVATE KEY-----\n", PEM_BODY, "\n-----END ", "RSA PRIVATE KEY-----")
SSH = j("-----BEGIN ", "OPENSSH PRIVATE KEY-----\n", "b3BlbnNzaC1rZXktdjE", "Q" * 40,
        "\n-----END ", "OPENSSH PRIVATE KEY-----")
CUT_PEM = j("-----BEGIN ", "EC PRIVATE KEY-----\n", "MHcCAQEEIIrYSSNQ", "F" * 48, "\n",
            "AwEHoUQDQgAEPR3t", "U" * 40, "\n")
BEARER = j("abc123DEF", "456ghi789xyz")
BASIC = j("dXNlcjpw", "YXNzd29yZA==")
URL_PW = j("hunter2", "pass")
ENV_VAL = j("abc123", "def456ghi")
QUOTED_VAL = j("s3cr3t-", "pa55word")
JSON_VAL = j("abcd1234", "efgh5678")
YAML_VAL = j("Qw3rTy-", "UiOp-9876")
HEADER_VAL = j("abcdef", "123456")

# (kind, text, the secret that must vanish)
SHAPES = [
    ("anthropic-key", f"use {ANTHROPIC} now", ANTHROPIC),
    ("openai-key", f"key {OPENAI_PROJ} ok", OPENAI_PROJ),
    ("openai-key", f"key {OPENAI_OLD} ok", OPENAI_OLD),
    ("github-token", f"remote {GH_CLASSIC} ok", GH_CLASSIC),
    ("github-token", f"remote {GH_SERVER} ok", GH_SERVER),
    ("github-token", f"remote {GH_FINE} ok", GH_FINE),
    ("aws-key-id", f"id {AWS_ID} ok", AWS_ID),
    ("aws-key-id", f"id {AWS_STS} ok", AWS_STS),
    ("aws-secret", f"aws_secret_access_key = {AWS_SECRET}", AWS_SECRET),
    ("aws-secret", f'"SecretAccessKey": "{AWS_SECRET}",', AWS_SECRET),
    ("slack-token", f"slack {SLACK} ok", SLACK),
    ("telegram-token", f"bot {TELEGRAM} ok", TELEGRAM),
    ("google-key", f"maps {GOOGLE} ok", GOOGLE),
    ("stripe-key", f"pay {STRIPE} ok", STRIPE),
    ("stripe-key", f"pay {STRIPE_R} ok", STRIPE_R),
    ("jwt", f"cookie {JWT} ok", JWT),
    ("private-key", f"key:\n{PEM}\nafter", PEM_BODY),
    ("private-key", f"key:\n{SSH}\nafter", "b3BlbnNzaC1rZXktdjE"),
    ("private-key", f"cut off:\n{CUT_PEM}", "MHcCAQEEIIrYSSNQ"),
    ("auth-header", f"Authorization: Bearer {BEARER}", BEARER),
    ("auth-header", f'curl -H "Authorization: Basic {BASIC}" https://x', BASIC),
    ("auth-header", f'{{"Authorization": "Bearer {BEARER}"}}', BEARER),
    ("url-password", f"postgres://admin:{URL_PW}@db.internal:5432/app", URL_PW),
    ("secret", f"GITHUB_TOKEN={ENV_VAL}", ENV_VAL),
    ("secret", f"export GITHUB_TOKEN={ENV_VAL} && run", ENV_VAL),
    ("secret", f'DB_PASSWORD="{QUOTED_VAL}"', QUOTED_VAL),
    ("secret", f"SECRET_KEY = '{QUOTED_VAL}'", QUOTED_VAL),
    ("secret", f'{{"api_key": "{JSON_VAL}"}}', JSON_VAL),
    ("secret", f'os.environ["API_TOKEN"] = "{JSON_VAL}"', JSON_VAL),
    ("secret", f"client_secret: {YAML_VAL}", YAML_VAL),
    ("secret", f"  - password: {YAML_VAL}  # prod", YAML_VAL),
    ("secret", f"> passwd: {YAML_VAL}", YAML_VAL),
    ("secret", f"PRIVATE_KEY={ENV_VAL}", ENV_VAL),
    ("secret", f"AWS_ACCESS_KEY={ENV_VAL}", ENV_VAL),
    ("secret", f"APIKEY={ENV_VAL}", ENV_VAL),
    ("secret", f"apiKey: '{JSON_VAL}'", JSON_VAL),
    ("secret", f"--password={ENV_VAL}", ENV_VAL),
    ("secret", f'curl -H "X-Api-Key: {HEADER_VAL}" https://x', HEADER_VAL),
    ("secret", f"`GITHUB_TOKEN={ENV_VAL}` then text", ENV_VAL),
]

PLACEHOLDERS = [
    "API_KEY=$API_KEY", "TOKEN=${GITHUB_TOKEN}", "TOKEN=<your-token>", 'password: "<password>"',
    "PASSWORD=xxxxxxxx", "SECRET=********", "TOKEN=", 'TOKEN=""', "API_KEY=changeme",
    "API_KEY=your-api-key-here", 'SECRET_KEY = "changeme-please"', "PASSWORD=short",
    "Authorization: Bearer $TOKEN", 'headers = {"Authorization": f"Bearer {token}"}',
    "Authorization: Bearer <token>", "https://user:${DB_PASS}@host/db",
    'CLIENT_SECRET="{{ client_secret }}"', "PASSWORD=%s",
]

CODE = [
    "token = get_token()", "token=get_token()",
    "client = Client(api_key=api_key, secret_key=self.secret_key)",
    "max_tokens=100000000", '"max_tokens": 4096', "input_tokens: 123456789",
    "tokenizer_path=/models/tok.json", "SECRET_KEY_FILE=/etc/app/secret.key",
    "password = os.environ['PASSWORD']", "The token: it expires in an hour",
    "git@github.com:org/repo.git", "https://example.com/a:b/c", "see task-12345678901234567890",
    "ratio 1234567890123:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsawA",
]


def test_shapes():
    print("every secret shape is redacted, and only the secret")
    for kind, text, secret in SHAPES:
        out, n = mirror.redact(text)
        check(f"{kind}: {text[:40]!r}",
              secret not in out and f"[redacted:{kind}]" in out and n >= 1)
    out, _ = mirror.redact(f"postgres://admin:{URL_PW}@db.internal:5432/app")
    check("the URL keeps its user and host",
          out == "postgres://admin:[redacted:url-password]@db.internal:5432/app")
    out, _ = mirror.redact(f'DB_PASSWORD="{QUOTED_VAL}" # note')
    check("the assignment keeps its name, quotes and comment",
          out == 'DB_PASSWORD="[redacted:secret]" # note')
    out, _ = mirror.redact(f"key:\n{PEM}\nafter")
    check("a private key block collapses to one line", out == "key:\n[redacted:private-key]\nafter")


def test_left_alone():
    print("placeholders and ordinary code are left alone")
    for text in PLACEHOLDERS:
        check(f"placeholder {text!r}", mirror.redact(text) == (text, 0))
    for text in CODE:
        check(f"code {text!r}", mirror.redact(text) == (text, 0))
    check("empty and None pass through", mirror.redact("") == ("", 0)
          and mirror.redact(None) == (None, 0))
    src = open(os.path.join(HOOKS, "obsidian-mirror.py"), encoding="utf-8").read()
    check("the writer's own source has nothing to redact", mirror.redact(src) == (src, 0))


def test_idempotent():
    print("redact() is idempotent")
    texts = [t for _, t, _ in SHAPES] + PLACEHOLDERS + CODE
    ok = True
    for t in texts:
        once, _ = mirror.redact(t)
        again, n = mirror.redact(once)
        if again != once or n:
            ok = False
            print("    not idempotent:", repr(t[:60]))
    check("a second pass changes nothing, on every fixture", ok)
    doc = "\n".join(texts)
    once, n1 = mirror.redact(doc)
    check("…and on all of them in one document", mirror.redact(once) == (once, 0) and n1 > 0)


def test_redact_head():
    print("redact_head: bounded work, no half-secret at the cut")
    short, cut = mirror.redact_head(f"x {ANTHROPIC}", 1000)
    check("under the limit: the same as redact()", (short, cut) == (f"x [redacted:anthropic-key]", False))
    big = "a " * 5000 + ANTHROPIC + " tail"
    limit = big.index(ANTHROPIC) + 15          # the cut splits the key
    head, cut = mirror.redact_head(big, limit)
    check("over the limit: reported as cut", cut)
    check("…and no fragment of the split key survives", "sk-ant" not in head and ANTHROPIC[:12] not in head)


def test_review_shapes():
    print("tricky secret shapes, and inputs that must not hang")
    tg = j("1234567890", ":AAH", "k" * 32)
    pw = j("Hunter2", "Hunter2x")
    val = j("abcdef", "gh12345")
    cases = [
        ("telegram-token", f"curl https://api.telegram.org/bot{tg}/getMe", tg),
        ("telegram-token", f"bot{tg}", tg),
        ("secret", f"<bash-stdout>API_KEY={val}\nDB_PASSWORD={pw}</bash-stdout>", pw),
        ("secret", f"<command-args>API_KEY={val}</command-args>", val),
        ("secret", f"echo DB_PASSWORD={pw}>.env", pw),
        ("secret", f"aws_session_token = {j('IQoJb3JpZ2', 'luX2VjEXAMPLE123')}", "luX2VjEXAMPLE123"),
        ("secret", f"password = {pw}", pw),
        ("secret", f"db.password = {pw}", pw),
        ("secret", f"PASSWORD={j('enterprise', '2024Secure!')}", "2024Secure!"),
        ("secret", f"SECRET={j('Yourmother', '123456')}", "123456"),
        ("secret", f"API_TOKEN={j('abc-none-', '9f8e7d6c5b4a')}", "9f8e7d6c5b4a"),
        ("url-password", f"redis://:{pw}@localhost:6379/0", pw),
        ("aws-secret", f"aws configure set aws_secret_access_key {AWS_SECRET}", AWS_SECRET),
        ("secret", f"DB_PASSWORD={j('P@ss,wo', 'rd;12345')}", "rd;12345"),
        ("secret", f"DB_PASSWORD={j('Xk9{mQ', '2#pLzz')}", "2#pLzz"),
        ("secret", f"export DB_PASSWORD={j('abc(de', 'f)ghijk')}", "f)ghijk"),
        ("secret", f"password: {j('abc#12', '3defghijk')}", "3defghijk"),
        ("secret", f"mysql --password {pw} -u root", pw),
        ("user-password", f"curl -u admin:{pw} https://x", pw),
        ("user-password", f"curl --user admin:{pw} https://x", pw),
        ("user-password", f"curl --user='admin:{pw}' https://x", pw),
        ("user-password", f"curl -uadmin:{pw} https://x", pw),
        ("secret", f"app --api-key '{val}'", val),
        ("secret", f'tool -token "{val}" run', val),
        ("npm-token", f"//registry.npmjs.org/:_authToken={j('npm_', 'a1B2c3D4e5F6g7H8i9J0')}"
                      "k1L2m3N4o5P6q7R8", "a1B2c3D4e5F6g7H8i9J0"),
    ]
    for text in ("the --token flag sets it", "--password prompt", "--password-file /etc/pw",
                 "--token-count 12345678", "--password $DB_PASSWORD", "--secret -v",
                 "docker run -u 1000:1000 img", "git push -u origin HEAD:main",
                 "curl -u $USER:$PASS https://x", "curl -u admin: https://x"):
        check(f"flag left alone: {text!r}", mirror.redact(text) == (text, 0))
    text = f'API_KEY={val}]: "x password: "{pw}"'     # a kept match must not hide the second
    once = mirror.redact(text)[0]
    check("a value inside a match that is left alone is still found", pw not in once
          and mirror.redact(once) == (once, 0))
    for kind, text, secret in cases:
        out, n = mirror.redact(text)
        check(f"{kind}: {text[:44]!r}", secret not in out and f"[redacted:{kind}]" in out and n >= 1)
    for text in ["token = get_token()", "api_key=os.environ['X']", "password = self.password",
                 "if token == expected:", "max_tokens = 4096", "TOKEN=your_token_here",
                 "SECRET=changeme123", "token = make_token(a, b)", "password: x  # not set"]:
        check(f"still left alone: {text!r}", mirror.redact(text) == (text, 0))
    import time
    for text in ["<https://x.com/cb?access_token=abc&" + "&".join(f"k{i}=v{i}" for i in range(8000)) + ">",
                 "token " + "x=" * 30000 + "("]:
        t = time.monotonic()
        mirror.redact(text)
        check(f"a long k=v run finishes fast ({len(text)} chars)", time.monotonic() - t < 1.0)


# ---- the writer ----------------------------------------------------------------------
def _entries():
    ts = "2026-07-01T10:00:00.000Z"
    prompt = j("here is my env:\n```\n", f"GITHUB_TOKEN={ENV_VAL}\n", PEM, "\n```\n",
               f"and this cookie {JWT} please")
    fenced = j("config:\n```yaml\n", f"password: {YAML_VAL}\n", "```\n", "````\nnested\n````\n",
               SSH, "\nend")
    edge = "a" * 2990 + " " + ANTHROPIC + " tail"                  # straddles the 3000 clip
    # key blocks that redact down to a few hundred chars, then a token split by the scan
    # bound: without the guard, the split token would land inside the visible clip
    pems = (PEM + "\n") * ((om.REDACT_SCAN - 20) // (len(PEM) + 1))
    huge = pems + "b" * (om.REDACT_SCAN - 10 - len(pems)) + " " + GH_CLASSIC + " " + "c " * 5000
    tool = lambda i, name, inp: {"type": "tool_use", "id": f"t{i}", "name": name, "input": inp}
    result = lambda i, text: {"type": "tool_result", "tool_use_id": f"t{i}", "content": text}
    return [
        {"type": "user", "timestamp": ts, "cwd": "/tmp/p",
         "message": {"role": "user", "content": prompt}},
        {"type": "assistant", "timestamp": ts, "message": {"role": "assistant", "content": [
            {"type": "text", "text": f"I see {ANTHROPIC} in there."},
            tool(1, "Bash", {"command": f"curl -H 'Authorization: Bearer {BEARER}' https://api"}),
            tool(2, "Read", {"file_path": "/tmp/p/config.yaml"}),
            tool(3, "Bash", {"description": "Print a long file", "command": "cat big"}),
            tool(4, "Bash", {"description": "Print a huge file", "command": "cat huge"})]}},
        {"type": "user", "timestamp": ts, "message": {"role": "user", "content": [
            result(1, f"HTTP 200 token={ENV_VAL}"), result(2, fenced),
            result(3, edge), result(4, huge)]}},
        {"type": "assistant", "timestamp": ts, "message": {"role": "assistant", "content": [
            {"type": "text", "text": f"Done.\npassword: {YAML_VAL}\nRotate {SLACK} too."}]}},
    ]


def _skeleton(body):
    """The structural lines of a rendered session: turn headings, speaker lines, rules,
    callout headers (up to their title) and code fences (with their quote prefix)."""
    out = []
    for ln in body.splitlines():
        if ln.startswith(("### 🕐", "**🧑 You**", "**🤖 Claude**")) or ln == "---":
            out.append(ln)
        elif ln.startswith("> [!"):
            out.append(ln.split(" · ")[0])
        elif re.match(r"^(?:> ?)*`{3,}", ln):
            out.append(re.match(r"^(?:> ?)*`{3,}", ln).group(0))
    return out


def test_writer():
    print("a rendered session: secrets gone, markdown intact")
    entries = _entries()
    body, n, last = om.render_session_file(entries, "abcd1234", "proj")
    secrets = [ENV_VAL, PEM_BODY[:20], JWT, ANTHROPIC, BEARER, YAML_VAL, "b3BlbnNzaC1rZXktdjE",
               GH_CLASSIC, SLACK]
    check("no secret reaches the note", not [s for s in secrets if s in body])
    check("no fragment of a key cut by the 3000-char clip", "sk-ant" not in body)
    check("no fragment of a token split by the pre-cut", GH_CLASSIC[:8] not in body)
    check("the huge output is still marked truncated", body.count("… (truncated)") >= 2)
    for kind in ("secret", "private-key", "jwt", "anthropic-key", "auth-header", "slack-token"):
        check(f"[redacted:{kind}] is in the note", f"[redacted:{kind}]" in body)
    check("the tool title (the command) is redacted", "Bearer [redacted:auth-header]" in body)

    # the same transcript rendered with redaction switched off: same structure
    saved = (om.mirror.redact, om.mirror.redact_head)
    om.mirror.redact = lambda t: (t, 0)
    om.mirror.redact_head = lambda t, limit: (t or "", False)
    try:
        raw, _, _ = om.render_session_file(entries, "abcd1234", "proj")
    finally:
        om.mirror.redact, om.mirror.redact_head = saved
    check("(control: the unredacted render does hold the secrets)", ANTHROPIC in raw and JWT in raw)
    check("turns, callouts and code fences match the unredacted render",
          _skeleton(body) == _skeleton(raw))
    blocks, cur = [], None
    for ln in body.splitlines():
        if ln.startswith("> [!"):
            cur = []
            blocks.append(cur)
        elif cur is not None and not ln.startswith(">"):
            cur = None
        elif cur is not None:
            cur.append(ln)
    fences = [sum(1 for ln in b if re.match(r"^> `{3,}", ln)) for b in blocks]
    check("every tool callout keeps a balanced code fence", len(blocks) == 4
          and all(f >= 2 and f % 2 == 0 for f in fences))

    marks = list(mirror.TURN_RE.finditer(body))
    um = mirror.USER_RE.search(body[marks[0].end():]) if marks else None
    check("the reader regexes still find the turn and the prompt",
          n == 1 and len(marks) == 1 and um and mirror.clean_prompt(um.group(1)) ==
          "here is my env: GITHUB_TOKEN=[redacted:secret] [redacted:private-key] "
          "and this cookie [redacted:jwt] please")
    ts, human, reply, gist, model = last
    check("the table gist is redacted", ENV_VAL not in gist and JWT not in gist)
    check("the featured turn is redacted", ENV_VAL not in human and ANTHROPIC not in reply)


def test_dashboard():
    print("latest.md: the featured turn and every prompt cell")
    stale = {"id": "11111111-1111-4111-8111-111111111111", "short": "11111111", "project": "old",
             "ts": "2026-07-01 09:00:00", "epoch": 1,
             "prompt": om.cell(f"deploy with GITHUB_TOKEN={ENV_VAL} now")}   # an old index row
    cur = {"id": "22222222-2222-4222-8222-222222222222", "short": "22222222", "project": "new",
           "ts": "2026-07-01 10:00:00", "epoch": 2, "prompt": "fine",
           "human": f"my key {ANTHROPIC}", "reply": f"Saw {JWT}", "model": None}
    board = om.render_dashboard({stale["id"]: stale, cur["id"]: cur}, cur)
    check("an old row's prompt cell is redacted", ENV_VAL not in board and "[redacted:secret]" in board)
    check("the featured prompt and reply are redacted", ANTHROPIC not in board and JWT not in board)
    check("the table keeps its shape", board.count("\n| ") == 3)
    cut = j("gh", "p_", "A1b2" * 6) + "…"        # an old row cut short inside a token
    board = om.render_dashboard({stale["id"]: {**stale, "prompt": "push with " + cut}, cur["id"]: cur}, cur)
    check("an old row cut inside a token loses the cut-off word", cut[:12] not in board
          and "push with …" in board)

    sdir = os.path.join(_TMP, "notes")
    os.makedirs(sdir, exist_ok=True)
    note = os.path.join(sdir, "33333333-3333-4333-8333-333333333333.md")
    with open(note, "w", encoding="utf-8") as f:     # a note another machine wrote unredacted
        f.write("\n".join([mirror.session_h1("33333333", "proj"), "", "---", "",
                           mirror.turn_block("2026-07-01 08:00:00", f"use {SLACK} please", "ok"), ""]))
    row = om._parse_session_md(note)
    check("a row read back from an unredacted synced note is redacted",
          row and SLACK not in row["prompt"] and "[redacted:slack-token]" in row["prompt"])


if __name__ == "__main__":
    try:
        test_shapes()
        test_left_alone()
        test_idempotent()
        test_redact_head()
        test_review_shapes()
        test_writer()
        test_dashboard()
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
    print(f"\n{'ALL PASS' if not _fails else 'FAILED: ' + ', '.join(_fails)}")
    sys.exit(1 if _fails else 0)
