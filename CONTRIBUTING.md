# Contributing to Organism

Thanks for helping. Bug reports, fixes and small focused features are all welcome. For a
bigger change, open an issue first so we can agree on the shape before you build it.

## Working on Organism

Clone the repo and start a session with your clone loaded as the plugin:

```sh
git clone https://github.com/Solidstate7/organism.git
claude --plugin-dir ./organism
```

That session runs the hooks and skills from your clone; a change shows up at the next
session start. If you also have Organism installed from the marketplace, disable it
first with `/plugin`, so its hooks don't run twice.

## Running the tests

```sh
bash claude/hooks/tests/run-all.sh
```

It prints `ALL PASS` or exits nonzero. It runs:

1. static checks on every tracked (and untracked, not ignored) file: `python3 -m
   py_compile` on `.py`, `bash -n` on `.sh`, and `node --check` on `.js` when node is
   installed;
2. every `claude/hooks/tests/test_*.py` and `test_*.sh`.

Every test works in temporary folders (a temporary `HOME` where it needs one) and never
touches your real `~/.claude`, your vault or `launchd`. Checks that need macOS skip themselves on other systems.

`test_timing.py` measures a few hot paths and fails only far past normal times. On a slow
machine, `ORGANISM_TIMING_SCALE=3` gives it three times the room, and
`ORGANISM_SKIP_TIMING=1` skips it.

The `/harvest` eval harness in `claude/hooks/tests/evals/harvest/` runs a real headless
Claude session per sample repo, so it costs tokens and is never part of the test suite.
Its README explains how to run it.

## How CI runs

`.github/workflows/ci.yml` runs the same `run-all.sh` on every push and pull request, on
`macos-latest` and `ubuntu-latest`. Python 3 (standard library only), node and git are
preinstalled on both, so there is no setup step. A change is ready when both pass.

## Working on hooks safely

Organism's hooks run inside your own Claude Code sessions. While you work on it:

- Test in a temporary `HOME` (copy the pattern from an existing test); never point a
  test at your real `~/.claude`.
- Run `deploy.sh`, `converge.sh` and the `launchd/` scripts only with a temporary `HOME`
  unless you mean to change your own install. `ORGANISM_DRY=1` only skips `launchctl` and
  alerts; it does not stop `deploy.sh` from relinking `~/.claude`.
- Add a test with every fix, and check that the test fails without the fix.

## Style

- Python uses the standard library only and must run on Python 3.9.
- Shell scripts must run under macOS's bash 3.2 and BSD tools (`sed`, `awk`, `date`).
- Comments say what the code does and why, in plain English. Leave out dates, ticket or
  decision numbers, review tags and personal names; put history in the commit message.
- Public text (docs, comments, messages the tools print) uses plain English and no em
  dashes. The one exception is the record formats other tools parse: the session note
  header (`# Claude session ... — project`), `# HANDOFF — `, `# Decisions — ` and the
  decision index lines; keep those as they are.
- Commit subjects follow [Conventional Commits](https://www.conventionalcommits.org/)
  (`fix(guard): ...`, `docs: ...`), with a body when the reason isn't obvious.

## Pull requests

Fill in the pull request template, keep each pull request to one change, and make sure
`run-all.sh` passes. By contributing you agree that your contribution is licensed under
the repository's license.

## Releasing

A push to `main` is a release. `.claude-plugin/marketplace.json` serves the plugin from
`./`, so every user who installed Organism through its marketplace gets `main` at their
next plugin update. CI runs only after the push, so check everything before it:

1. Run `bash claude/hooks/tests/run-all.sh` on the exact commit that you will push. It
   must print `ALL PASS`.
2. Read every commit that `origin/main` doesn't have yet (`git log -p origin/main..HEAD`)
   for names, paths or keys that must stay private.
3. Set the new version in `.claude-plugin/plugin.json`, and give it a dated section in
   `CHANGELOG.md` that covers every change since the last published version.
4. Check that the README still matches the code.
5. Tag the release commit: `git tag -a vX.Y.Z -m "Organism X.Y.Z"`.
6. Push `main` and the tag: `git push origin main vX.Y.Z`. The push must be a fast-forward.
   Never force-push `main`, because users' plugin caches hold the old commits. To undo a
   release, revert the commits and push the reverts.
7. Wait for CI to pass on macOS and Linux. Then, on a computer with the marketplace
   install, run `claude plugin marketplace update organism` and
   `claude plugin update organism@organism`, and check that `claude plugin list` shows
   the new version.

A plugin must not carry a `CLAUDE.md` at its root, because Claude Code would load it into
every user's session. That is why these notes live here.
