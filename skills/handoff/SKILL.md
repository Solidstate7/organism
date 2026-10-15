---
name: handoff
description: Harvest decisions into ADRs (Obsidian-native in a vault) + a CLAUDE.md index, commit them, then rewrite HANDOFF.md. --retro reconstructs PRE-harness decisions from git + session transcripts.
argument-hint: [--retro] [optional focus note]
disable-model-invocation: true
allowed-tools: Read, Write, Edit, Task, Grep, Bash(pwd), Bash(ls:*), Bash(find:*), Bash(cat:*), Bash(wc:*), Bash(grep:*), Bash(python3:*), Bash(git status:*), Bash(git log:*), Bash(git branch:*), Bash(git rev-parse:*), Bash(git add:*), Bash(git commit:*), Bash(git check-ignore:*), Bash(git ls-files:*), Bash(git diff:*), Bash(git rm --cached:*), Bash(git rm -r --cached:*), Bash(organism adr-link:*), Bash(organism guard-scan:*), Bash(~/.local/bin/organism adr-link:*), Bash(~/.local/bin/organism guard-scan:*)
---

Two jobs, in order, both targeting the CURRENT project's root:

1. **Decisions → ADRs + `CLAUDE.md` index, committed**: record this session's architectural decisions as durable records, indexed from the always-loaded `CLAUDE.md`, and commit just those files to git.
2. **State → `HANDOFF.md`**: rewrite the living session brief (gitignored, local-only).

Do job 1 first (it's the durable one), then job 2. Only steps 0.4, 0.5, 1g, 1g-bis, 1.5, 1.6 and the retro R3 links touch git, and 1.6 (the push) runs only when the user's note asks for one. The continuity loop itself is disk-based and never needs a commit.

The helper commands below are `organism <verb>` (`organism adr-link ...`, `organism guard-scan ...`). If `organism` is not on PATH, run `~/.local/bin/organism` instead.

**Forward vs retro is auto-detected, the user never passes a mode.** In step 0 you ask the link tool (`organism adr-link state`) and act on what it reports:
- **Always** run the forward harvest (job 1 below, source = *this conversation*) and the linkage catch-up (1g-bis + 1.5). These are invisible to the user.
- **If `organism adr-link state` reports `retro_backlog=yes` AND `retro_checkpoint=none`** (history holds decision-shaped work that no ADR covers, and it hasn't been dispositioned yet) **surface a one-line offer**, e.g. *"this project has ~N unrecorded historical decisions; reconstruct them now? (y/N)"*, and run the **RETRO reconstruction** (R1–R4 below) only if the user agrees. Do not silently launch the heavy archaeology, and **never invent decisions**; the user is never asked to know the word "retro" up front. **If they decline** (or R1 turns up no real candidates), set the checkpoint so the offer doesn't nag every handoff: `organism adr-link retro-checkpoint set` (step 1.5 commits it). Re-open later with `--retro` or `retro-checkpoint clear`. If `retro_checkpoint` is already set, stay silent, retro is a one-time catch-up, not a recurring prompt.
- `$ARGUMENTS` containing `--retro`/`retro` just **forces** that reconstruction pass without the offer (and any extra text scopes it: a subsystem, date range, suspected gap). The flag is now optional, not a mode switch.

## RETRO mode, reconstruct decisions made before Organism was installed

Use when a project predates the harvest loop: decisions live in code/git/old sessions but not in ADRs.

### R1. Build the candidate list from the map↔territory diff, never replay history linearly

- **Map:** the ADR index + each existing ADR's load-bearing claims; any orientation/state doc (`ORIENTATION.md`, audits), note these often *name* the gaps ("cut later", "user decision <date>", drift tables).
- **Territory, TWO sources, not just git:**
  1. `git log --reverse --date=short --pretty="%h %ad %s"` over the whole history.
  2. **In-repo decision docs**: `CLAUDE.md`, `AGENTS.md`, READMEs, design/spec docs. These *describe* architectural decisions in prose (the agent roster, the gate, the runtime model, key constraints) that often were never committed as ADRs. **This source is essential when git history is shallow**, a project git-tracked long after it began (e.g. everything in one "Initial commit", or data older than the first commit) has its real decisions living in the docs, not the diff. Check `git log --reverse | head -1` against the project's apparent age; if git starts late, lean on the docs and say so in Provenance (fidelity is doc-derived, not git-derived).
- **Candidates = decision-shaped changes the map doesn't cover**: from *either* territory source. Apply the project's own "what gets an ADR" bar if its ADR README states one; else the job-1 definition (choice + rationale + consequence; structure/protocol/dependency/scope cuts; anything a future reader would reverse-engineer from a diff or a doc). High-yield patterns: (i) a commit that contradicts an existing ADR's claim; (ii) a scope decision recorded only in a working doc; (iii) **an architectural choice stated as fact in `CLAUDE.md`/README with no backing ADR.**
- Routine impl/fix/content commits are NOT candidates. Bound the pass: if the diff yields nothing, say so and stop, never invent decisions.

### R2. Source ladder per candidate (cite the best layer you can reach)

1. **In-repo contemporaneous docs** (sequence-plan, design notes), highest fidelity; only the ADR *framing* is retroactive.
2. **Raw session transcripts**: `~/.claude/projects/<root with "/"→"-">/*.jsonl`. User verbatim = decision-grade. ⚠️ ~30-day rolling retention, note in the report when the window has already eaten candidate-era sessions. ⚠️ mtime = *last* write: resumed sessions hide early days inside later-mtime files, so locate originating sessions by `grep -l` for commit-message strings/keywords, never by date listing.
3. **Turn mirror** (vault `_claude-live/sessions/`, if present), durable and synced across machines: every prompt, every reply text block and every tool call (tool inputs dropped, outputs clipped). Once raw transcripts have expired, this is the decision-grade source.
4. **Commit messages**: rationale composed at implementation time (post-hoc; label it so).
5. **Inference from code**: lowest; mark explicitly as inferred.

**Mining technique:** transcripts are megabytes of one-line JSON, never Read them whole. `grep -l` to find files, then python3 to print (line-index, role, ~1500 chars) around keyword matches; pull surrounding events by index for the deliberation. Fan out one read-only subagent (Task/Explore) per candidate, in parallel, returning: user's verbatim words, stated reasons, alternatives discussed (or "none recorded"), session+line range, confidence.

### R3. Write reconstructed ADRs, provenance is the contract

Conform to the existing template (1c) and continue numbering, with these retro-specific additions:

- **Date:** the ORIGINAL decision date, plus `· recorded <today>`.
- **A `Provenance:` line in the header block** stating: reconstructed <today>, the sources, and the fidelity split, what is user-verbatim, what is post-hoc (commit-message-only), what is inferred. If the session shows a terse directive with no deliberation, the Alternatives section says "None recorded", **a reconstruction must never invent a deliberation that didn't happen.**
- **Contradicted existing ADRs get a minimal forward-pointer** appended to their status line ("<claim> amended by ADR-NNNN: <one clause>"), never rewrite their body (append-only, same as supersession).
- **Index entries** are marked `accepted (reconstructed)`, and the index gains a one-line legend explaining the marker (recorded retroactively from git + transcripts; decision dates are original).
- **Link the implementing commit(s) back to the ADR**: this is retro-only and load-bearing. A reconstructed ADR postdates its implementing commit, so that commit can *never* name it: neither the forward enforcer nor step-1.5 `backfill` can form the link (both need the commit to mention an ADR that didn't exist yet). For each implementing/provenance commit `<hash>` you cited, run `organism adr-link link <hash> <N>` (N = the new ADR number; the linker writes a non-destructive `git notes --ref=adr` note on that historical commit). Step 1.5's `backlinks write` (which runs after) then lists those commits under the ADR as *implements*. Skip if `organism adr-link` is not available.

### R4. Gate, commit, report

1f-bis (user ratifies the draft) and 1g (surgical commit) apply as-is; a sensible message: `docs(adr): retroactive realign: reconstruct NNNN..MMMM from git + session transcripts`, with the same **`ADR: none`** trailer as 1g (a retro recording commit is not an implementation either, the real ADR↔commit links come from the R3 `link` calls to the historical implementing commits, not from this commit). **After the reconstruction commits, set the retro checkpoint** (`organism adr-link retro-checkpoint set`) so future handoffs stop offering retro, this pass dispositioned the backlog; step 1.5's commit includes the marker. Then step 1.5 (link refresh) and job 2 run normally. In the final report, state coverage honestly: which candidates were reconstructed at which fidelity, which were skipped and why, and whether transcript retention has already cost anything (that urgency is the reason to run retro sooner, not later, on other projects that predate Organism).

## 0. Locate the project root + check the decision links

Run `pwd`, and `git rev-parse --show-toplevel` if this is a git repo. That directory is `<root>`. Everything below is relative to `<root>`, never a subdirectory's root, never `$HOME`.

Then, if `organism adr-link` is available, run `organism adr-link state` once, including in a non-git root, which the link tool classifies honestly (it emits a fourth value, `no_git`, for that case). Its `key=value` output drives the auto-detection from the intro:
- `decision_repo=no` → not a git repo AND no ADRs: truly N/A, skip all ADR/linkage steps (1a–1.5).
- `decision_repo=no_git` → **ADRs exist but this isn't a git repo** (e.g. a plain synced directory). The records are still maintainable: run the harvest normally (1a–1f-bis, write/update ADRs, index, MOC, ratify), but skip every git-touching step, 0.5, the 1g commit, 1g-bis, and 1.5 (linkage is git-native; there are no commits to link to). Job 2 skips its git block as usual.
- `decision_repo=greenfield` → a git repo with **no ADR dir yet**. This is NOT "skip", still harvest this session's decisions; step 1a will *create* the ADR dir on first use. Honor its `dirty_work` for step 0.5 exactly as a normal repo.
- `decision_repo=yes` → full flow.
- `retro_backlog=yes` (and `retro_checkpoint=none`) → make the one-line retro offer; `uncovered=N>0` → note in HANDOFF.md's open threads; `dirty_work=N>0` → do step 0.5 next.

The user is shown only conclusions, never the raw keys.

## 0.4. Keep the brief out of git, git repos only

`HANDOFF.md` is local state and must never be committed. Before anything is staged (step 0.5's bulk add would otherwise sweep in an untracked brief):

1. If `git check-ignore -q HANDOFF.md` fails, append the line `HANDOFF.md` to the root `.gitignore`, creating the file if needed.
2. If `HANDOFF.md` is tracked (`git ls-files --error-unmatch HANDOFF.md` succeeds), untrack it with `git rm --cached -q HANDOFF.md`. The file stays on disk.
3. If 1 or 2 changed anything, commit just that: `git add .gitignore`, then `git commit -m "chore: keep HANDOFF.md out of git" -m "ADR: none"`, but only when nothing else is staged (`git diff --cached --name-only` lists only `.gitignore` and `HANDOFF.md`). Otherwise leave the change for step 0.5's commit.

This happens once per repo; later runs find the brief already ignored and skip it. It asks nothing.

## 0.5. Commit outstanding implementation FIRST, code precedes the ADR

**The decision record must land in git AFTER the work it describes.** Handoff's ADR commit (1g) is surgical, it stages only `CLAUDE.md` + the ADR dir, so any implementation left uncommitted ends up committed *after* the ADR, inverting cause and effect (a handoff once committed the ADR five minutes before its four implementation commits). Prevent that here, **before writing any ADR file** (once 1d writes ADRs, a bulk `git add` would sweep them in, so this must run first).

**Three starting states, all handled, recognize which one you're in before acting:**

- **Dirty tree** (`dirty_work>0`), uncommitted implementation exists. The numbered
  procedure below commits it automatically (behind the byproduct + safety gates), code
  before record. This is the automatic-commit path: the user never has to remember
  a manual commit before running handoff.
- **Clean tree, but the user committed manually during the session**: nothing to
  commit here, and that is correct: **never re-commit, amend, squash, or rewrite the
  user's own commits.** Their commits are the implementation record; the ADR↔commit
  join happens later without touching them, 1g-bis `link-session` links every
  untrailered work-commit since the last handoff to the new ADR(s), and step 1.5
  `backfill` catches any the user already trailered with `ADR: <n>` (self-declared
  commits are linked to their OWN ADR and skipped by link-session, no blanket links).
- **Mixed**: some work committed manually, some still dirty. Commit only the dirty
  remainder via the procedure below; the manual commits are handled exactly as above.

In every state the ORDER is invariant: implementation commits (whoever made them) sit
before the ADR recording commit (1g), and the links are written after both exist.
Skip this whole step if not a git repo. If `dirty_work>0`:

1. **Review** `git status --short`, this is the session's uncommitted work (handoff has written nothing yet).
2. **Byproduct check, gitignore generated data, don't commit it.** Run `organism adr-link ignore-scan`. It lists directories of likely *generated* files (dated daily outputs, or `logs/cache/build/...` dir names) as `<dir>  <count>  <reason>`. For each one the user confirms is byproduct (not source they want versioned): add the dir to `.gitignore`, untrack anything already tracked (`git rm -r --cached <dir>`), and commit that as a **separate chore** *before* the implementation commit, `git commit -m "chore: stop tracking generated <dir>" -m "ADR: none"`. This is the gap that let one project carry 132 tracked daily-data files into git; catching it here keeps data out of the bulk commit. Empty output → nothing to do.
3. **Safety gate, never blind-commit danger.** Scan the remaining list for things that must not be committed: untracked secrets (`.env`, `*.key`, `*.pem`, token/credential files), large binaries or build artifacts not covered by `.gitignore`, or files plainly unrelated to this session. **A secret stops the run:** commit nothing, write no record and no brief, and report each secret's path (never its contents) so the user can remove or ignore it and run `/handoff` again. Large binaries, build artifacts and unrelated files are left out of the commit (stage with `git add -A -- . ':(exclude)<path>'` for each) and listed in the 1f-bis draft. Ask nothing here: the command's only questions are the retro offer (step 0) and the byproduct check (step 2 above).
4. **Author a real message** from session context, a conventional-commit summary (subject + body bullets if several features), never a generic "wip".
5. **Commit it as ONE implementation commit, before the ADR:**
   ```
   git add -A          # after the safety gate; add ':(exclude)<path>' for each file it left out
   git commit -m "<authored summary>" -m "ADR: none"
   ```
   `ADR: none` because the decision's number is minted later (step 1); step 1g-bis (`link-session`) connects this commit to the new ADR(s) afterward, and the global gentle hook never blocks. You MAY instead make one commit per decision if the file→decision mapping is obvious and clean, but never block on slicing; one honest bulk commit is the floor.
6. The tree is now clean of implementation. Step 1 harvests the decisions about this just-committed work; 1g commits the ADR **after** it; 1g-bis links them. Correct order, restored.

## 1. Harvest decisions

Scan THIS conversation for genuine **architectural / technical decisions** made this session, a choice with a rationale and a consequence: format/protocol/dependency/structure picks, "chose X over Y because Z", a trade-off deliberately accepted. Also treat anything in `$ARGUMENTS` as a candidate. **Ignore** routine edits, bug fixes, and discussion that never resolved. **If nothing qualifies, skip ALL of step 1**, never invent decisions, never create empty dirs/files.

**Scope test.** A candidate qualifies only if it is about *this* project, the repo at `<root>`: its code, tooling, data, what it commits, runs or publishes. Sessions wander: a side project's design, a personal errand, a tool choice for some other repo. Those decisions are real, but they belong elsewhere, and recording them here puts another project's (or the user's private) context into this project's committed `<adrdir>`. Do **not** write an ADR for them. List them in the 1f-bis draft under **"Decided elsewhere, not recorded here"**, one line each: the decision and the project it belongs to, so the user can run `/handoff` there. When unsure whether a decision is this project's, it is not: do less. (Precedent: decisions about a separate service were harvested into the directory the session happened to start in and had to be moved later, leaving stubs and reserved numbers behind.)

### 1a. Find where ADRs live, RESPECT the project's existing structure, don't impose one

Resolve the ADR directory in this priority (first hit wins):

1. A `<root>/.adr-dir` file exists → its trimmed contents are the ADR directory path (adr-tools convention).
2. A log4brains config (`<root>/.log4brains.yml` or `log4brains.yml`) exists → use the `project.adrFolder` it declares.
3. An existing directory, first match of: `docs/adr/`, `docs/decisions/`, `docs/architecture/decisions/`, `doc/adr/`, `adr/`, `architecture/decisions/`.
4. None of the above (greenfield) → default to `docs/adr/`, and create it.

Use `ls`/`find` to check; don't assume. Remember the resolved path. `adr-link.sh` resolves the directory in this same order, so its links and counts use the folder this step picks.

### 1b. Detect whether this project is inside an Obsidian vault

Walk up from `<root>` toward `/` looking for an ancestor directory that contains a `.obsidian/` folder. If found → **VAULT mode**: use Obsidian-native output (`[[wikilinks]]`, `#adr/*` tags, a decisions MOC, `> [!info]` callouts). If not found → **PLAIN mode**: standard Markdown links, no tags/MOC. Steps marked **(vault)** apply only in VAULT mode; steps marked **(plain)** only otherwise.

### 1c. Match the existing convention (brownfield) or use the default (greenfield)

**If the ADR dir already contains records:** conform, never introduce a second style.
- Read the highest-numbered existing ADR to learn (i) the **filename pattern** and (ii) the **section template**. New ADRs continue the numbering (highest + 1, same zero-padding), mirror that filename pattern and template. Do NOT add frontmatter/tags the existing records don't use, even in VAULT mode.
- **Never renumber, rewrite, or delete an existing ADR** (only a status change for supersession is allowed).

**If the dir is empty/new (greenfield):** filename `NNNN-kebab-title.md` starting at `0001`.
- **(plain)** template:
  ```
  # NNNN. <Title>

  - Status: accepted
  - Date: YYYY-MM-DD

  ## Context
  <what forced the decision, problem, constraints, forces>
  ## Decision
  <what we chose, stated plainly>
  ## Consequences
  <what it costs and enables; the trade-off accepted>
  ## Alternatives considered
  <options weighed and why they lost, omit this section if none>
  ```
- **(vault)** same body, but lead with YAML frontmatter and drop the `- Status:`/`- Date:` lines:
  ```
  ---
  tags: [adr, adr/accepted]
  date: YYYY-MM-DD
  ---
  # NNNN. <Title>

  ## Context
  …
  ## Decision
  …
  ## Consequences
  …
  ## Alternatives considered
  … (omit if none)
  ```

### 1d. Write one ADR per new decision

Create `<adrdir>/<NNNN>-<kebab-title>.md`, filled from the session (real context, not boilerplate). Status accepted, today's date.

**Privacy rule.** A record is committed, and a committed file can be pushed or made public later. Write the *situation*, never the *identifiers*: no account, customer, order or case numbers; no addresses, phone numbers or e-mail addresses; no money figures tied to a person; no names of third parties; no credentials, tokens or keys. Say "a prepaid mobile contract" or "the landlord thread", and leave the specifics to `HANDOFF.md` or the session mirror, which never enter git. Quote the user verbatim in `provenance` only when the quote itself carries none of the above. The same rule binds the `CLAUDE.md` index line. Step 1g checks every record against the user's Confidential Terms list before it commits; this rule covers what no list can enumerate.

**Supersession** (append-only, never delete the old one):
- Set the old ADR's status to superseded.
- **(vault)** link both ways so Obsidian backlinks form: the new ADR's body says `Supersedes [[OLD-NNNN-kebab-title]]`; the old ADR gets `Superseded by [[NEW-NNNN-kebab-title]]` and its frontmatter tag flips to `adr/superseded`.
- **(plain)** the old ADR's `- Status:` line becomes `superseded by NNNN`.

### 1e. Index it in CLAUDE.md (the always-loaded summary)

- **If `CLAUDE.md` doesn't exist**, create it from this skeleton (do NOT fabricate project facts, leave the TODO):
  ```
  # <project name>

  <!-- TODO: describe what this is + how to run / build / test. Fill from a verified
       read of the repo, not from memory, a wrong always-loaded CLAUDE.md is worse
       than an incomplete one. -->

  ## Decisions

  > Decision index, newest first. Full records are the linked ADR notes.
  ```
- **If `CLAUDE.md` exists without a `## Decisions` section**, append that header + blockquote; touch nothing else.
- For each new decision, prepend under the blockquote, newest first, sharing its ADR's number:
  - **(vault)** `- **[[NNNN-kebab-title|DEC-NNNN]]** · YYYY-MM-DD · #adr/accepted — <decision, one line>. _Why:_ <rationale>.`
  - **(plain)** `- **[DEC-NNNN](<relative/path/to/NNNN-title.md>) · YYYY-MM-DD · accepted** — <decision, one line>. _Why:_ <rationale>.`
  - The `[[wikilink]]` target is the ADR note's **basename without extension** (Obsidian resolves it).
- Mark superseded index entries `superseded by [[NEW…|DEC-NNNN]]` (vault) / `superseded by DEC-NNNN` (plain); never delete an index line.
- **Keep the index bounded:** if it would exceed ~20 entries, keep the 20 most recent and end with `_Older decisions: see the [[_decisions-moc|decisions MOC]]_` (vault) / `_Older decisions: see <adrdir>._` (plain). Touch nothing in `CLAUDE.md` outside `## Decisions`.

### 1f. (vault) Maintain the decisions MOC

Regenerate `<adrdir>/_decisions-moc.md` from the FULL set of ADRs in the dir (this file is generated and owned by this command, overwrite it freely; never touch any other index/README the project authored). Content:
```
---
tags: [adr, moc]
---
# Decisions — <project>

> [!info] Map of Content for this project's architecture decisions. Auto-maintained by `/handoff`; full records are the linked ADR notes in `<adrdir>/`.

## Accepted
- [[NNNN-kebab-title|DEC-NNNN]] — <one-line decision> · YYYY-MM-DD
… (newest first)

## Superseded
- [[NNNN-kebab-title|DEC-NNNN]] — <one-line> · superseded by [[MMMM-…|DEC-MMMM]]

## Proposed
… (omit any section that has no entries)
```

### 1f-bis. Harvest draft: shown, not gated (invoking /handoff IS the ratification)

*(Running the command is itself the deliberate act of approval: a second "approve?" prompt would be one judgment more than the user needs to spend.)*

After writing the records (1d–1f), present a concise **harvest draft**, transparency stays even though the pause goes:
- each new/changed **ADR**: id, title, the one-line decision, and status (accepted/proposed/superseded);
- the **`CLAUDE.md` Decisions-index** lines added or changed;
- **Decided elsewhere, not recorded here**: the step-1 scope test's rejects, one line each: the decision and the project it belongs to. Omit the heading when there are none.

Then **proceed directly to 1g**, do not ask for approval. The user ratified by choosing to run `/handoff`; the draft summary is for reading, and the remedy for a wrong record is the standing one: edit or supersede, then the next handoff commits the correction. If the user interrupts or objects mid-run, stop before 1g and leave the drafts on disk with the "awaiting revision" note in HANDOFF.md's open threads.

This remains the *semantic* checkpoint (the decisions are surfaced legibly at the moment they become durable). It composes with any repo-level commit hook the repo opted into.

### 1g. Commit the durable records, git repos only

The ADRs + `CLAUDE.md` index are durable, shared knowledge, so this command commits them itself (so you never have a stray manual commit step). `HANDOFF.md` is gitignored and stays local, it is never part of this commit. After the 1f-bis draft is shown, and after writing 1d–1f:

- **Confidential-term check first.** Before staging anything, run `organism guard-scan <each ADR file written or changed in 1d> CLAUDE.md`. It reads the user's Confidential Terms list (the same list the publish guard uses, with the same per-repo exemptions) and exits 2 naming the term and the file on a hit. On a hit **do not commit**: remove or rephrase the term in the record (1d says how), re-run the scan, then commit. If the term is this repo's own codename, the per-repo exemption file is the fix, not the record. If the scan reports an empty list, say so in the handoff report, the check is only as good as the list. Hook or node missing: report it and continue (the scan fails open; the guard itself blocks while terms are armed).
- **Skip the commit entirely if** this isn't a git repo, or job 1 wrote/changed nothing.
- Commit ONLY the durable doc paths, using the pathspec form so other staged/unstaged changes are left untouched, never `git add .`/`-A`, never code, never `HANDOFF.md`:
  ```
  git commit -m "<message>" -- CLAUDE.md <adrdir>
  ```
- **Message**: conventional and specific to what actually changed:
  - one new: `docs(adr): record DEC-0003 word-family grouping`
  - several: `docs(adr): record DEC-0003..0004 + index`
  - supersession: `docs(adr): DEC-0005 supersedes DEC-0002`
- **Append an `ADR: none` trailer** so the commit passes any forward-link enforcer the repo opted into (`adr-link.sh`'s commit-msg hook rejects a commit with no `ADR:` trailer). Use **`none`, NOT the decision numbers**: this is a *recording* commit (it writes the ADR file), not an implementation. Trailing `ADR: <N>` here would manufacture a tautological self-link, the commit that wrote ADR-N pointing at ADR-N, which pollutes ADR-N's implementation set and can mask a decided-but-unimplemented ADR. A decision's real links come from its *implementing* commits (forward enforcer trailers, or step-1.5 `backfill`, or R3's `link` in retro), never from the commit that records it.
  ```
  git commit -m "docs(adr): record DEC-0003..0004 + index" -m "ADR: none" -- CLAUDE.md <adrdir>
  ```
- Commit on the **current branch**, never create or switch branches. **Never `git push` here;** the only push is step 1.6, and only when the note asks for one.
- If the commit fails (nothing to commit, detached HEAD, etc.), report it briefly and continue to job 2, a git error must never abort the handoff.

### 1g-bis. Link this session's work to the new ADRs, FORWARD mode only

*(Skip in RETRO mode, retro links specific historical commits in R3 instead. Skip if step 1 wrote no new ADRs, or `organism adr-link` is not available.)*

A decision harvested at session end gets its ADR number only now, so the commits that implemented it (made earlier this session, before the number existed) couldn't carry an `ADR:` trailer and step-1.5 `backfill` won't catch them. Link them explicitly, for each new ADR number `N` written this run:
```
organism adr-link link-session --since "<session start>" <N>
```
This links every work commit since the last handoff to ADR-N, skipping doc/maintenance commits. Runs before step 1.5's `backlinks write` so the links render. **The links stay within the session:** only commits after the previous `docs(adr):` commit count, and only those no older than the later of `--since` and the last write of `HANDOFF.md` (normally the previous brief, since job 2 rewrites it only after this step). With neither, and no earlier record, the bound is the last 24 hours, so a first handoff in a repo with no records never ties years of history to one new record. **Pass `--since` when you know when this session started** (local time, `--since "YYYY-MM-DD HH:MM"`); otherwise drop the flag and let the other bounds apply. Options go before the ADR numbers. Commits older than the bound are never linked: the command says how many it left out and how to include them (`--all --since <date>`). Report that line briefly; never re-run with `--all` on your own to reach before this session, since older history is `--retro`'s job. **One exception:** `HANDOFF.md` can be written during the session too (an interrupted run's "awaiting revision" note from 1f-bis, a hand edit), and then it hides this session's earlier commits. When the command says `HANDOFF.md` was written after `--since` and this session wrote it, re-run once with `--all --since "<session start>"`, the line it prints. **Known coarseness (a deliberate choice):** if this run harvested several decisions, every session work-commit links to *each* of them, refine later by editing notes if a tighter mapping is wanted. Without this, a just-built ADR would render "Implemented by: none yet".

## 1.5. Refresh the ADR↔commit link index

Run this **whenever** `<root>` is a git repo, the resolved `<adrdir>` (from 1a) exists, AND `organism adr-link` is available, **independent of whether step 1 harvested anything** (new code commits this session may reference *existing* ADRs). Skip silently if any condition fails; a failure here never aborts the handoff.

1. `organism adr-link backfill`, promote this session's ADR/DEC-referencing commits into `git notes --ref=adr`. Non-destructive, idempotent, **local-only** (notes are never pushed by `/handoff`).
2. `organism adr-link backlinks write`, regenerate the `<!-- adr-links -->` reverse-index block in each ADR (commits per ADR, tagged records/implements), from trailers + notes.
3. **Commit only if** `git status --short -- <adrdir>` shows changes left by step 2 that step 1g didn't already commit:
   ```
   git commit -m "docs(adr): refresh ADR↔commit link index" -m "ADR: none" -- <adrdir>
   ```
   Same discipline as 1g (pathspec form, current branch, never `-A`, no push outside step 1.6; `ADR: none` keeps the enforcer happy on this mechanical commit). A git error here is reported and ignored.

These generated blocks are mechanical, **not** decisions, they do not go through the 1f-bis gate. When step 1g also ran, this is a small, separate follow-up commit (decision commit vs link-maintenance commit, the split is intended).

## 1.6. Push, only when the note asks for it, git repos only

Skip this step unless `$ARGUMENTS` explicitly asks for a push, for example "push", "and push" or "push to origin". A note that merely mentions pushing as a topic ("decide whether to push later") is not a request. When it is one, after 1g and 1.5:

- Run `git push` once, for the current branch, to the upstream it already tracks. Never `--force`, never another branch, never a remote the branch does not track.
- No upstream configured: do not pick one. Say the push was skipped and why.
- A rejected push or a publish-guard block is reported as it is, never retried with force or with the guard bypassed.
- Push the branch only. The link notes (`refs/notes/adr`) stay local, as before.

This is the only push `/handoff` ever makes. It asks nothing; outside bypass mode, Claude Code's own permission prompt still appears, because `git push` is deliberately not in this command's allowed tools.

## 2. Rewrite HANDOFF.md

Gather real state (use the output, never guess; if not a git repo, skip git and say so):
- `git status --short --branch`
- `git log -8 --oneline` (top line = HEAD)
- `git branch --show-current`

Read the existing `<root>/HANDOFF.md` (if present) to carry forward the standing backlog. Then overwrite `<root>/HANDOFF.md` wholesale with, in order:

1. **Header + git**: `# HANDOFF — <project>`, today's date, a short git block (HEAD hash + subject, branch, clean vs dirty with paths).
2. **Current status**: 2-4 sentences: what this session was about and the headline outcome.
3. **What's live**: working surfaces / capabilities; carry forward, update only what changed.
4. **Next / open threads**: concrete next steps + anything mid-flight, most-actionable first; PRESERVE the standing backlog. **Carry forward steps, not identifiers**: a next step says what to do and where the details are (a vault note, a mail subject), never the account, case or order number, address, phone or money figure itself. The brief is gitignored, but it is synced and injected into every session started in this directory.
5. **How to use this file**: short footer: regenerated by `/handoff` (which also commits the ADRs + `CLAUDE.md`); auto-injected at session start; this file is gitignored local state; decisions live as committed ADRs indexed from `CLAUDE.md`'s `## Decisions` (and, in a vault, the `[[_decisions-moc]]`).

## Rules

- If `$ARGUMENTS` is non-empty, treat it as a focus note: weave it into Current status / Next threads AND consider it for decision harvest; call it out.
- Be exact. Use real git output; never invent commit hashes, branches, ports, paths, decisions, or project facts.
- Respect existing structure over the defaults above, detect, then conform. When unsure whether something is a real decision or a real existing convention, do less, not more.
- The auto-commit (1g) is surgical: only `CLAUDE.md` + the ADR dir, only via the pathspec form, on the current branch. Never `git add -A`/`.`, never branch, never push outside step 1.6, never commit `HANDOFF.md`. Outstanding code is committed only by step 0.5, before any ADR; step 1g never sweeps code into the records commit.
- Keep HANDOFF.md concise (~one screen) and index entries terse.
- Scope and privacy: a decision that belongs to another project is listed, never recorded (step 1); identifiers never enter a record (1d), the index line, or the brief (2); the records are scanned against the Confidential Terms list before they are committed (1g).
