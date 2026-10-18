# RETRO mode: reconstruct decisions made before Organism was installed

Read this file only when step 0 of `/handoff` made the retro offer and the user accepted, or when `$ARGUMENTS` contains `--retro`. Everything in SKILL.md still applies; this adds the reconstruction pass, R1 to R4, which runs before job 2.

Use when a project predates the harvest loop: decisions live in code/git/old sessions but not in ADRs.

## R1. Build the candidate list from the map↔territory diff, never replay history linearly

- **Map:** the ADR index + each existing ADR's load-bearing claims; any orientation/state doc (`ORIENTATION.md`, audits), note these often *name* the gaps ("cut later", "user decision <date>", drift tables).
- **Territory, TWO sources, not just git:**
  1. `git log --reverse --date=short --pretty="%h %ad %s"` over the whole history.
  2. **In-repo decision docs**: `CLAUDE.md`, `AGENTS.md`, READMEs, design/spec docs. These *describe* architectural decisions in prose (the agent roster, the gate, the runtime model, key constraints) that often were never committed as ADRs. **This source is essential when git history is shallow**, a project git-tracked long after it began (e.g. everything in one "Initial commit", or data older than the first commit) has its real decisions living in the docs, not the diff. Check `git log --reverse | head -1` against the project's apparent age; if git starts late, lean on the docs and say so in Provenance (fidelity is doc-derived, not git-derived).
- **Candidates = decision-shaped changes the map doesn't cover**: from *either* territory source. Apply the project's own "what gets an ADR" bar if its ADR README states one; else the job-1 definition (choice + rationale + consequence; structure/protocol/dependency/scope cuts; anything a future reader would reverse-engineer from a diff or a doc). High-yield patterns: (i) a commit that contradicts an existing ADR's claim; (ii) a scope decision recorded only in a working doc; (iii) **an architectural choice stated as fact in `CLAUDE.md`/README with no backing ADR.**
- Routine impl/fix/content commits are NOT candidates. Bound the pass: if the diff yields nothing, say so and stop, never invent decisions.

## R2. Source ladder per candidate (cite the best layer you can reach)

1. **In-repo contemporaneous docs** (sequence-plan, design notes), highest fidelity; only the ADR *framing* is retroactive.
2. **Raw session transcripts**: `~/.claude/projects/<root with "/"→"-">/*.jsonl`. User verbatim = decision-grade. ⚠️ ~30-day rolling retention, note in the report when the window has already eaten candidate-era sessions. ⚠️ mtime = *last* write: resumed sessions hide early days inside later-mtime files, so locate originating sessions by `grep -l` for commit-message strings/keywords, never by date listing.
3. **Turn mirror** (vault `_claude-live/sessions/`, if present), durable and synced across machines: every prompt, every reply text block and every tool call (tool inputs dropped, outputs clipped). Once raw transcripts have expired, this is the decision-grade source.
4. **Commit messages**: rationale composed at implementation time (post-hoc; label it so).
5. **Inference from code**: lowest; mark explicitly as inferred.

**Mining technique:** transcripts are megabytes of one-line JSON, never Read them whole. `grep -l` to find files, then python3 to print (line-index, role, ~1500 chars) around keyword matches; pull surrounding events by index for the deliberation. Fan out one read-only subagent (Task/Explore) per candidate, in parallel, returning: user's verbatim words, stated reasons, alternatives discussed (or "none recorded"), session+line range, confidence.

## R3. Write reconstructed ADRs, provenance is the contract

Conform to the existing template (step 1c) and continue numbering (`organism adr-index next`), with these retro-specific additions:

- **Date:** the ORIGINAL decision date, plus `· recorded <today>`.
- **A `Provenance:` line in the header block** stating: reconstructed <today>, the sources, and the fidelity split, what is user-verbatim, what is post-hoc (commit-message-only), what is inferred. If the session shows a terse directive with no deliberation, the Alternatives section says "None recorded", **a reconstruction must never invent a deliberation that didn't happen.**
- **Contradicted existing ADRs get a minimal forward-pointer** appended to their status line ("<claim> amended by ADR-NNNN: <one clause>"), never rewrite their body (append-only, same as supersession).
- **Index entries** are marked `accepted (reconstructed)`, and the index gains a one-line legend explaining the marker (recorded retroactively from git + transcripts; decision dates are original).
- **Link the implementing commit(s) back to the ADR**: this is retro-only and load-bearing. A reconstructed ADR postdates its implementing commit, so that commit can *never* name it: neither the forward enforcer nor step-1.5 `backfill` can form the link (both need the commit to mention an ADR that didn't exist yet). For each implementing/provenance commit `<hash>` you cited, run `organism adr-link link <hash> <N>` (N = the new ADR number; the linker writes a non-destructive `git notes --ref=adr` note on that historical commit). Step 1.5's `backlinks write` (which runs after) then lists those commits under the ADR as *implements*. Skip if `organism adr-link` is not available.

## R4. Gate, commit, report

1f-bis (user ratifies the draft) and 1g (surgical commit) apply as-is; a sensible message: `docs(adr): retroactive realign: reconstruct NNNN..MMMM from git + session transcripts`, with the same **`ADR: none`** trailer as 1g (a retro recording commit is not an implementation either, the real ADR↔commit links come from the R3 `link` calls to the historical implementing commits, not from this commit). **After the reconstruction commits, set the retro checkpoint** (`organism adr-link retro-checkpoint set`) so future handoffs stop offering retro, this pass dispositioned the backlog; step 1.5's commit includes the marker. Then step 1.5 (link refresh) and job 2 run normally. In the final report, state coverage honestly: which candidates were reconstructed at which fidelity, which were skipped and why, and whether transcript retention has already cost anything (that urgency is the reason to run retro sooner, not later, on other projects that predate Organism).
