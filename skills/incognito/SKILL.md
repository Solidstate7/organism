---
name: incognito
description: Toggle incognito mode for the Obsidian session mirror. While it is on, turns aren't recorded to _claude-live.
argument-hint: "[on|off|toggle|purge|status]"
disable-model-invocation: true
allowed-tools: Bash(organism incognito:*), Bash(~/.local/bin/organism incognito:*)
---

Run this exact command and report its single line of output to the user verbatim, then stop:

```
organism incognito $ARGUMENTS
```

If `organism` is not found, run `~/.local/bin/organism incognito $ARGUMENTS` instead.

Notes:
- `$ARGUMENTS` is one of `on`, `off`, `toggle`, `purge` or `status` (empty means `status`).
- `purge` is retroactive: it scrubs THIS session's already-recorded mirror (its `sessions/<id>.md`, its `.sessions.json` entry, and its row and featured reply in `latest.md`), seals every turn so far so it stays gone even across a `--backfill`, and turns incognito on. Other machines drop its row once the deletion syncs. Plain `on` is forward-only.
- Incognito stays on until you turn it off, across a resume too: a resumed incognito session gets a one-line reminder at its start, not a silent resume.
- This only gates `obsidian-mirror.py` (the session to `_claude-live/` recorder). The raw transcript, the reconciler, and `/handoff` are unaffected.
- It is a fast local toggle: do nothing else, and do not explain unless asked.
