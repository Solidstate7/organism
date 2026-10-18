---
name: handoff
description: Harvest decisions into ADRs (Obsidian-native in a vault) + a CLAUDE.md index, commit them, then rewrite HANDOFF.md. --retro reconstructs PRE-harness decisions from git + session transcripts.
argument-hint: [--retro] [optional focus note]
disable-model-invocation: true
allowed-tools: Read, Write, Edit, Task, Grep, Bash(pwd), Bash(ls:*), Bash(find:*), Bash(cat:*), Bash(wc:*), Bash(grep:*), Bash(python3:*), Bash(git status:*), Bash(git log:*), Bash(git branch:*), Bash(git rev-parse:*), Bash(git add:*), Bash(git commit:*), Bash(git check-ignore:*), Bash(git ls-files:*), Bash(git diff:*), Bash(git rm --cached:*), Bash(git rm -r --cached:*), Bash(organism adr-link:*), Bash(organism adr-index:*), Bash(organism guard-scan:*), Bash(organism handoff-state:*), Bash(~/.local/bin/organism adr-link:*), Bash(~/.local/bin/organism adr-index:*), Bash(~/.local/bin/organism guard-scan:*), Bash(~/.local/bin/organism handoff-state:*)
---

Two jobs, in order, both at the CURRENT project's root:

1. **Decisions → ADRs + `CLAUDE.md` index, committed.** Record this session's architectural decisions as durable records, indexed from the always-loaded `CLAUDE.md`, and commit just those files.
2. **State → `HANDOFF.md`.** Rewrite the living session brief (gitignored, local-only).

Job 1 first, then job 2. Only steps 0.4, 0.5, 1g, 1g-bis, 1.5, 1.6 and the retro links touch git; 1.6 (the push) runs only when the user's note asks for one. The loop itself is disk-based and never needs a commit.

Helper commands are `organism <verb>` (`adr-link`, `adr-index`, `handoff-state`, `guard-scan`); if `organism` is not on PATH, use `~/.local/bin/organism`. The command asks at most two questions: the retro offer (step 0) and the byproduct check (step 0.5). Running `/handoff` is the approval; never ask for one.

## 0. Root, state, mode

Run `organism handoff-state`. Its `root` is `<root>`: the git top level, else the vault root (the configured vault, or the nearest folder above the start folder holding `.obsidian` or `docs/adr`), and `root_kind` says which (`git`, `vault`, `none`). `root_kind=none` means neither: say "no repository and no vault above this folder; /handoff writes nothing here" and stop. Everything below is relative to `<root>`, never a subdirectory, never `$HOME`.

Run `organism adr-link state` (it classifies a non-git root honestly) and act on its keys:

- `decision_repo=no`: not a git repo and no ADRs. Skip steps 1a to 1.5.
- `decision_repo=no_git`: ADRs exist, no git (a synced vault). Harvest normally (1a to 1f-bis: records, index, MOC); skip every git step (0.4, 0.5, 1g, 1g-bis, 1.5, 1.6).
- `decision_repo=greenfield`: a git repo with no ADR dir yet. Harvest; 1a creates the dir. Honour `dirty_work` in 0.5.
- `decision_repo=yes`: the full flow.
- `retro_backlog=yes` with `retro_checkpoint=none`: history holds decision-shaped work no record covers. Offer once, in one line: "this project has ~N unrecorded historical decisions; reconstruct them now? (y/N)". On yes, or when `$ARGUMENTS` contains `--retro` (any extra text scopes it), Read `RETRO.md` next to this SKILL.md (the skill's base directory; from a clone, `~/.organism/skills/handoff/RETRO.md`) and run R1 to R4 before job 2. On no, or when R1 finds nothing, `organism adr-link retro-checkpoint set` so the offer stops (step 1.5 commits the marker). A set checkpoint means silence: retro is a one-time catch-up, never a recurring prompt.
- `uncovered=N>0`: note it in the brief's open threads. `dirty_work=N>0`: step 0.5 runs.

Show the user conclusions, never the raw keys.

## 0.4. Keep the brief out of git (git repos only)

Before anything is staged: if `git check-ignore -q HANDOFF.md` fails, append `HANDOFF.md` to the root `.gitignore` (create it if needed); if `git ls-files --error-unmatch HANDOFF.md` succeeds, `git rm --cached -q HANDOFF.md` (the file stays on disk). If either changed something and nothing else is staged, commit just that: `git add .gitignore`, then `git commit -m "chore: keep HANDOFF.md out of git" -m "ADR: none"`; otherwise leave it for 0.5's commit. Once per repo; asks nothing.

## 0.5. Commit outstanding implementation FIRST (git repos only)

The record must land in git AFTER the work it describes. 1g stages only `CLAUDE.md` and the ADR dir, so implementation left uncommitted would follow its own record. Three starting states:

- **Dirty tree** (`dirty_work>0`): the procedure below commits it, code before record. The user never has to remember a manual commit.
- **Clean tree, the user committed during the session**: nothing to commit. **Never re-commit, amend, squash or rewrite the user's commits.** 1g-bis links the untrailered work commits since the last handoff to the new records; 1.5 `backfill` catches the ones already trailered `ADR: <n>` (those link to their own record, and link-session skips them).
- **Mixed**: commit only the dirty remainder; the manual commits are handled as above.

If `dirty_work>0`:

1. `git status --short`: the session's uncommitted work (handoff has written nothing yet).
2. **Byproduct check.** `organism adr-link ignore-scan` lists directories of likely generated files as `<dir>  <count>  <reason>`. For each one the user confirms is byproduct: add it to `.gitignore`, `git rm -r --cached <dir>` if tracked, and commit that separately first: `git commit -m "chore: stop tracking generated <dir>" -m "ADR: none"`. (One project once carried 132 tracked daily-data files.) Empty output: nothing to do.
3. **Safety gate.** Scan the list for untracked secrets (`.env`, `*.key`, `*.pem`, token or credential files), large binaries or build artifacts outside `.gitignore`, and files plainly unrelated to this session. **A secret stops the run:** commit nothing, write no record and no brief, and report each secret's path (never its contents) so the user can remove or ignore it and run `/handoff` again. Binaries, artifacts and unrelated files are left out (`git add -A -- . ':(exclude)<path>'` for each) and listed in the 1f-bis draft. Ask nothing here.
4. Author a real conventional-commit message from the session (subject, body bullets if several features); never a generic "wip".
5. One implementation commit, before the record: `git add -A` (with the exclusions), then `git commit -m "<summary>" -m "ADR: none"`. `ADR: none` because the number is minted in step 1; 1g-bis links this commit afterwards. One commit per decision is fine when the mapping is obvious, but one honest bulk commit is the floor; never block on slicing.

## 1. Harvest decisions

Scan THIS conversation for genuine architectural or technical decisions made this session: a choice with a rationale and a consequence (a format, protocol, dependency or structure pick, "X over Y because Z", a trade-off deliberately accepted). `$ARGUMENTS` is a candidate too. Ignore routine edits, bug fixes and discussion that never resolved. **If nothing qualifies, skip all of step 1**: never invent decisions, never create empty dirs or files.

**Scope test.** A candidate qualifies only if it is about *this* project, the repo at `<root>`: its code, tooling, data, what it commits, runs or publishes. A side project's design, a personal errand, a tool choice for another repo are real decisions that belong elsewhere; recording them here puts another project's (or the user's private) context into this repo's committed records. Do not write a record for them: list them in the 1f-bis draft under "Decided elsewhere, not recorded here", one line each (the decision and its project), so the user can run `/handoff` there. When unsure whether a decision is this project's, it is not: do less. (Precedent: decisions about a separate service were harvested into the directory the session started in and had to be moved later, leaving stubs and reserved numbers behind.)

### 1a to 1c. Where the records live, and in what style

Run `organism adr-index next`. It prints `adr_dir` (resolved in this order: `.adr-dir`, a log4brains config, the first existing usual folder, else `docs/adr`), `exists`, `count`, `next` with its padding, `pattern`, `style` (vault, plain, madr or unknown), `latest` (the highest record) and `vault` (an `.obsidian` ancestor, or the configured vault root). Respect the project's structure; never impose a second one.

- **Records exist:** Read `latest` and conform: the same filename pattern, section template and numbering; no frontmatter or tags the existing records don't use, even in a vault. **Never renumber, rewrite or delete an existing record** (only a status change for supersession).
- **No records yet:** `NNNN-kebab-title.md` from `next`, with the default template: `# NNNN. <Title>`, then `## Context` (what forced the decision), `## Decision` (stated plainly), `## Consequences` (what it costs and enables), `## Alternatives considered` (omit if none). Plain style adds `- Status: accepted` and `- Date: YYYY-MM-DD` under the title; vault style instead leads with frontmatter `tags: [adr, adr/accepted]` and `date: YYYY-MM-DD`.
- `vault=yes` means VAULT mode: `[[wikilinks]]`, `#adr/*` tags, the decisions MOC, `> [!info]` callouts. Otherwise PLAIN mode: standard links, no tags, no MOC. Steps marked (vault) or (plain) apply accordingly.

### 1d. Write one record per new decision

`<adrdir>/<NNNN>-<kebab-title>.md`, filled from the session (real context, not boilerplate), status accepted, today's date.

**Privacy rule.** A record is committed, and a committed file can be pushed or made public later. Write the *situation*, never the *identifiers*: no account, customer, order or case numbers; no addresses, phone numbers or e-mail addresses; no money figures tied to a person; no names of third parties; no credentials, tokens or keys. Say "a prepaid mobile contract" or "the landlord thread"; the specifics stay in `HANDOFF.md` or the session mirror, which never enter git. Quote the user in `provenance` only when the quote carries none of these. The same rule binds the index line. 1g scans the records against the Confidential Terms list; this rule covers what no list can enumerate.

**Supersession** is append-only: set the old record's status to superseded, never delete it. (vault) link both ways so backlinks form: the new body says `Supersedes [[OLD]]`, the old gets `Superseded by [[NEW]]` and its tag flips to `adr/superseded`. (plain) the old `- Status:` line becomes `superseded by NNNN`.

### 1e. Index it in CLAUDE.md

- No `CLAUDE.md`: create it with `# <project name>`, a `<!-- TODO: describe what this is and how to run, build and test it; fill from a verified read of the repo, never from memory -->` comment, then `## Decisions` and the blockquote `> Decision index, newest first. Full records are the linked ADR notes.`
- `CLAUDE.md` without `## Decisions`: append that header and blockquote; touch nothing else.
- Prepend one line per new decision under the blockquote, newest first, in the format the existing lines use. Default (vault): `- **[[NNNN-kebab-title|DEC-NNNN]]** · YYYY-MM-DD · #adr/accepted — <decision, one line>. _Why:_ <rationale>.` Default (plain): `- **[DEC-NNNN](<relative path>) · YYYY-MM-DD · accepted** — <decision, one line>. _Why:_ <rationale>.` The wikilink target is the record's basename without extension.
- A superseded entry gets `superseded by [[NEW|DEC-NNNN]]` (vault) or `superseded by DEC-NNNN` (plain); never delete an index line.
- Then `organism adr-index trim`: it keeps the 20 newest entries and adds the pointer to the rest. Touch nothing in `CLAUDE.md` outside `## Decisions`.

### 1f. (vault) The decisions MOC

`organism adr-index moc --write` regenerates `<adrdir>/_decisions-moc.md` from every record (Accepted, Superseded, Proposed; newest first), keeping an existing page's header and the lines it already has. The page is owned by this command; never touch any other index or README the project authored.

### 1f-bis. Harvest draft: shown, not gated

Running the command is the approval; a second "approve?" would be one judgement more than the user needs to spend. After writing 1d to 1f, show a concise draft: each new or changed record (id, title, one-line decision, status); the index lines added or changed; "Decided elsewhere, not recorded here" (omit when empty). Then go straight to 1g. A wrong record is fixed the standing way: edit or supersede, and the next handoff commits it. If the user interrupts or objects mid-run, stop before 1g, leave the drafts on disk and note "awaiting revision" in the brief's open threads.

### 1g. Commit the durable records (git repos only)

- **Confidential-term check first:** `organism guard-scan <each record written or changed in 1d> CLAUDE.md`. It reads the same list and per-repo exemptions the publish guard uses and exits 2 naming the term and the file. On a hit do not commit: rephrase per 1d, scan again. The repo's own codename belongs in the per-repo exemption file, not out of the record. An empty list: say so in the report. Hook or node missing: report it and continue (the scan fails open; the guard itself blocks while terms are armed).
- Skip the commit if job 1 wrote or changed nothing.
- `git commit -m "<message>" -m "ADR: none" -- CLAUDE.md <adrdir>`: the pathspec form only, never `git add .`/`-A`, never code, never `HANDOFF.md`, on the current branch, no push. Messages: `docs(adr): record DEC-0003 word-family grouping`, `docs(adr): record DEC-0003..0004 + index`, `docs(adr): DEC-0005 supersedes DEC-0002`. The trailer is `none`, not the numbers: this commit records the decision, it doesn't implement it; a self-link would pollute the record's implementation set and could mask a decided-but-unbuilt record.
- A failed commit (nothing to commit, detached HEAD) is reported briefly; job 2 still runs.

### 1g-bis. Link this session's work to the new records (forward only)

Skip in retro, when step 1 wrote nothing, or without `organism adr-link`. A decision gets its number only now, so the commits that implemented it carry no trailer and `backfill` can't see them. For each new number `N`: `organism adr-link link-session --since "<session start, local YYYY-MM-DD HH:MM>" <N>` (drop `--since` when unknown; options before the numbers). It links every work commit since the last handoff, bounded by the later of `--since` and the brief's last write (24 hours when there is no earlier record), skipping doc and maintenance commits. Report its line about older commits left out; never re-run with `--all` to reach before this session (that is retro's job). One exception: when it says `HANDOFF.md` was written after `--since` and this session wrote it (an interrupted run, a hand edit), re-run once with `--all --since "<session start>"`. Several decisions in one run link every work commit to each of them; refine the notes later if a tighter mapping is wanted.

## 1.5. Refresh the record↔commit link index

Whenever `<root>` is a git repo, `<adrdir>` exists and `organism adr-link` is available, even when step 1 harvested nothing (new commits may name existing records): `organism adr-link backfill` (promotes `ADR:`/`DEC-` references into `git notes --ref=adr`, local only, never pushed), then `organism adr-link backlinks write` (regenerates the `<!-- adr-links -->` block in each record). If `git status --short -- <adrdir>` shows changes 1g didn't commit: `git commit -m "docs(adr): refresh ADR↔commit link index" -m "ADR: none" -- <adrdir>`. Mechanical, not a decision, not gated. A git error here is reported and ignored.

## 1.6. Push, only when the note asks (git repos only)

Only when `$ARGUMENTS` explicitly asks ("push", "and push", "push to origin"; "decide whether to push later" is not a request), after 1g and 1.5: `git push` once, the current branch, to the upstream it already tracks. Never `--force`, never another branch or remote; no upstream: skip and say why. A rejected push or a publish-guard block is reported as it is, never retried with force or around the guard. The notes (`refs/notes/adr`) stay local. Outside bypass mode Claude Code's own prompt still appears: `git push` is deliberately not in this command's allowed tools.

## 2. Rewrite HANDOFF.md

Run `organism handoff-state` again, before writing (HEAD moved if 0.5 or 1g committed). It prints the facts the brief may state (branch, HEAD, upstream, ahead and behind, pushed or not, the dirty files, the last 8 commits, every hash the current brief names with whether it exists and is pushed, the sessions active since the brief was written, the decision-link state) and saves a copy of the current brief for step 6. Every hash, branch and pushed-or-not claim in the brief is copied from that output; a fact it doesn't show is not written. Outside a git repo it says so: write no git block. An action outside git (a page published, a mail sent, a deploy) has no script proof: write it as "as described by the session" unless its outcome was checked (the live page, the sent mail), and say which.

Read the existing brief to carry the standing backlog forward, then overwrite `<root>/HANDOFF.md` wholesale with, in order:

1. **Header and git**: `# HANDOFF — <project>`, today's date, a short git block from the state output (HEAD hash and subject, branch, upstream with ahead and behind, clean or dirty with paths).
2. **Current status**: 2 to 4 sentences: what this session was about and the headline outcome.
3. **What's live**: working surfaces and capabilities; carry forward, update only what changed.
4. **Next / open threads**: concrete next steps and anything mid-flight, most actionable first; preserve the standing backlog. **Steps, not identifiers**: say what to do and where the details are (a vault note, a mail subject), never the account, case or order number, address, phone or money figure itself. The brief is gitignored, but it is synced and injected into every session started in this directory.
5. **How to use this file**: regenerated by `/handoff` (which also commits the records and `CLAUDE.md`); injected at session start; gitignored local state; decisions live as committed records indexed from `CLAUDE.md`'s `## Decisions` (and, in a vault, `[[_decisions-moc]]`).
6. **A quiet session is said, not skipped**: when the state shows `commits_since_brief=0` and `dirty=0` and step 1 recorded nothing, the report says in one line that the session wrote and decided nothing. The brief is still written: the pointer and the status change, and a run the user started always leaves one.
7. **Check it**: run `organism handoff-state --check`. It names every hash the new brief cites that doesn't exist or that the brief calls pushed while the upstream lacks it, and lists the open threads the old brief had that the new one dropped. Fix a hash claim before the run ends (edit the brief, run the check again). A dropped thread is allowed when the item is done or moved: list each one in the final report with why it left.

## Rules

- `$ARGUMENTS` non-empty: a focus note. Weave it into Current status and Next threads, consider it for the harvest, and call it out.
- Be exact. Git facts come from `organism handoff-state` (step 2); never invent commit hashes, branches, ports, paths, decisions or project facts.
- Detect, then conform: existing structure beats the defaults above. When unsure whether something is a real decision or a real convention, do less, not more.
- 1g is surgical: only `CLAUDE.md` and the ADR dir, only the pathspec form, on the current branch. Never `git add -A`/`.`, never branch, never push outside 1.6, never commit `HANDOFF.md`. Code is committed only by 0.5, before any record; 1g never sweeps code into the records commit.
- Keep the brief about one screen and the index entries terse.
- Scope and privacy: a decision that belongs to another project is listed, never recorded (step 1); identifiers never enter a record (1d), the index line, or the brief (2); the records are scanned against the Confidential Terms list before they are committed (1g).
