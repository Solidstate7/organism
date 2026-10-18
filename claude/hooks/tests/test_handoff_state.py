#!/usr/bin/env python3
"""tests for handoff-state.py: the facts /handoff states about a repo come from this script.
It reports the branch, HEAD, upstream, ahead and behind, the dirty files, every hash the
brief names with exists and pushed, strips credentials from remote URLs, never reads a
date as a hash, saves a copy of the brief, and --check finds a hash the new brief invents,
a commit it calls pushed while the upstream lacks it, and an open thread it dropped.
Self-contained: temp dirs only (HOME and the state dir sandboxed), git hooks off.
Run:  python3 claude/hooks/tests/test_handoff_state.py"""
import os
import shutil
import subprocess
import sys
import tempfile

HOOKS = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
TOOL = os.path.join(HOOKS, "handoff-state.py")

_fails = []


def check(name, cond):
    print(("  ✓ " if cond else "  ✗ ") + name)
    if not cond:
        _fails.append(name)


T = tempfile.mkdtemp(prefix="handoff-state-")
HOME = os.path.join(T, "home")
os.makedirs(os.path.join(HOME, ".claude", "state"))
ENV = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "CLAUDE_", "ORGANISM_"))}
ENV.update(HOME=HOME, ORGANISM_STATE_DIR=os.path.join(HOME, ".claude", "state"),
           GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull,
           GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@test.invalid",
           GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@test.invalid",
           # pinned dates: the same hashes on every run, so a check never depends on luck
           GIT_AUTHOR_DATE="2026-01-01T00:00:00+0000", GIT_COMMITTER_DATE="2026-01-01T00:00:00+0000")


def git(cwd, *args):
    p = subprocess.run(["git", "-c", "core.hooksPath=" + os.devnull, "-C", cwd] + list(args),
                       capture_output=True, text=True, env=ENV)
    return p.stdout.strip()


def tool(cwd, *args):
    p = subprocess.run([sys.executable, TOOL] + list(args), cwd=cwd, capture_output=True,
                       text=True, env=ENV)
    return p.returncode, p.stdout, p.stderr


def kv(out):
    d = {}
    for ln in out.split("\n\n", 1)[0].splitlines():
        if "=" in ln:
            k, v = ln.split("=", 1)
            d[k] = v
    return d


try:
    bare = os.path.join(T, "origin.git")
    git(T, "init", "-q", "--bare", bare)
    R = os.path.join(T, "repo")
    os.makedirs(R)
    git(R, "init", "-q", "-b", "main")
    with open(os.path.join(R, "app.py"), "w") as f:
        f.write("print(1)\n")
    with open(os.path.join(R, ".gitignore"), "w") as f:
        f.write("HANDOFF.md\n")   # step 0.4 keeps the brief out of git
    git(R, "add", "-A")
    git(R, "commit", "-q", "-m", "feat: first", "-m", "ADR: none")
    A = git(R, "rev-parse", "--short", "HEAD")
    git(R, "remote", "add", "origin", bare)
    git(R, "push", "-q", "-u", "origin", "main")
    with open(os.path.join(R, "app.py"), "a") as f:
        f.write("print(2)\n")
    git(R, "commit", "-q", "-am", "feat: second, not pushed", "-m", "ADR: none")
    B = git(R, "rev-parse", "--short", "HEAD")
    with open(os.path.join(R, "notes.txt"), "w") as f:
        f.write("dirty\n")
    git(R, "remote", "add", "leak",
        "https://user:ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789abcd@example.invalid/x.git")
    brief = os.path.join(R, "HANDOFF.md")
    with open(brief, "w") as f:
        f.write("# HANDOFF — repo\n\n2026-10-18\n\nHEAD `%s` pushed to origin. Commit `fff1fff` was reverted.\n"
                "Date 20261018 is not a hash. Session `642868c6-9a36-41f9-86d9-ebb5d6a6f0e5` ran; the UI was defaced.\n"
                "- 2026-10-18 01:55 · `ac7a37ce` · MacBook Air, last prompt: hi → `/v/_claude-live/sessions/ac7a37ce.md`\n"
                "\n## Next / open threads\n\n"
                "1. Migrate the queue consumer to the new broker\n"
                "2. Re-run the load test against staging\n"
                "3. Write the runbook for the weekly export job\n\n"
                "## How to use this file\n\nfooter\n" % A)

    print("state in a git repo")
    rc, out, err = tool(R)
    d = kv(out)
    check("exits 0", rc == 0)
    check("root is the repo", d.get("root") == os.path.realpath(R) and d.get("root_kind") == "git")
    check("branch and HEAD", d.get("branch") == "main" and d.get("head") == B)
    check("upstream, ahead 1, behind 0, not pushed",
          d.get("upstream") == "origin/main" and d.get("ahead") == "1"
          and d.get("behind") == "0" and d.get("pushed") == "no")
    check("one dirty file", d.get("dirty") == "1" and "?? notes.txt" in out)
    check("commits since the brief: none (the commits are dated before it)", d.get("commits_since_brief") == "0")
    check("the pushed commit the brief names: exists, pushed",
          ("%s exists=yes  pushed=yes  feat: first" % A) in out)
    check("the invented hash on a commit line: exists=no", "fff1fff exists=no" in out)
    hsec = out.split("## hashes named in the brief", 1)[1].split("##", 1)[0]
    check("a date is not read as a hash", "20261018" not in hsec)
    check("a UUID segment, a hex word and a session id in the pointer are not read as hashes",
          "642868c6" not in hsec and "defaced" not in hsec and "ac7a37ce" not in hsec)
    check("the remote's credentials are stripped", "ghp_ABC" not in out and "user:" not in out)
    check("the remote is still listed", "leak https://example.invalid/x.git" in out)
    check("adr-link state is appended", "decision_repo=" in out)
    saved = d.get("brief_saved", "")
    check("the brief was saved under the state dir",
          saved.startswith(ENV["ORGANISM_STATE_DIR"]) and os.path.isfile(saved)
          and open(saved).read() == open(brief).read())

    print("check before any rewrite")
    rc, out, err = tool(R, "--check")
    check("equal copy is reported, hash check still runs", "saved copy equals" in out and "fff1fff missing" in out)
    check("the UUID segment, the hex word and the session id are not reported missing",
          "642868c6 missing" not in out and "defaced missing" not in out and "ac7a37ce missing" not in out)

    print("check after a rewrite")
    with open(brief, "w") as f:
        f.write("# HANDOFF — repo\n\n2026-10-19\n\nHEAD `%s`, pushed and in sync with origin. "
                "`%s` was pushed earlier. Commit `fff1fff` again. Date 20261018.\n\n## Next / open threads\n\n"
                "1. Migrate the queue consumer to the new broker (started)\n"
                "2. Re-run the load test against staging\n\n## How to use this file\n\nfooter\n"
                % (B, A))
    rc, out, err = tool(R, "--check")
    check("exits 1 on a hash claim that doesn't hold", rc == 1)
    check("the unpushed HEAD called pushed is flagged", ("%s unpushed, but the brief calls it pushed" % B) in out)
    check("the pushed commit is not flagged", ("%s unpushed" % A) not in out)
    check("the invented hash is flagged", "fff1fff missing" in out)
    check("an all-digit token that is no commit is not reported missing", "20261018 missing" not in out)
    check("the dropped thread is listed", "Write the runbook for the weekly export job" in out)
    check("a kept thread, reworded, is not listed", "Migrate the queue consumer" not in out.split("## dropped", 1)[1])
    check("the summary counts the claims", "2 hash claim(s) don't hold" in out)

    print("check with a clean brief")
    with open(brief, "w") as f:
        f.write("# HANDOFF — repo\n\nHEAD `%s` · upstream `origin/main`, ahead 1, behind 0. `%s` pushed.\n"
                "The branch is one commit ahead of origin; `%s` is not pushed.\n\n"
                "## Next / open threads\n\n1. Migrate the queue consumer to the new broker\n"
                "2. Re-run the load test against staging\n3. Write the runbook for the weekly export job\n"
                % (B, A, B))
    rc, out, err = tool(R, "--check")
    check("exits 0 when every claim holds", rc == 0 and out.rstrip().endswith("ok"))
    check("the skill's own git line (upstream, ahead 1) and an honest 'not pushed' are not flagged", "unpushed, but" not in out)

    print("no remote, no upstream, detached HEAD, a raw @ in a password")
    N = os.path.join(T, "noremote")
    os.makedirs(N)
    git(N, "init", "-q", "-b", "main")
    with open(os.path.join(N, "a"), "w") as f:
        f.write("a\n")
    git(N, "add", "-A")
    git(N, "commit", "-q", "-m", "feat: a", "-m", "ADR: none")
    NA = git(N, "rev-parse", "--short", "HEAD")
    with open(os.path.join(N, ".gitignore"), "w") as f:
        f.write("HANDOFF.md\n")
    git(N, "add", "-A")
    git(N, "commit", "-q", "-m", "chore: ignore the brief", "-m", "ADR: none")
    with open(os.path.join(N, "HANDOFF.md"), "w") as f:
        f.write("# HANDOFF\n\nCommit `%s` pushed.\n" % NA)
    rc, out, err = tool(N)
    d = kv(out)
    check("no upstream: ahead and behind are n/a, pushed=no-upstream",
          d.get("upstream") == "none" and d.get("ahead") == "n/a" and d.get("behind") == "n/a" and d.get("pushed") == "no-upstream")
    rc, out, err = tool(N, "--check")
    check("a pushed claim in a repo with no remote is flagged", rc == 1 and ("%s this repository has no remote, but the brief calls it pushed" % NA) in out)
    git(N, "remote", "add", "leak2", "https://user:p@ss@example.invalid/x.git")
    git(N, "checkout", "-q", "--detach")
    rc, out, err = tool(N)
    d = kv(out)
    check("detached HEAD: branch (detached), upstream none, counts n/a", d.get("branch") == "(detached)" and d.get("upstream") == "none" and d.get("ahead") == "n/a")
    check("a raw @ in the password is stripped with the rest", "p@ss" not in out and "user:" not in out and "leak2 https://example.invalid/x.git" in out)

    print("state outside a git repository and a vault")
    D = os.path.join(T, "plain")
    os.makedirs(D)
    rc, out, err = tool(D)
    d = kv(out)
    check("exits 0 with root_kind=none", rc == 0 and d.get("root_kind") == "none" and d.get("vault") == "no")
    check("says no brief is written here", "writes no brief here" in out)
    check("says there are no git facts", "not a git repository" in out)
    check("no brief: brief=none", d.get("brief") == "none")

    print("state inside a vault: the root is the vault, one brief per vault")
    V = os.path.join(T, "vault")
    os.makedirs(os.path.join(V, ".obsidian"))
    os.makedirs(os.path.join(V, "sub", "deeper"))
    with open(os.path.join(V, "HANDOFF.md"), "w") as f:
        f.write("# HANDOFF — vault\n")
    rc, out, err = tool(os.path.join(V, "sub", "deeper"))
    d = kv(out)
    check("the vault is found by its .obsidian folder", d.get("vault") == "yes" and d.get("vault_root") == os.path.realpath(V))
    check("root is the vault root, kind vault", d.get("root") == os.path.realpath(V) and d.get("root_kind") == "vault")
    check("the vault's brief is the brief", d.get("brief") == os.path.join(os.path.realpath(V), "HANDOFF.md"))
    G = os.path.join(V, "sub", "repo")
    os.makedirs(G)
    git(G, "init", "-q", "-b", "main")
    d = kv(tool(G)[1])
    check("a git repo inside the vault keeps its own root", d.get("root") == os.path.realpath(G) and d.get("root_kind") == "git" and d.get("vault") == "yes")
    W = os.path.join(T, "notes")
    os.makedirs(os.path.join(W, "docs", "adr"))
    os.makedirs(os.path.join(W, "inner"))
    d = kv(tool(os.path.join(W, "inner"))[1])
    check("a folder holding docs/adr is a root for the brief, not an Obsidian vault",
          d.get("root") == os.path.realpath(W) and d.get("root_kind") == "vault" and d.get("vault") == "no")

    print("the saved vault key, and a vault that is the home folder")
    K = os.path.join(T, "keyed-vault")
    os.makedirs(os.path.join(K, "sub"))
    os.makedirs(os.path.join(HOME, ".claude", "state"), exist_ok=True)
    with open(os.path.join(HOME, ".claude", "state", "paths.env"), "w") as f:
        f.write('CLAUDE_VAULT_ROOT="%s"\n' % K)
    d = kv(tool(os.path.join(K, "sub"))[1])
    check("CLAUDE_VAULT_ROOT alone makes the root and the vault, no .obsidian needed",
          d.get("root") == os.path.realpath(K) and d.get("root_kind") == "vault" and d.get("vault") == "yes")
    with open(os.path.join(HOME, ".claude", "state", "paths.env"), "w") as f:
        f.write('CLAUDE_VAULT_ROOT="%s"\n' % HOME)
    H = os.path.join(HOME, "somewhere")
    os.makedirs(H)
    d = kv(tool(H)[1])
    check("a vault that is $HOME counts as none: no brief in ~/HANDOFF.md", d.get("root_kind") == "none" and d.get("vault") == "no")
    os.remove(os.path.join(HOME, ".claude", "state", "paths.env"))
finally:
    shutil.rmtree(T, ignore_errors=True)

print()
if _fails:
    print("FAIL: %d check(s): %s" % (len(_fails), "; ".join(_fails)))
    sys.exit(1)
print("ALL PASS")
