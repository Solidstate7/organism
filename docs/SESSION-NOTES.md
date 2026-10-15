# Session notes

Organism copies every Claude Code session into your vault as it happens. The session
copy is written to be read, not searched, and is optimised for Obsidian. The
[README](../README.md#session-notes) lists the two files and when to open each; this page
shows what a session looks like inside them, and [what they contain](#what-the-notes-contain).

Each note starts with a header that names the session, its project and the machine it
ran on. Every turn below it has the same shape, so your eyes learn where to look:

````markdown
# Claude session `8967771b` — my-project

🖥️ *MacBook Air (MacBookAir10,1)*

<!-- project-dir: /Users/you/dev/my-project -->

---

### 🕐 2026-09-02 15:30:18

**🧑 You**

Explain the handoff command in detail

**🤖 Claude** · `fable-5-1`

I'll read the command file and its hooks first.

> [!note]- 🖥️ Bash · Locate the handoff command files
>
> ```
> (output only, clipped at 3,000 characters or 60 lines)
> ```
````

The 🖥️ line gives the machine's name and model. It matters when several Macs share one
vault through Obsidian Sync: every note says which machine the session ran on, and
`claude --resume` works only on that machine, because the raw transcripts do not sync.
The `project-dir` comment is hidden in Obsidian's reading view; it tells Organism which
project the session belongs to.

Four rules keep a long session readable:

- **Claude's prose is the spine.** It is kept in full and inline. Everything else folds.
- **Each tool call is one collapsed callout.** The title is the tool's icon, its name, and
  a one-line label: the command's description, the file path, or the search pattern. The
  body is the output only; the input is dropped. A failed call uses a `[!failure]-`
  callout, so errors stand out while the page is still folded.
- **Big pastes fold too.** A prompt over 40 lines collapses into a quote callout titled by
  what it was: an injected skill payload, or pasted context with its first heading and
  line count. What you actually asked stays visible above it.
- **Time and place are always there.** Every turn carries the machine's local time, the
  header names the machine, and a session run by a script rather than a person carries
  a `⚙️ headless` marker in the header.

In Obsidian's reading view the callouts start collapsed, so a session reads as a
conversation, and you expand only the tool call you care about. In any other editor the
same lines are ordinary blockquotes.

## What the notes contain

The notes hold your prompts and Claude's replies in full. For each tool call they hold
its title (the command's description, the command itself, the file path or the search
pattern) and its output, clipped at 3,000 characters or 60 lines. The rest of a tool's
input is not copied. `latest.md` and the hidden `.sessions.json` beside it also keep
each session's last prompt for the table.

Before anything is written, common secrets are replaced with `[redacted:<kind>]`: API
keys and tokens from Anthropic, OpenAI, GitHub, AWS, Slack, Telegram, Google, Stripe and
npm, JWTs, private key blocks, `Authorization` header values, passwords in URLs and in
`curl -u user:password`, and values given to names or flags that contain SECRET, TOKEN,
PASSWORD, API_KEY and the like (`API_KEY=...`, `--password ...`). Placeholders such as
`$TOKEN` or `<your-key>` are left alone. This is a best-effort list of patterns,
not a guarantee: a secret in a shape it does not know, or a password written as plain
prose, is copied as it is.

The notes live in your vault, so they go wherever the vault goes. If the vault syncs to
a cloud service (Obsidian Sync, iCloud, Dropbox, a git remote), the notes sync with it.

To keep a project out of the notes entirely, create an empty `.organism-no-notes` file
in its folder. Sessions started in that folder or anywhere below it get no note and no
row in the table. The file also works in any folder above the project, up to but not
including your home folder. To list folders in one place instead, set
`CLAUDE_NOTES_EXCLUDE` in `~/.claude/state/paths.env` to absolute paths separated by
spaces. `$HOME` works, and a path that contains a space goes in single quotes:

```sh
CLAUDE_NOTES_EXCLUDE="$HOME/clients '$HOME/Client Work'"
```

Notes written before you opt a project out stay until you delete them. To leave out
only part of a session, use `/incognito on` and `/incognito off`; `/incognito purge`
deletes what the current session has saved so far.
