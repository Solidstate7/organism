# Security policy

Organism runs inside every Claude Code session: its hooks see every Bash and
connector tool call, and it copies each session into a folder of notes that you may
sync to a cloud. A flaw here can leak what you work on, so reports are welcome.

## Reporting a vulnerability

Please report it privately through GitHub: open the repository's **Security** tab
and choose **Report a vulnerability**. Don't open a public issue for a security
problem.

Include what you found, how to reproduce it, and what an attacker or a mistake could
expose. You can expect a first reply within a week. Once a fix is released, the
advisory credits you unless you'd rather stay anonymous.

## What counts

- A secret or private text reaching the session notes despite redaction, incognito
  or a project's opt-out.
- A push, deploy or upload that the publish guard should block but lets through, for
  a term on the list.
- A hook that runs code it shouldn't, writes outside the files it documents, or
  breaks a Claude Code session.
- The uninstaller leaving Organism's hooks or jobs active.

Not in scope: deliberately disguising a term to get past the guard (the guard is a
safety net against accidents, not a sandbox; see `claude/hooks/guard-publish.spec.md`),
and problems in Claude Code itself, which go to Anthropic.

## Supported versions

Only the latest release receives fixes.
