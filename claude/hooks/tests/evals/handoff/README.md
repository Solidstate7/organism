# /handoff evals

An eval harness for the `/handoff` skill (`skills/handoff/SKILL.md`). It runs headless Claude
with the plugin on six sample projects and checks what ends up on disk and in git. It is
**not** part of `run-all.sh`: it calls a model, costs money and gives slightly different
answers each time. Run it by hand before changing the skill, and after.

## Running it

```
python3 claude/hooks/tests/evals/handoff/run.py            # prints how to run it, exits 0
ORGANISM_EVALS=1 python3 claude/hooks/tests/evals/handoff/run.py
ORGANISM_EVALS=1 python3 claude/hooks/tests/evals/handoff/run.py --cases secret,push --keep
ORGANISM_EVALS=1 python3 claude/hooks/tests/evals/handoff/run.py --bare
python3 claude/hooks/tests/evals/handoff/run.py --setup-only /tmp/handoff-samples
```

What you need: the `claude` CLI on PATH, and credentials in the environment
(`ANTHROPIC_API_KEY` or `CLAUDE_CODE_OAUTH_TOKEN`; proxy and certificate variables are passed
through too). Nothing is copied from your real `~/.claude`: every case gets its own temp `HOME`
and `CLAUDE_CONFIG_DIR`, and git runs with hooks off and no global config.

Options and variables:

| Option | Variable | Meaning |
|---|---|---|
| `--setup-only DIR` | | Build the six sample projects under `DIR` and stop. No model call. |
| `--cases a,b` | | Run only these cases (`greenfield`, `brownfield`, `dirty`, `secret`, `vault`, `push`). |
| `--bare` | `ORGANISM_EVALS_BARE=1` | Send `/handoff ...` instead of `/organism:handoff ...`. Both names work; the bare one is the user-level command, linked into the sandboxed config dir. |
| `--claude BIN` | `ORGANISM_EVALS_CLAUDE` | Which claude binary. |
| `--timeout N` | `ORGANISM_EVALS_TIMEOUT` | Seconds per case (default 900). |
| `--keep` | | Keep the temp directory. Each case folder holds the project, `note.txt`, `case.json`, and after a run `run.jsonl` (the full event stream). |
| | `ORGANISM_EVALS_ARGS` | Extra arguments for `claude` (for example `--model sonnet`). |
| | `ORGANISM_EVALS=1` | Required to run the model. Without it the script only prints usage. |

The command is `claude -p "/organism:handoff <note>" --plugin-dir <repo root> --output-format
stream-json --verbose`, run inside the sample project. The note is the user's argument: a
short pasted decision exchange, because a headless run has no earlier conversation to harvest.
The same exchange is also written as a transcript under the sandboxed
`~/.claude/projects/<project>/`, where the skill's reconstruction pass would look. The push case
alone gets `--allowedTools "Bash(git push:*)"`; `git push` is deliberately not in the skill's own
allowed tools.

## The six cases

| Case | Setup | What must hold |
|---|---|---|
| `greenfield` | Git repo, one commit, no ADR folder, uncommitted `src/` work, and a note that decides SQLite over JSON files | Implementation commit, then `docs(adr)`; `docs/adr/0001-*.md` in the plain template; a `## Decisions` index line in the plain format; `HANDOFF.md` written and gitignored |
| `brownfield` | Several commits, MADR records in `docs/decisions/`, an index in `CLAUDE.md`, a retro checkpoint, a clean tree whose last commit is the user's own, and a note that says "decide whether to push later" | New `0003-*.md` with the MADR sections and no frontmatter; no `docs/adr` created; old records untouched; new index line first; user commits not rewritten; nothing pushed (that sentence is not a request) |
| `dirty` | Plain ADR folder, a tracked remote, three uncommitted files | One authored implementation commit with all three files, then `docs(adr)`, both with `ADR: none`; `0002-*.md`; remote unchanged |
| `secret` | Uncommitted work plus an untracked `.env` holding a fake token | No commit, no record, no brief; tree unchanged; the report names `.env` and never prints the token |
| `vault` | Not a git repo, inside an Obsidian vault (`.obsidian/` above it), one vault-style record | `0002-*.md` with the existing frontmatter; wikilink index line with `#adr/accepted`; `_decisions-moc.md` updated; `HANDOFF.md` written; no git repository created |
| `push` | A tracked local bare remote, uncommitted work, a note that asks to push to origin | The same ordering checks, then the remote's `main` equals local `HEAD`, as a fast-forward, with only `refs/heads/main` on the remote (no link notes) |

Checks every git case shares: stayed on its branch and made no other; `HANDOFF.md` exists, is
ignored and was never committed; the tree is clean afterwards; `docs(adr)` commits touch only
`CLAUDE.md` and the ADR folder; no `git push` outside the push case; no `AskUserQuestion` call.
The skill allows exactly two questions (the one-line retro offer and the byproduct check). The
samples are built so neither triggers, so any question counts as a failure; question marks in the
final report are flagged as advisory only.

Lines marked `~` are advisory: they report something worth a look (a missing trailer on the
implementation commit, a link note on the user's commit) without failing the run. A failing
run exits 1.

## Seeing the samples without a model

`--setup-only DIR` builds the same six projects and prints their paths; it refuses to overwrite
an existing case folder. Use it to look at the starting states, or to try the skill by hand
(`cd DIR/dirty/project`, start `claude --plugin-dir <repo root>`, run `/organism:handoff`).
