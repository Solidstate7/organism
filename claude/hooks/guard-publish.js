#!/usr/bin/env node
'use strict';
// guard-publish.js: PreToolUse guard (primary surface: Bash; non-Bash matchers optional).
// Blocks publish-class commands/tool calls that would expose any term from the
// user's private "Confidential Terms" vault denylist (path via paths.env
// GUARD_PUBLISH_DENYLIST; ~/.claude/confidential-terms.txt is a retired fallback).
// Works under defaultMode:"bypassPermissions" because a hard block uses exit
// code 2, which the harness honors regardless of permission mode.
//
// Outcomes (authoritative decision table: guard-publish.spec.md):
//   confidential term in published content  -> stderr + alert.py push + exit 2 (HARD BLOCK)
//   publish-class, no term / EMPTY denylist -> exit 0, silent allow
//   not publish-class / unmatched tool      -> exit 0, silent (normal flow)
//   internal error, terms armed             -> stderr + local log + exit 2 (BLOCK: the guard
//                                              cannot vouch for a call it did not check)
//   internal error, empty denylist          -> stderr breadcrumb + exit 0 (nothing to protect)
//
// Invoked via guard-publish.sh, which does not start node at all while the list has no
// active term, and blocks when the list is armed but node is missing or crashes. An
// empty denylist is visible via --status ("armed:<N> source:<path>", for the reconcile probe).

const fs = require('fs');
const os = require('os');
const path = require('path');
const { execSync, execFileSync, spawn } = require('child_process');

// Time budget for the content scans. The hook itself is killed at 15 s (hooks/hooks.json),
// and before Claude Code 2.1.295 a killed hook let the command through, so the scans stop
// at 10 s and answer.
const START = Date.now();
const BUDGET_MS = Number(process.env.GUARD_PUBLISH_BUDGET_MS) || 10000;
class Incomplete extends Error {}   // a scan that could not finish: the command is blocked

// discovered python3 for the alert push (brew prefix varies by arch; system python3 last)
const PYBIN = ['/opt/homebrew/bin/python3', '/usr/local/bin/python3', '/usr/bin/python3']
  .find(p => { try { return fs.statSync(p).isFile(); } catch (_) { return false; } }) || 'python3';

// Denylist sources, in priority order:
//   1. $GUARD_PUBLISH_DENYLIST             (override, used by the test harness)
//   2. GUARD_PUBLISH_DENYLIST in paths.env (the vault page; the user edits it there)
//   3. legacy ~/.claude/confidential-terms.txt  (retired pointer; resilience only)
// The vault page path lives in the machine-local, gitignored paths.env, never hardcoded here.
// guard-publish.sh repeats this order and the line rules below in sh + awk, to skip
// node while the list is empty: change one, change the other.
function fromPathsEnv(key) {
  try {
    const ep = process.env.CLAUDE_PATHS_ENV || path.join(os.homedir(), '.claude', 'state', 'paths.env');
    for (const line of fs.readFileSync(ep, 'utf8').split('\n')) {
      const t = line.trim();
      if (!t || t.startsWith('#') || !t.includes('=')) continue;
      const i = t.indexOf('=');
      if (t.slice(0, i).trim() !== key) continue;
      let v = t.slice(i + 1).trim();
      const m = v.match(/^(["'])([\s\S]*?)\1/);   // quoted: take inside the quotes (ignore a trailing # comment)
      v = m ? m[2] : v.split('#')[0].trim();        // unquoted: drop a trailing comment
      return v.replace(/\$HOME/g, os.homedir()).replace(/^~(?=\/)/, os.homedir());
    }
  } catch (_) {}
  return null;
}

function loadTerms() {
  // Returns { terms, source }, source is the file actually read (for --status),
  // null when no source was readable.
  const sources = [
    process.env.GUARD_PUBLISH_DENYLIST,
    fromPathsEnv('GUARD_PUBLISH_DENYLIST'),
    path.join(os.homedir(), '.claude', 'confidential-terms.txt'),
  ].filter(Boolean);
  let text = null, source = null;
  for (const p of sources) {
    try { text = fs.readFileSync(p, 'utf8'); source = p; break; } catch (_) {}
  }
  if (text == null) return { terms: [], source: null };
  // If the page carries the marked block, read ONLY inside it (so the page's
  // own prose/frontmatter can never become an accidental "term"); else (the
  // legacy flat .txt) read the whole file.
  const b = text.indexOf('CONFIDENTIAL-TERMS:BEGIN');
  const e = text.indexOf('CONFIDENTIAL-TERMS:END');
  let region = text;
  if (b !== -1 && e !== -1 && e > b) {
    const from = text.indexOf('\n', b);
    region = from !== -1 ? text.slice(from + 1, e) : '';
  }
  let terms = [];
  for (let line of region.split('\n')) {
    line = line.trim();
    if (!line || line.startsWith('```') || line.startsWith('#') || line.startsWith('<!--')) continue;
    line = line.replace(/^[-*+]\s+/, '').replace(/`/g, '').replace(/#.*$/, '').trim();
    if (line) terms.push(line);
  }
  return { terms, source };
}

// Per-repo term exemptions: a repo may legitimately contain its OWN
// codenames (a private repo named after the term, or private repos cross-referencing
// each other), pushing THAT repo is not a leak. User-curated, gitignored map:
//   ~/.claude/state/guard-exempt.json   { "<abs repo root>": ["term", ...], ... }
// ($GUARD_EXEMPT_FILE overrides, used by the test harness.) Any read/parse error
// means NO exemptions: the guard degrades toward blocking, never toward leaking.
function exemptTermsFor(root) {
  try {
    const fp = process.env.GUARD_EXEMPT_FILE || path.join(os.homedir(), '.claude', 'state', 'guard-exempt.json');
    const map = JSON.parse(fs.readFileSync(fp, 'utf8'));
    const want = fs.realpathSync(root);
    for (const k of Object.keys(map)) {
      let kk = k.replace(/\$HOME/g, os.homedir()).replace(/^~(?=\/|$)/, os.homedir());
      try { kk = fs.realpathSync(kk); } catch (_) {}
      if (kk === want && Array.isArray(map[k])) return new Set(map[k].map((t) => String(t).toLowerCase()));
    }
  } catch (_) {}
  return new Set();
}

function allowSilently() { process.exit(0); }
// There is no ask() helper (permissionDecision:"ask"): that path was retired.
function logLine(text) {
  // One line in the LOCAL log (never sent anywhere): every block is answerable.
  try {
    const logPath = process.env.GUARD_PUBLISH_LOG || path.join(os.homedir(), '.claude', 'logs', 'guard-publish.log');
    fs.mkdirSync(path.dirname(logPath), { recursive: true });
    const ts = new Date().toISOString().replace('T', ' ').slice(0, 19);
    fs.appendFileSync(logPath, `[${ts}] ${text}\n`);
  } catch (_) {}
}
function internalErrorAllow(e) {
  // --scan only: it fails open, like its caller expects (a record check that cannot
  // run must not stop /handoff), but never silently.
  try {
    process.stderr.write(`guard-publish: internal error (${e && e.message ? e.message : String(e)}): allowing (fail-open)\n`);
  } catch (_) {}
  process.exit(0);
}
function maskTerm(t) {
  // enough to recognize your own term, useless to a stranger: s******k
  return t.length <= 2 ? t[0] + '*' : t[0] + '*'.repeat(Math.max(1, t.length - 2)) + t[t.length - 1];
}
function denyBlock(reason, detail) {
  // exit 2 = block, honored in every permission mode (incl. bypassPermissions)
  detail = detail || {};
  // Full, unmasked detail goes to the LOCAL log, every block is answerable:
  // which term, which command, which repo, which remote.
  logLine(`BLOCK terms=[${(detail.hits || []).join(', ')}] cwd=${detail.cwd || '?'}` +
    ` repo=${detail.root || '-'} remote=${detail.remote || '-'} cmd=${(detail.cmd || detail.surface || '?').slice(0, 200)}`);
  // The phone alert names the trigger (masked) and the destination, enough to
  // triage from the alert alone; the exact string stays in the local log.
  try {
    // GUARD_PUBLISH_NO_ALERT lets the probe tests exercise blocks without buzzing the user's phone.
    if (!process.env.GUARD_PUBLISH_NO_ALERT) {
      const masked = (detail.hits || []).map(maskTerm).join(', ') || '(unknown)';
      const where = [detail.root || detail.cwd, detail.remote].filter(Boolean).join(' → ') || detail.surface || 'unknown surface';
      // Detached: waiting on alert.py (up to 15 s, the same as the hook's own
      // timeout) could let a slow Telegram send get the hook killed before the
      // exit 2 below, and a killed hook lets the command run.
      spawn(PYBIN,
        [path.join(__dirname, 'alert.py'),
         '--title', '🚫 Publish blocked',
         '--text', `guard-publish stopped a publish-class command.\nTerm: ${masked}\nWhere: ${where}\nFull detail: ~/.claude/logs/guard-publish.log`],
        { detached: true, stdio: 'ignore' }).unref();
    }
  } catch (_) {}
  process.stderr.write(reason + '\n');
  process.exit(2);
}

// ---- --status: machine-readable armed-state probe ----
// An empty denylist means the guard blocks NOTHING, allowed by design. This lets a
// probe report DISARMED (armed:0) apart from "hook missing", which a check of the
// settings wiring alone cannot tell. Checked before stdin: --status carries no payload.
if (process.argv[2] === '--status') {
  let { terms, source } = loadTerms();
  process.stdout.write(`armed:${terms.length} source:${source || '(none)'}\n`);
  process.exit(0);
}

// ---- --scan <file>...: record check before /handoff commits ----
// handoff.md step 1g runs this on the ADR files + CLAUDE.md it is about to commit.
// Same denylist, same case-insensitive substring match, same per-repo exemptions
// (root = the first file's git toplevel). Exit 2 on a hit, term and file on stderr,
// unmasked line in the local log, no phone alert (the user is at the terminal and
// nothing has left the machine). Exit 0 when clean, when the list is empty (stderr
// says so: the check is only as good as the list), or when nothing was readable
// (fail-open with a breadcrumb, like every other path here). Checked before stdin.
if (process.argv[2] === '--scan') {
  const files = process.argv.slice(3);
  if (!files.length) { process.stderr.write('guard-publish --scan: no files given: nothing checked\n'); process.exit(0); }
  try {
    const { terms, source } = loadTerms();
    if (!terms.length) {
      process.stderr.write(`guard-publish --scan: denylist empty (source:${source || '(none)'}): nothing checked\n`);
      process.exit(0);
    }
    let root = '';
    try {
      root = execSync('git rev-parse --show-toplevel 2>/dev/null',
        { cwd: path.dirname(path.resolve(files[0])), stdio: ['ignore', 'pipe', 'ignore'] }).toString().trim();
    } catch (_) {}
    const exempt = root ? exemptTermsFor(root) : new Set();
    const active = terms.filter((t) => !exempt.has(t.toLowerCase()));
    const hits = [];
    let read = 0;
    for (const f of files) {
      let hay;
      try { hay = fs.readFileSync(f, 'utf8').toLowerCase(); read++; } catch (_) {
        process.stderr.write(`guard-publish --scan: cannot read ${f}: skipped\n`); continue;
      }
      for (const t of active) if (hay.includes(t.toLowerCase())) hits.push(`${t} → ${f}`);
    }
    if (hits.length) {
      logLine(`SCAN-BLOCK hits=[${hits.join('; ')}] repo=${root || '-'}`);
      process.stderr.write(
        `🚫 guard-publish --scan: confidential term(s) in the records about to be committed:\n  ${hits.join('\n  ')}\n` +
        `These are on your private "Confidential Terms" denylist. Do NOT commit this. ` +
        `Rephrase the record (handoff.md step 1d), or, if the term is this repo's own codename, add a per-repo exemption.\n`);
      process.exit(2);
    }
    process.stdout.write(`guard-publish --scan: clean (${read} file(s), ${active.length} term(s))\n`);
    process.exit(0);
  } catch (e) {
    internalErrorAllow(e);
  }
}

// ---- 1. what a Bash command runs ----
// A small shell lexer. It splits a command into statements of words (quotes removed,
// nothing expanded) on ; & && | || |& newlines and ( ), and records per statement the
// text of each $(...), `...`, <(...) and >(...) (lexed again later as commands of their
// own) and the bodies of its here-docs and here-strings. A here-doc body and a # comment
// are never read as statements. With `sub` set it lexes the inside of a $( and stops at
// the unmatched ")", returning its index as `end`.
function lex(s, i, sub) {
  const stmts = [];
  const blank = () => ({ w: [], subs: [], heredocs: [], sep: '', any: false });
  let st = blank(), word = null, redir = null, depth = 0;
  const pending = [];                   // here-docs whose bodies start on the next line
  const add = (t) => { word = (word === null ? '' : word) + t; };
  const flush = () => {
    if (word === null) return;
    if (redir === '<<' || redir === '<<-') pending.push({ st, delim: word, strip: redir === '<<-' });
    else if (redir === '<<<') st.heredocs.push(word);
    else if (!redir) st.w.push(word);
    redir = null; word = null;
  };
  const end = (sep) => {
    flush(); redir = null;
    if (st.w.length || st.subs.length || st.heredocs.length || st.any) { st.sep = sep; stmts.push(st); }
    st = blank();
  };
  const subst = (j) => {                // s[j] is the "(" of $( <( >(: the index of its ")"
    if (s[j - 1] === '$' && s[j + 1] === '(') {          // $(( arithmetic )): no command in it
      let d = 0, k = j;
      for (; k < s.length; k++) { if (s[k] === '(') d++; else if (s[k] === ')' && --d === 0) break; }
      return k;
    }
    const r = lex(s, j + 1, true);
    st.subs.push(s.slice(j + 1, r.end));
    return r.end;
  };
  const tick = (j) => {                 // s[j] is a backtick: the index of the closing one
    let k = j + 1;
    while (k < s.length && s[k] !== '`') k += s[k] === '\\' ? 2 : 1;
    st.subs.push(s.slice(j + 1, k).replace(/\\([\\`$])/g, '$1'));
    return k;
  };
  const bodies = (j) => {               // read the pending here-doc bodies from line start j
    for (const p of pending.splice(0)) {
      const lines = [];
      while (j < s.length) {
        let e = s.indexOf('\n', j);
        if (e < 0) e = s.length;
        const line = s.slice(j, e);
        j = e + 1;
        if ((p.strip ? line.replace(/^\t+/, '') : line) === p.delim) break;
        lines.push(line);
      }
      p.st.heredocs.push(lines.join('\n'));
    }
    return j;
  };
  while (i < s.length) {
    const c = s[i];
    if (c === '\\') { if (s[i + 1] !== '\n') add(s[i + 1] || ''); i += 2; continue; }
    if (c === "'") {
      let e = s.indexOf("'", i + 1);
      if (e < 0) e = s.length;
      add(s.slice(i + 1, e)); i = e + 1; continue;
    }
    if (c === '$' && s[i + 1] === "'") { const r = ansiC(s, i + 2); add(r.text); i = r.end; continue; }
    if (c === '"') {
      let j = i + 1, buf = '';
      while (j < s.length && s[j] !== '"') {
        const d = s[j];
        if (d === '\\' && j + 1 < s.length) {
          if (s[j + 1] === '\n') { j += 2; continue; }
          if ('"\\$`'.includes(s[j + 1])) { buf += s[j + 1]; j += 2; continue; }
          buf += d; j++; continue;
        }
        if (d === '`') { const e = tick(j); buf += s.slice(j, e + 1); j = e + 1; continue; }
        if (d === '$' && s[j + 1] === '(') { const e = subst(j + 1); buf += s.slice(j, e + 1); j = e + 1; continue; }
        if (d === '$' && s[j + 1] === '{') { const e = braceEnd(s, j + 2); buf += s.slice(j, e + 1); j = e + 1; continue; }
        buf += d; j++;
      }
      add(buf); i = j + 1; continue;
    }
    if (c === '`') { const e = tick(i); add(s.slice(i, e + 1)); i = e + 1; continue; }
    if ((c === '$' || c === '<' || c === '>') && s[i + 1] === '(') { const e = subst(i + 1); add(s.slice(i, e + 1)); i = e + 1; continue; }
    if (c === '$' && s[i + 1] === '{') { const e = braceEnd(s, i + 2); add(s.slice(i, e + 1)); i = e + 1; continue; }
    if (c === '#' && word === null) { while (i < s.length && s[i] !== '\n') i++; continue; }
    if (c === ' ' || c === '\t') { flush(); i++; continue; }
    if (c === '\n') { end('\n'); i = bodies(i + 1); continue; }
    if (c === '(') { end('('); stmts.push({ open: true }); depth++; i++; continue; }
    if (c === ')') {
      if (sub && depth === 0) { end(')'); return { stmts, end: i }; }
      end(')');
      if (depth > 0) { depth--; stmts.push({ close: true }); }
      i++; continue;
    }
    if (c === '<' || c === '>' || (c === '&' && s[i + 1] === '>')) {
      if (word !== null && /^\d+$/.test(word)) word = null;   // the fd of 2>&1
      else flush();
      const op = ['<<<', '<<-', '&>>', '<<', '<>', '<&', '>>', '>|', '>&', '&>'].find((o) => s.startsWith(o, i)) || c;
      redir = op; st.any = true; i += op.length; continue;
    }
    if (c === ';' || c === '&' || c === '|') {
      const op = ['&&', '||', '|&', ';;', ';&'].find((o) => s.startsWith(o, i)) || c;
      end(op); i += op.length; continue;
    }
    add(c); i++;
  }
  end('');
  return { stmts, end: s.length };
}
function braceEnd(s, j) {               // the index of the } that closes a ${ opened before j
  let d = 1, k = j;
  for (; k < s.length; k++) { if (s[k] === '{') d++; else if (s[k] === '}' && --d === 0) break; }
  return k;
}
function ansiC(s, j) {                  // $'...' from j: its text, and the index after it
  const one = { n: '\n', t: '\t', r: '\r', e: '\x1b', E: '\x1b', a: '\x07', b: '\b', f: '\f', v: '\v' };
  let out = '', k = j;
  while (k < s.length && s[k] !== "'") {
    if (s[k] !== '\\' || k + 1 >= s.length) { out += s[k++]; continue; }
    const n = s[k + 1];
    const m = /^(?:x([0-9a-fA-F]{1,2})|([0-7]{1,3}))/.exec(s.slice(k + 1, k + 4));
    if (one[n]) { out += one[n]; k += 2; }
    else if (m) { out += String.fromCharCode(parseInt(m[1] || m[2], m[1] ? 16 : 8)); k += 1 + m[0].length; }
    else { out += n; k += 2; }
  }
  return { text: out, end: k + 1 };
}

// Wrappers that run the rest of their statement as a command, with those of their
// options that take the next word as a value.
const WRAPPERS = {
  command: [], builtin: [], exec: ['-a'], nohup: [], time: [],
  env: ['-u', '--unset', '-C', '--chdir', '-S', '--split-string'],
  sudo: ['-u', '--user', '-g', '--group', '-h', '--host', '-p', '--prompt', '-C', '--close-from',
    '-D', '--chdir', '-r', '--role', '-t', '--type', '-T', '--command-timeout', '-U', '--other-user', '-R', '--chroot'],
  doas: ['-u', '-C'],
  nice: ['-n', '--adjustment'],
  timeout: ['-s', '--signal', '-k', '--kill-after'],
  xargs: ['-I', '-J', '-L', '-n', '-P', '-R', '-S', '-s', '-E', '-a', '-d', '--arg-file', '--delimiter',
    '--max-args', '--max-lines', '--max-procs', '--max-chars', '--eof', '--replace'],
  caffeinate: ['-t', '-w'],
  npx: ['-p', '--package', '-c', '--call'],
  bunx: ['-p', '--package'],
};
const KEYWORDS = new Set(['!', '{', '}', 'if', 'then', 'else', 'elif', 'do', 'while', 'until']);
// The word that names the program a statement runs, past NAME=value prefixes, shell
// keywords and wrappers (env, sudo, xargs, npx, pnpm dlx...), with the NAME=value
// assignments seen on the way. `at` is -1 when the statement runs no program.
function program(w) {
  const assign = {};
  let i = 0;
  while (i < w.length) {
    const t = w[i], b = path.basename(t), m = /^([A-Za-z_][A-Za-z0-9_]*)=([\s\S]*)$/.exec(t);
    if (m) { assign[m[1]] = m[2]; i++; continue; }
    if (KEYWORDS.has(t)) { i++; continue; }
    if ((b === 'pnpm' || b === 'yarn') && w[i + 1] === 'dlx') { i += 2; continue; }
    if (!Object.prototype.hasOwnProperty.call(WRAPPERS, b)) break;
    if (b === 'command' && /^-[vV]$/.test(w[i + 1] || '')) return { at: -1, assign };   // a lookup, not a run
    const val = WRAPPERS[b];
    i++;
    while (i < w.length && w[i].startsWith('-') && w[i] !== '-') {
      if (w[i] === '--') { i++; break; }
      if (val.includes(w[i])) i++;
      i++;
    }
    if (b === 'timeout') i++;           // its DURATION
  }
  return { at: i < w.length ? i : -1, assign };
}

const SHELLS = new Set(['bash', 'sh', 'zsh', 'dash', 'ksh']);
// Programs that never run their arguments as a local command: a `git push` among their
// words is text (echo git push, grep git push), or runs elsewhere (ssh host git push).
// Behind any other program the guard does not know (stdbuf -oL git push, flock f git
// push), the first later word naming git, a shell or eval is read as a command too.
const NOT_RUNNERS = new Set(['echo', 'printf', 'print', 'grep', 'egrep', 'fgrep', 'rg', 'ag', 'ack',
  'man', 'help', 'info', 'which', 'type', 'whatis', 'apropos', 'tldr', 'cat', 'less', 'more', 'head',
  'tail', 'wc', 'sort', 'ls', 'ssh', 'mosh', 'alias', 'export', 'local', 'declare', 'readonly', 'set',
  'unset', 'for', 'case', 'select', 'function', 'test', '[', '[[', 'git', 'gh']);
const INNER = new Set(['git', 'eval', ...SHELLS]);
// The commands a statement hands to a shell as text: the argument of bash -c / sh -c /
// zsh -c, eval's arguments, or, for a shell that reads its commands from stdin, its
// here-docs and here-strings and whatever echo, printf or a here-doc pipes into it.
function scriptsOf(st, at, stmts, k) {
  const w = st.w, name = path.basename(w[at]);
  if (name === 'eval') return [w.slice(at + 1).join(' ')];
  if (!SHELLS.has(name)) return [];
  let c = false, s = false, i = at + 1;
  for (; i < w.length; i++) {
    const a = w[i];
    if (a === '-') { s = true; i++; break; }
    if (a === '--') { i++; break; }
    if (['-o', '+o', '-O', '+O', '--rcfile', '--init-file'].includes(a)) { i++; continue; }
    if (/^-[A-Za-z]+$/.test(a)) { c = c || a.includes('c'); s = s || a.includes('s'); continue; }
    if (/^\+[A-Za-z]+$/.test(a) || a.startsWith('--')) continue;
    break;
  }
  if (c) return i < w.length ? [w[i]] : [];
  if (i < w.length && !s) return [];    // runs a script file
  const texts = [...st.heredocs];
  for (let j = k - 1; j >= 0 && (stmts[j].sep === '|' || stmts[j].sep === '|&'); j--) {
    const prev = stmts[j], p = program(prev.w);
    texts.push(...prev.heredocs);
    const pn = p.at >= 0 ? path.basename(prev.w[p.at]) : '';
    if (pn === 'echo' || pn === 'printf') {
      texts.push(prev.w.slice(p.at + 1).filter((x) => !/^-[neE]+$/.test(x)).join(' ').replace(/\\n/g, '\n'));
    }
  }
  return texts;
}

// Every simple command the shell would run, each with the directory it runs in: the
// statements of the command, of each $(...), `...` and <(...) in it, and of the scripts
// it hands to a shell as text (scriptsOf). A `cd <dir>` (or pushd) moves the statements
// after it (`cd ~/app && git push` pushes ~/app); a cd inside ( ) or a substitution
// stays there.
function commands(cmd, cwd0, expand, depth = 0) {
  const out = [];
  // Deeper nesting than any real command: refuse to guess (blocks while armed).
  if (depth > 16) throw new Error('command nested too deeply to read');
  const { stmts } = lex(cmd, 0, false);
  const saved = [];
  let cwd = cwd0;
  stmts.forEach((st, k) => {
    if (st.open) { saved.push(cwd); return; }
    if (st.close) { if (saved.length) cwd = saved.pop(); return; }
    for (const sub of st.subs) out.push(...commands(sub, cwd, expand, depth + 1));
    const p = program(st.w);
    if (p.at < 0) {
      // A wrapper whose option value is the command itself (env -S 'git push').
      if (st.w.length && Object.prototype.hasOwnProperty.call(WRAPPERS, path.basename(st.w[0]))) {
        for (const t of st.w.slice(1)) if (/\s/.test(t)) out.push(...commands(t, cwd, expand, depth + 1));
      }
      return;
    }
    const name = path.basename(st.w[p.at]);
    if (name === 'cd' || name === 'pushd') {
      const args = st.w.slice(p.at + 1);
      while (args.length && /^-[LPe@]+$/.test(args[0])) args.shift();
      const to = args.length ? expand(args[0]) : os.homedir();
      // `cd -` or a target only the shell can work out (`cd "$(git rev-parse ...)"`):
      // the directory it was in is the best guess left
      if (args[0] !== '-' && !/\$\(|`/.test(to)) cwd = path.resolve(cwd || os.homedir(), to);
      return;
    }
    const ats = [p.at];
    if (!NOT_RUNNERS.has(name) && !INNER.has(name)) {
      const j = st.w.findIndex((t, i) => i > p.at && INNER.has(path.basename(t)));
      if (j > 0) ats.push(j);
      // A whole command passed as one word (su -c 'git push', fish -c, watch, env -S)
      // is read as a script too.
      for (const t of st.w.slice(p.at + 1)) {
        if (/\s/.test(t)) out.push(...commands(t, cwd, expand, depth + 1));
      }
    }
    for (const at of ats) {
      out.push({ w: st.w, at, assign: p.assign, cwd });
      for (const s of scriptsOf(st, at, stmts, k)) out.push(...commands(s, cwd, expand, depth + 1));
    }
  });
  return out;
}

// ---- 2. which of those publish ----
// Plain gh/npm/vercel/curl/... patterns over the raw command text; a git push, a deploy
// or an upload is recognised from the parsed commands instead (pushPlan, publishPlan).
const PUBLISH = [
  /\bgh\s+(?:(?:-R|--repo)(?:\s+|=)?\S+\s+)?repo\s+create\b/,
  /\bgh\s+(?:(?:-R|--repo)(?:\s+|=)?\S+\s+)?release\s+create\b/,
  /\bgh\s+(?:(?:-R|--repo)(?:\s+|=)?\S+\s+)?gist\s+create\b/,
  /\bgh\s+(?:(?:-R|--repo)(?:\s+|=)?\S+\s+)?pr\s+create\b/,
  // Plain gh subcommands that publish or expose data. `gh repo edit` matches broadly,
  // not only --visibility: description/homepage edits publish their text too.
  /\bgh\s+(?:(?:-R|--repo)(?:\s+|=)?\S+\s+)?repo\s+edit\b/,
  /\bgh\s+(?:(?:-R|--repo)(?:\s+|=)?\S+\s+)?secret\s+set\b/,
  /\bgh\s+(?:(?:-R|--repo)(?:\s+|=)?\S+\s+)?release\s+upload\b/,
  /\bgh\s+(?:(?:-R|--repo)(?:\s+|=)?\S+\s+)?gist\s+edit\b/,
  /\bgh\s+(?:(?:-R|--repo)(?:\s+|=)?\S+\s+)?workflow\s+run\b/,
  /\bgh\s+(?:(?:-R|--repo)(?:\s+|=)?\S+\s+)?api\b[^\n]*(--method\s+(PUT|POST|PATCH|DELETE)|-X\s+(PUT|POST|PATCH|DELETE)|(^|\s)(-f|-F|--field|--raw-field|--input)(\s|=))/,
  /\bnpm\s+publish\b/, /\bpnpm\s+publish\b/, /\byarn\s+publish\b/,
  /\bvercel\b(?!\s+(env\s+pull|ls|list|inspect|whoami|logs|link|--help))/,
  /\bnetlify\s+deploy\b/,
  /\bcurl\b[^\n]*(-X\s*(POST|PUT|PATCH)|--request[\s=]+(POST|PUT|PATCH)|--data\b|--data-\w+|--json\b|(^|\s)-[A-Za-z]*[dTF]|--upload-file|--form\b)/,
  /\bwget\b[^\n]*(--post-data|--post-file|--body-file|--body-data|--method[= ](POST|PUT|PATCH))/,
];
// The same patterns, matched only where a parsed command's program starts.
function publishesAt(c) {
  const s = [path.basename(c.w[c.at]), ...c.w.slice(c.at + 1)].join(' ');
  return PUBLISH.some((re) => { const m = re.exec(s); return !!m && m.index === 0; });
}

// git's global options that take the next word as their value (git -C <path> push).
const GIT_VALUE = new Set(['-C', '-c', '--git-dir', '--work-tree', '--namespace', '--super-prefix',
  '--config-env', '--attr-source', '--list-cmds']);
// git's own commands, which an alias can never replace: no config lookup for these.
const GIT_BUILTINS = new Set(('add am annotate apply archive bisect blame branch bundle cat-file check-attr ' +
  'check-ignore checkout cherry cherry-pick clean clone commit config count-objects describe diff ' +
  'difftool fetch for-each-ref format-patch fsck gc grep hash-object help init log ls-files ls-remote ' +
  'ls-tree maintenance merge merge-base mergetool mv name-rev notes pull push range-diff rebase reflog ' +
  'remote repack replace reset restore rev-list rev-parse revert rm shortlog show show-ref ' +
  'sparse-checkout stash status submodule switch symbolic-ref tag update-index update-ref var verify-commit ' +
  'version whatchanged worktree').split(' '));
// VAR=value assignments anywhere in the Bash command (export, an earlier statement),
// set by bash() for the alias lookup below.
let scriptEnv = {};
// What `git <word>` expands to: a -c alias on the command line, else an [alias] entry
// in the git config git would read there: the directory it runs in, --git-dir or
// GIT_DIR, --config-env, and the GIT_CONFIG_* variables (GIT_CONFIG_PARAMETERS,
// GIT_CONFIG_GLOBAL too) the command sets. null when it is not an alias.
function gitAlias(word, aliases, c, plan) {
  const key = Object.keys(aliases).find((k) => k.toLowerCase() === word.toLowerCase());
  if (key !== undefined) return aliases[key];
  if (GIT_BUILTINS.has(word) || word.startsWith('-')) return null;
  let dir = c.cwd || process.cwd();
  for (const d of plan.dirs) if (!/[$`]/.test(d)) dir = path.resolve(dir, d.replace(/^~(?=\/|$)/, os.homedir()));
  const pre = [];
  if (plan.gitDir && !/[$`]/.test(plan.gitDir)) pre.push(`--git-dir=${path.resolve(dir, plan.gitDir)}`);
  for (const v of plan.configEnv) pre.push(`--config-env=${v}`);
  try {
    const v = execFileSync('git', [...pre, 'config', '--get', `alias.${word}`],
      { cwd: dir, stdio: ['ignore', 'pipe', 'ignore'], timeout: 2000,
        env: Object.assign({}, process.env, scriptEnv, c.assign) }).toString().trim();
    return v || null;
  } catch (_) { return null; }
}
// `git [global options] <word> ...`: the global options read into a push plan, the
// -c and GIT_CONFIG_* aliases, and the index of git's subcommand word; null when the
// command is not git or is only asking for help.
function gitHead(c) {
  const w = c.w;
  if (c.at < 0 || path.basename(w[c.at]) !== 'git') return null;
  const plan = { dirs: [], gitDir: c.assign.GIT_DIR || null, workTree: c.assign.GIT_WORK_TREE || null,
    repo: null, refspecs: [], branches: false, mirror: false, tagsOnly: false, followTags: false, del: false,
    configEnv: [] };
  const aliases = {};
  const n = parseInt(c.assign.GIT_CONFIG_COUNT || '', 10);
  for (let k = 0; k < (n > 0 ? Math.min(n, 100) : 0); k++) {
    const m = /^alias\.(.+)$/i.exec(c.assign[`GIT_CONFIG_KEY_${k}`] || '');
    if (m && c.assign[`GIT_CONFIG_VALUE_${k}`] !== undefined) aliases[m[1]] = c.assign[`GIT_CONFIG_VALUE_${k}`];
  }
  let i = c.at + 1;
  for (; i < w.length; i++) {
    const a = w[i];
    if (GIT_VALUE.has(a)) {
      const v = w[++i];
      if (v === undefined) return null;
      if (a === '-C') plan.dirs.push(v);
      else if (a === '--git-dir') plan.gitDir = v;
      else if (a === '--work-tree') plan.workTree = v;
      else if (a === '-c') { const m = /^alias\.([^=]+)=([\s\S]*)$/i.exec(v); if (m) aliases[m[1]] = m[2]; }
      else if (a === '--config-env' && /^alias\./i.test(v)) plan.configEnv.push(v);
    } else if (a.startsWith('--config-env=')) {
      if (/^alias\./i.test(a.slice(13))) plan.configEnv.push(a.slice(13));
    } else if (a.startsWith('--git-dir=')) plan.gitDir = a.slice(10);
    else if (a.startsWith('--work-tree=')) plan.workTree = a.slice(12);
    else if (['-h', '--help', '-v', '--version'].includes(a)) return null;
    else if (!a.startsWith('-')) break;
  }
  return { plan, aliases, i };
}
// What git's subcommand word runs once aliases are expanded (alias chains followed, as
// git does): { words } for a git command line, { shell } for a `!` alias, null when the
// word is not an alias or the chain loops (git refuses a loop too). A chain longer than
// any real one is refused (blocks while armed) rather than read as no alias.
function gitExpand(c, head) {
  let words = c.w.slice(head.i);
  const seen = new Set();
  while (words.length && words[0] !== 'push') {
    const word = words[0];
    if (seen.has(word.toLowerCase())) return null;
    if (seen.size > 16) throw new Error('git alias chain too long to read');
    seen.add(word.toLowerCase());
    const v = gitAlias(word, head.aliases, c, head.plan);
    if (v === null) return seen.size > 1 ? { words } : null;
    if (v.startsWith('!')) return { shell: v.slice(1), args: words.slice(1) };
    words = [...v.trim().split(/\s+/), ...words.slice(1)];
  }
  return { words };
}
// `git [global options] push [options] [repository [refspec...]]` → what it would send,
// or null when push is not git's subcommand (git stash push, git commit -m "push it").
function pushPlan(c) {
  const head = gitHead(c);
  if (!head) return null;
  const { plan } = head;
  let rest;
  if (c.w[head.i] === 'push') rest = c.w.slice(head.i + 1);
  else {
    // An alias that pushes (git -c alias.p=push p, [alias] p = push in a config, or a
    // chain of them). A `!` alias is read as a shell command too (in bash()).
    const x = c.w[head.i] === undefined ? null : gitExpand(c, head);
    if (!x) return null;
    if (x.shell !== undefined) {
      // Its commands are read too (in bash()); one that names push at all
      // is taken as a push of this repository, as a parse can miss `f(){ git push; }; f`.
      if (!/\bpush\b/.test(x.shell)) return null;
      rest = x.args;
    } else if (x.words[0] !== 'push') return null;
    else rest = x.words.slice(1);
  }
  for (let i = 0; i < rest.length; i++) {
    const a = rest[i];
    if (a === '--all' || a === '--branches') plan.branches = true;
    else if (a === '--mirror') plan.mirror = true;
    else if (a === '--tags') plan.tagsOnly = true;
    else if (a === '--follow-tags') plan.followTags = true;
    else if (a === '-d' || a === '--delete') plan.del = true;
    else if (a.startsWith('--repo=')) plan.repo = a.slice(7);
    else if (['--repo', '-o', '--push-option', '--receive-pack', '--exec'].includes(a)) {
      if (a === '--repo') plan.repo = rest[i + 1];
      i++;
    } else if (a.startsWith('-')) continue;
    else if (plan.repo === null) plan.repo = a;
    else plan.refspecs.push(a);
  }
  return plan;
}

// Deploys, uploads and posts: what a command sends besides its own text, or null when
// it is none of them. `dirs` are walked (dirTerms, leaving `skip` folders out too),
// `files` are read as they are, `paths` are read when they are text files and walked
// when they are folders. A plan with nothing in it still makes the command publish-class
// (docker push, gh issue create): its text is checked.
const VERCEL_NON_UPLOAD = new Set(['env', 'domains', 'domain', 'dns', 'certs', 'cert', 'alias',
  'aliases', 'secrets', 'secret', 'project', 'projects', 'teams', 'team', 'switch', 'login',
  'logout', 'whoami', 'ls', 'list', 'inspect', 'logs', 'link', 'pull', 'build', 'dev',
  'redeploy', 'promote', 'rollback', 'remove', 'rm', 'git', 'integration', 'integrations',
  'init', 'help', 'bisect', 'telemetry', 'target', 'targets', 'cache', 'blob', 'install', 'i']);
const VERCEL_VALUE_FLAGS = new Set(['--cwd', '--scope', '-S', '--token', '-t', '--env', '-e',
  '--build-env', '-b', '--meta', '-m', '--regions', '--target', '--local-config', '-A',
  '--global-config', '-Q', '--name', '-n', '--archive']);
const SURGE_NON_UPLOAD = new Set(['login', 'logout', 'whoami', 'list', 'teardown', 'token', 'plus',
  'ssl', 'encrypt', 'invite', 'revoke', 'files', 'config', 'audit', 'bust', 'help']);
// Options that take the next word as a value, per tool (only where it changes which
// words are the operands; an option missed here can only make the guard read more).
const VALUE_OPTS = {
  aws: ['--profile', '--region', '--endpoint-url', '--output', '--query', '--color', '--ca-bundle',
    '--cli-read-timeout', '--cli-connect-timeout', '--cli-binary-format', '--exclude', '--include',
    '--acl', '--grants', '--storage-class', '--sse', '--sse-c', '--sse-c-key', '--sse-kms-key-id',
    '--website-redirect', '--content-type', '--cache-control', '--content-disposition',
    '--content-encoding', '--content-language', '--expires', '--metadata', '--metadata-directive',
    '--expected-size', '--page-size', '--request-payer', '--source-region', '--checksum-algorithm'],
  cargo: ['--config', '--color', '--manifest-path', '--package', '--registry', '--index', '--token',
    '--target', '--target-dir', '--features', '--jobs', '--lockfile-path'],
  docker: ['--host', '--context', '--config', '--log-level', '--tlscacert', '--tlscert', '--tlskey'],
  firebase: ['--project', '--config', '--token', '--only', '--except', '--message', '--account'],
  fly: ['--app', '--config', '--image', '--env', '--region', '--dockerfile', '--build-arg',
    '--build-secret', '--build-target', '--strategy', '--vm-size', '--vm-memory', '--vm-cpus',
    '--wait-timeout', '--label', '--primary-region', '--regions', '--access-token'],
  gcloud: ['--project', '--account', '--configuration', '--verbosity', '--format',
    '--impersonate-service-account', '--billing-project', '--flags-file', '--flatten', '--trace-token'],
  rclone: ['--config', '--log-file', '--log-level', '--transfers', '--checkers', '--exclude',
    '--include', '--filter', '--exclude-from', '--include-from', '--filter-from', '--files-from',
    '--bwlimit', '--max-age', '--min-age', '--max-size', '--min-size', '--backup-dir', '--suffix',
    '--retries', '--timeout', '--contimeout', '--stats', '--max-transfer', '--compare-dest',
    '--copy-dest', '--max-depth', '--header', '--buffer-size', '--cache-dir'],
  rsync: ['--rsh', '--rsync-path', '--filter', '--exclude', '--include', '--exclude-from',
    '--include-from', '--files-from', '--temp-dir', '--block-size', '--backup-dir', '--suffix',
    '--chmod', '--chown', '--usermap', '--groupmap', '--compare-dest', '--copy-dest', '--link-dest',
    '--partial-dir', '--log-file', '--log-file-format', '--out-format', '--password-file', '--port',
    '--sockopts', '--bwlimit', '--timeout', '--contimeout', '--max-size', '--min-size',
    '--max-delete', '--modify-window', '--checksum-choice', '--compress-choice', '--compress-level',
    '--skip-compress', '--info', '--debug', '--remote-option', '--iconv', '--stop-after', '--stop-at',
    '--write-batch', '--only-write-batch', '--read-batch', '--protocol', '--address'],
  surge: ['--project', '--domain', '--endpoint'],
  twine: ['--repository', '--repository-url', '--sign-with', '--identity', '--username', '--password',
    '--comment', '--config-file', '--cert', '--client-cert'],
  wrangler: ['--config', '--env', '--name', '--compatibility-date', '--compatibility-flags', '--assets',
    '--site', '--site-include', '--site-exclude', '--var', '--define', '--alias', '--routes', '--route',
    '--outdir', '--outfile', '--triggers', '--tsconfig', '--project-name', '--branch', '--commit-hash',
    '--commit-message', '--dispatch-namespace', '--message', '--tag'],
};
function flagValues(w, names) {
  const out = [];
  for (let i = 0; i < w.length; i++) {
    for (const n of names) {
      if (w[i] === n && i + 1 < w.length) out.push(w[i + 1]);
      else if (w[i].startsWith(n + '=')) out.push(w[i].slice(n.length + 1));
    }
  }
  return out;
}
function flagValue(w, names) { return flagValues(w, names)[0] || null; }
// The operands of a getopt-style command line: short options may cluster (-avz) and
// those in `shortV` take a value, attached (-P22) or as the next word; the long options
// in `longV` take the next word unless written --opt=value.
function getopt(args, shortV, longV) {
  const pos = [];
  for (let i = 0; i < args.length; i++) {
    const a = args[i];
    if (a === '--') { pos.push(...args.slice(i + 1)); break; }
    if (a.startsWith('--')) { if (!a.includes('=') && (longV || []).includes(a)) i++; continue; }
    if (a.length > 1 && a.startsWith('-')) {
      for (let k = 1; k < a.length; k++) if (shortV.includes(a[k])) { if (k === a.length - 1) i++; break; }
      continue;
    }
    pos.push(a);
  }
  return pos;
}
// host:path (scp, rsync, rclone): a colon after at least one character, before any slash.
function hostColon(p) { const i = p.indexOf(':'); return i > 0 && !p.slice(0, i).includes('/'); }
// A copy command's local sources when a later operand is remote (an upload); null for a
// download, a remote-to-remote copy, or no local source.
function upload(pos, remote) {
  let last = -1;
  pos.forEach((p, i) => { if (remote(p)) last = i; });
  const local = last > 0 ? pos.slice(0, last).filter((p) => !remote(p)) : [];
  return local.length ? local : null;
}
// A path with shell wildcards (dist/*, *.whl) expanded against the disk, as the shell
// (or twine) would; the path itself when it has none or nothing matches.
function glob(p) {
  if (!/[*?[]/.test(p) || fs.existsSync(p)) return [p];
  let found = [path.parse(p).root || '/'];
  for (const seg of p.split('/').filter(Boolean)) {
    leftMs(`files matching ${p}`);
    if (!/[*?[]/.test(seg)) { found = found.map((b) => path.join(b, seg)); continue; }
    const re = globRe(seg), next = [];
    for (const b of found) {
      let names = [];
      try { names = fs.readdirSync(b); } catch (_) {}
      for (const n of names) if (re.test(n) && (n[0] !== '.' || seg[0] === '.')) next.push(path.join(b, n));
    }
    found = next.slice(0, 5000);
  }
  return found.length ? found : [p];
}
function globRe(seg) {
  let re = '';
  for (let i = 0; i < seg.length; i++) {
    const ch = seg[i], close = ch === '[' ? seg.indexOf(']', i + 2) : -1;
    if (ch === '*') re += '.*';
    else if (ch === '?') re += '.';
    else if (close > 0) { re += '[' + seg.slice(i + 1, close).replace(/^!/, '^').replace(/\\/g, '\\\\') + ']'; i = close; }
    else re += ch.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  }
  try { return new RegExp(`^${re}$`); } catch (_) { return /(?!)/; }
}
function publishPlan(c, expand) {
  const { w, at } = c;
  if (at < 0) return null;
  const cwd = c.cwd || process.cwd();
  const abs = (p) => path.resolve(cwd, expand(p));
  const isDir = (p) => { try { return fs.statSync(p).isDirectory(); } catch (_) { return false; } };
  const isFile = (p) => { try { return fs.statSync(p).isFile(); } catch (_) { return false; } };
  const plan = (o) => Object.assign({ dirs: [], files: [], paths: [], skip: [] }, o);
  const sources = (list) => plan({ paths: list.filter((p) => p !== '-').map(abs).flatMap(glob) });
  const name = path.basename(w[at]);
  const rest = w.slice(at + 1);
  if (name === 'vercel') {
    const pos = [];
    for (let i = 0; i < rest.length; i++) {
      if (VERCEL_VALUE_FLAGS.has(rest[i])) { i++; continue; }
      if (!rest[i].startsWith('-')) pos.push(rest[i]);
    }
    if (pos.length && VERCEL_NON_UPLOAD.has(pos[0])) return null;
    const cwdFlag = flagValue(rest, ['--cwd']);
    const p = pos.filter((x) => x !== 'deploy').map(abs).find(isDir);
    return plan({ dirs: [p || (cwdFlag ? abs(cwdFlag) : cwd)] });
  }
  if (name === 'netlify' && rest[0] === 'deploy') {
    const d = flagValue(rest.slice(1), ['--dir', '-d']);
    const f = flagValue(rest.slice(1), ['--functions', '-f']);
    return plan({ dirs: [d ? abs(d) : cwd, ...(f ? [abs(f)] : [])] });
  }
  if (['npm', 'pnpm', 'yarn'].includes(name) && rest[0] === 'publish') {
    const target = rest.slice(1).filter((x) => !x.startsWith('-')).map(abs)[0];
    if (target && isFile(target)) return plan({ files: [target] });   // a tarball: read as is
    return plan({ dirs: [target && isDir(target) ? target : cwd] });
  }
  if (name === 'gh') {
    // global repository flag before the subcommand: gh -R owner/repo pr create
    let g = rest;
    while (g.length && /^(-R|--repo)(=.*)?$|^-R./.test(g[0])) g = g.slice(/^(-R|--repo)$/.test(g[0]) ? 2 : 1);
    const [a, b] = g;
    const files = (list) => list.filter((x) => !x.startsWith('-')).map((x) => abs(x.split('#')[0])).filter(isFile);
    if (a === 'release' && (b === 'create' || b === 'upload')) return plan({ files: files(g.slice(3)) });
    if (a === 'gist' && b === 'create') return plan({ files: files(g.slice(2)) });
    if (a === 'gist' && b === 'edit') {
      const add = flagValue(g.slice(2), ['--add', '-a']);
      return plan({ files: add ? [abs(add)].filter(isFile) : [] });
    }
    if ((a === 'issue' && ['create', 'comment', 'edit'].includes(b)) ||
        (a === 'pr' && ['create', 'comment', 'review', 'edit'].includes(b))) {
      return plan({ files: flagValues(g.slice(2), ['--body-file', '-F']).filter((x) => x !== '-').map(abs).filter(isFile) });
    }
    return null;
  }
  if (name === 'docker' || name === 'podman') {
    const pos = getopt(rest, 'Hcl', VALUE_OPTS.docker);
    return pos[0] === 'push' || (pos[0] === 'image' && pos[1] === 'push') ? plan({}) : null;
  }
  if (name === 'cargo') {
    const pos = getopt(rest, 'CZpjF', VALUE_OPTS.cargo).filter((x) => !x.startsWith('+'));
    if (pos[0] !== 'publish') return null;
    const dir = flagValue(rest, ['-C']) ? abs(flagValue(rest, ['-C'])) : cwd;
    const mp = flagValue(rest, ['--manifest-path']);
    return plan({ dirs: [mp ? path.dirname(path.resolve(dir, expand(mp))) : dir], skip: ['target'] });
  }
  const twine = name === 'twine' ? rest
    : (/^python[0-9.]*$/.test(name) && rest[0] === '-m' && rest[1] === 'twine' ? rest.slice(2) : null);
  if (twine) {
    const pos = getopt(twine, 'riupc', VALUE_OPTS.twine);
    return pos[0] === 'upload' ? sources(pos.slice(1)) : null;
  }
  if (name === 'scp' || name === 'rsync') {
    const pos = name === 'scp' ? getopt(rest, 'cDFiJloPSX') : getopt(rest, 'efTBM@', VALUE_OPTS.rsync);
    const local = upload(pos, (p) => new RegExp(`^${name}://`, 'i').test(p) || hostColon(p));
    return local ? sources(local) : null;
  }
  if (name === 'aws') {
    const pos = getopt(rest, '', VALUE_OPTS.aws);
    const s3 = pos.indexOf('s3');
    if (s3 < 0 || !['cp', 'sync', 'mv'].includes(pos[s3 + 1])) return null;
    const local = upload(pos.slice(s3 + 2), (p) => /^s3:\/\//i.test(p));
    return local ? sources(local) : null;
  }
  if (name === 'gsutil' || name === 'gcloud') {
    const pos = name === 'gsutil' ? getopt(rest, 'ohuiaLsjzx') : getopt(rest, '', VALUE_OPTS.gcloud);
    const sub = name === 'gsutil' ? pos.findIndex((p) => ['cp', 'rsync', 'mv'].includes(p))
      : (pos.indexOf('storage') >= 0 && ['cp', 'rsync', 'mv'].includes(pos[pos.indexOf('storage') + 1]) ? pos.indexOf('storage') + 1 : -1);
    if (sub < 0) return null;
    const local = upload(pos.slice(sub + 1), (p) => /^(gs|s3):\/\//i.test(p));
    return local ? sources(local) : null;
  }
  if (name === 'rclone') {
    const pos = getopt(rest, '', VALUE_OPTS.rclone);
    const sub = pos.findIndex((p) => ['copy', 'sync', 'move', 'copyto', 'moveto'].includes(p));
    if (sub < 0) return null;
    const local = upload(pos.slice(sub + 1), (p) => p.startsWith(':') || hostColon(p));
    return local ? sources(local) : null;
  }
  if (name === 'firebase') {
    const pos = getopt(rest, 'Pcm', VALUE_OPTS.firebase);
    if (pos[0] !== 'deploy' && pos[0] !== 'hosting:channel:deploy') return null;
    const cfg = flagValue(rest, ['--config', '-c']);
    return plan({ dirs: [cfg ? path.dirname(abs(cfg)) : cwd] });
  }
  if (name === 'wrangler') {
    const pos = getopt(rest, 'ce', VALUE_OPTS.wrangler);
    const cfg = flagValue(rest, ['--config', '-c']);
    const base = cfg ? path.dirname(abs(cfg)) : cwd;
    if (pos[0] === 'deploy' || pos[0] === 'publish') {
      return plan({ dirs: [base, ...flagValues(rest, ['--assets', '--site']).map(abs)] });
    }
    if (pos[0] === 'pages' && (pos[1] === 'deploy' || pos[1] === 'publish')) {
      return plan({ dirs: [pos.slice(2).map(abs).find(isDir) || base] });
    }
    return null;
  }
  if (name === 'fly' || name === 'flyctl') {
    const pos = getopt(rest, 'aciert', VALUE_OPTS.fly);
    if (pos[0] !== 'deploy') return null;
    const cfg = flagValue(rest, ['--config', '-c']);
    return plan({ dirs: [pos.slice(1).map(abs).find(isDir) || (cfg ? path.dirname(abs(cfg)) : cwd)] });
  }
  if (name === 'surge') {
    const pos = getopt(rest, 'pde', VALUE_OPTS.surge);
    if (pos.length && SURGE_NON_UPLOAD.has(pos[0])) return null;
    const proj = flagValue(rest, ['--project', '-p']);
    return plan({ dirs: [proj ? abs(proj) : (pos[0] && isDir(abs(pos[0])) ? abs(pos[0]) : cwd)] });
  }
  return null;
}

// ---- 3. helpers for the content scans ----
function timedOut(e) {
  return !!e && (e.code === 'ETIMEDOUT' || e.code === 'ENOBUFS' || e.signal === 'SIGTERM');
}
function leftMs(what) {
  const t = BUDGET_MS - (Date.now() - START);
  if (t <= 0) throw new Incomplete(what);
  return t;
}
// A UTF-8 locale for git, so -i folds non-ASCII terms too (a C locale folds ASCII only).
const GIT_ENV = Object.assign({}, process.env, { LC_ALL: 'C.UTF-8' });
// git with a deadline: a timeout (or an output too large to hold) is Incomplete; any
// other failure (not a repo, unknown ref, missing config key) is just empty output.
function git(repo, args, what) {
  try {
    return execFileSync('git', [...repo.git, ...args],
      { stdio: ['ignore', 'pipe', 'ignore'], timeout: leftMs(what), maxBuffer: 64 * 1024 * 1024, env: GIT_ENV }).toString();
  } catch (e) {
    if (e instanceof Incomplete || timedOut(e)) throw new Incomplete(what);
    return '';
  }
}
function toplevel(dir) {
  try {
    return execFileSync('git', ['rev-parse', '--show-toplevel'],
      { cwd: dir, stdio: ['ignore', 'pipe', 'ignore'], timeout: 5000 }).toString().trim();
  } catch (_) { return ''; }
}
// The repository a push runs in: the git arguments that address it, and a root for
// messages and exemptions. It follows git -C (each one relative to the one before),
// --git-dir and --work-tree (or GIT_DIR / GIT_WORK_TREE before the command), and finds a
// bare repository too; null outside any repository.
function pushRepo(c, plan, expand) {
  let dir = c.cwd || process.cwd();
  for (const d of plan.dirs) if (!/\$\(|`/.test(expand(d))) dir = path.resolve(dir, expand(d));
  const pre = [];
  if (plan.gitDir) pre.push(`--git-dir=${path.resolve(dir, expand(plan.gitDir))}`);
  if (plan.workTree) pre.push(`--work-tree=${path.resolve(dir, expand(plan.workTree))}`);
  const ask = (args) => {
    try {
      return execFileSync('git', [...pre, ...args],
        { cwd: dir, stdio: ['ignore', 'pipe', 'ignore'], timeout: 5000 }).toString().trim();
    } catch (_) { return ''; }
  };
  const root = ask(['rev-parse', '--show-toplevel']) || ask(['rev-parse', '--absolute-git-dir']);
  return root ? { root, git: ['-C', root, ...pre] } : null;
}
// The remote a push goes to, when it is a configured remote (its remote-tracking refs
// say what it already has); null for a URL, a path or anything unknown.
function destRemote(repo, plan) {
  const remotes = git(repo, ['remote'], 'remotes').split('\n').map((x) => x.trim()).filter(Boolean);
  if (plan.repo) return remotes.includes(plan.repo) ? plan.repo : null;
  const cfg = (k) => git(repo, ['config', '--get', k], 'git config').trim();
  const cur = git(repo, ['symbolic-ref', '--short', '-q', 'HEAD'], 'current branch').trim();
  const r = (cur && cfg(`branch.${cur}.pushRemote`)) || cfg('remote.pushDefault') ||
    (cur && cfg(`branch.${cur}.remote`)) ||
    (remotes.includes('origin') ? 'origin' : (remotes.length === 1 ? remotes[0] : ''));
  return remotes.includes(r) ? r : null;
}
// The commits a push would send, as git revision arguments.
function pushRevs(repo, plan) {
  if (plan.del) return [];
  const revs = [];
  if (plan.mirror) revs.push('--all');
  if (plan.branches) revs.push('--branches');
  if (plan.tagsOnly || plan.followTags) revs.push('--tags');
  for (let spec of plan.refspecs) {
    spec = spec.replace(/^\+/, '');
    const src = spec.includes(':') ? spec.slice(0, spec.indexOf(':')) : spec;
    if (!src) continue;                                  // ":dst" deletes a remote ref
    if (/[*?[]/.test(src)) { revs.push('--branches', '--tags'); continue; }
    const sha = git(repo, ['rev-parse', '--verify', '--quiet', `${src}^{commit}`], 'refspec').trim();
    if (sha) revs.push(sha);
  }
  if (!plan.refspecs.length && !plan.branches && !plan.mirror && !plan.tagsOnly) revs.push('HEAD');
  return revs;
}
// What those commits carry that the destination does not have yet: every new
// commit's message, and every line its changes add (file names included). Only
// commits on the destination's remote-tracking refs are skipped; an unknown
// destination gets the whole history. (Reading only HEAD's tree and the working
// tree would miss a term removed at HEAD but still in an older commit, or one that
// lived only in a commit message.)
function historyText(repo, revs, remote, pat) {
  if (!revs.length) return '';
  const range = remote ? [...revs, '--not', `--remotes=${remote}`] : revs;
  const msgs = git(repo, ['log', '--format=%B', '-i', '-E', `--grep=${pat}`, ...range, '--'], 'commit messages');
  const diffs = git(repo, ['log', '-p', '-m', '-U0', '--no-color', '--no-ext-diff', '--format=',
    '-i', `-G${pat}`, ...range, '--'], 'commit contents');
  const re = new RegExp(pat, 'i');
  const names = git(repo, ['-c', 'core.quotepath=false', 'log', '-m', '--name-only', '--format=', ...range, '--'],
    'file names')
    .split('\n').filter((l) => re.test(l)).join('\n');
  return msgs + '\n' + names + '\n' + diffs.split('\n').filter((l) => !l.startsWith('-')).join('\n');
}
// HEAD (what the tip carries) + the tracked working tree, matching lines and file names only.
// core.quotepath=false keeps non-ASCII names as written instead of octal escapes.
function treeText(repo, pat) {
  const t = leftMs('tracked files');
  try {
    // The pattern goes through the environment and the repository through argv, never
    // into the sh -c string: a backtick in a term would run as command substitution.
    return execFileSync('/bin/sh', ['-c',
      '{ git "$@" grep -I -i -h -E "$GP_PAT" HEAD -- . ; git "$@" grep -I -i -h -E "$GP_PAT" -- . ;' +
      ' { git "$@" -c core.quotepath=false ls-tree -r --name-only HEAD ; git "$@" -c core.quotepath=false ls-files ; } | grep -i -E "$GP_PAT" ; } 2>/dev/null | head -c 200000 || true',
      'sh', ...repo.git],
      { stdio: ['ignore', 'pipe', 'ignore'], timeout: t, env: Object.assign({}, GIT_ENV, { GP_PAT: pat }) }
    ).toString();
  } catch (e) {
    if (timedOut(e)) throw new Incomplete('tracked files');
    return '';
  }
}
// Every term found in the names and contents of the files under `dir` (not following
// links; .git, node_modules and the `skip` folders left out; files over 5 MB and binary
// files skipped).
function dirTerms(dir, terms, skip) {
  const found = new Set();
  const lower = terms.map((t) => t.toLowerCase());
  const leave = new Set(['.git', 'node_modules', ...(skip || [])]);
  const stack = [dir];
  while (stack.length) {
    leftMs(`files under ${dir}`);
    const d = stack.pop();
    let ents = [];
    try { ents = fs.readdirSync(d, { withFileTypes: true }); } catch (_) { continue; }
    for (const e of ents) {
      const fp = path.join(d, e.name);
      if (!leave.has(e.name)) {
        const name = e.name.toLowerCase();
        lower.forEach((t, i) => { if (name.includes(t)) found.add(terms[i]); });
      }
      if (e.isDirectory()) {
        if (!leave.has(e.name)) stack.push(fp);
      } else if (e.isFile()) {
        let buf;
        try {
          if (fs.statSync(fp).size > 5 * 1024 * 1024) continue;
          buf = fs.readFileSync(fp);
        } catch (_) { continue; }
        if (buf.subarray(0, 8000).includes(0)) continue;          // binary
        const text = buf.toString('utf8').toLowerCase();
        lower.forEach((t, i) => { if (text.includes(t)) found.add(terms[i]); });
      }
    }
  }
  return found;
}
// A file's text (up to 5 MB); '' when it is missing, too big, or (with textOnly) binary.
function fileText(fp, textOnly) {
  try {
    const st = fs.statSync(fp);
    if (!st.isFile() || st.size >= 5 * 1024 * 1024) return '';
    const buf = fs.readFileSync(fp);
    return textOnly && buf.subarray(0, 8000).includes(0) ? '' : buf.toString('utf8');
  } catch (_) { return ''; }
}
function incompleteBlock(what, detail) {
  logLine(`BLOCK-INCOMPLETE scan=${what} cwd=${detail.cwd || '?'} cmd=${(detail.cmd || '?').slice(0, 200)}`);
  process.stderr.write(
    `🚫 BLOCKED by guard-publish: it could not finish checking what this command sends (${what}) ` +
    `within ${Math.round(BUDGET_MS / 1000)} s, so it cannot vouch for it.\n` +
    `Narrow it (push one branch, deploy a smaller folder), or run it yourself in a terminal if you are sure.\n`);
  process.exit(2);
}

// ---- 4. the hook ----
let data = {};
let cmd = '';

// Non-Bash surfaces. A PreToolUse(Bash) matcher never sees MCP or web tools;
// settings.json wires this same hook onto more matchers. WebFetch and WebSearch hand a
// URL or a query to a third party, so they are always scanned (WebFetch's `prompt` is
// left out: it stays with Claude, the URL goes to the site). Any other tool is scanned
// when its NAME is publish-class (deploy/publish/push/release/gist/upload, which covers
// mcp__<server>__deploy_* style); the rest pass silently.
function toolCall(terms) {
  const name = String(data.tool_name || '');
  const web = name === 'WebFetch' || name === 'WebSearch';
  if (!web && !/deploy|publish|push|release|gist|upload|create|update|post|send|comment|issue|message|commit|merge|share/i.test(name)) return;
  let input = data.tool_input || {};
  const hay = [];
  if (name === 'WebFetch' && typeof input === 'object') {
    input = Object.assign({}, input);
    delete input.prompt;
    // %-encoded runs decoded too, so a term in a query string still reads as itself
    hay.push(String(input.url || '').replace(/\+/g, ' ').replace(/(%[0-9a-fA-F]{2})+/g, (m) => {
      try { return decodeURIComponent(m); } catch (_) { return m; }
    }));
  }
  hay.push(JSON.stringify(input));
  const low = hay.join('\n').toLowerCase();
  const hits = [...new Set(terms.filter((t) => low.includes(t.toLowerCase())))];
  if (hits.length) {
    denyBlock(
      `🚫 BLOCKED by guard-publish: tool call ${name} would expose confidential term(s): ${hits.join(', ')}.\n` +
      `These are on your private "Confidential Terms" denylist.\n` +
      `Do NOT publish this. If it's a false positive, edit the term out of that vault page; if the content is genuinely confidential, remove it before publishing.`,
      { hits, surface: name, cwd: data.cwd }
    );
  }
}

function bash(terms) {
  const cwd0 = data.cwd || process.cwd();
  // Simple VAR=value assignments in the command, for $VAR in paths (then the environment,
  // as the shell would: `cd $HOME/app` must not become `/app`).
  const env = {};
  for (const m of cmd.matchAll(/(?:^|[;&|]|\s)([A-Za-z_][A-Za-z0-9_]*)=("[^"]*"|'[^']*'|[^\s;&|]+)/g)) {
    env[m[1]] = m[2].replace(/^["']|["']$/g, '');
  }
  scriptEnv = env;
  // $PWD and $(pwd) are the directory the command runs in, not this hook's own
  if (env.PWD === undefined) env.PWD = cwd0;
  const val = (n) => (env[n] !== undefined ? env[n] : (process.env[n] || ''));
  const expand = (p) => String(p)
    .replace(/\$\{([A-Za-z_][A-Za-z0-9_]*)\}/g, (_, n) => val(n))
    .replace(/\$\(\s*pwd\s*\)|`\s*pwd\s*`/g, () => val('PWD'))
    .replace(/\$([A-Za-z_][A-Za-z0-9_]*)/g, (_, n) => val(n))
    .replace(/^~(?=\/|$)/, os.homedir());

  const cmds = commands(cmd, cwd0, expand);
  // `!` git aliases run a shell command: read their commands and text as part of this one.
  const bodies = [];
  for (let k = 0; k < cmds.length && bodies.length < 16; k++) {
    const c = cmds[k], head = gitHead(c);
    if (!head || c.w[head.i] === undefined || c.w[head.i] === 'push') continue;
    const x = gitExpand(c, head);
    if (!x || x.shell === undefined) continue;
    const body = [x.shell, ...x.args].join(' ');
    bodies.push(body);
    // git runs a `!` alias at the top of the work tree
    cmds.push(...commands(body, toplevel(c.cwd || cwd0) || c.cwd, expand, 1));
  }
  const all = [cmd, ...bodies].join('\n');
  const pushes = cmds.map((c) => ({ c, plan: pushPlan(c) })).filter((x) => x.plan);
  const sends = cmds.map((c) => ({ c, plan: publishPlan(c, expand) })).filter((x) => x.plan);
  if (!pushes.length && !sends.length && !cmds.some(publishesAt) && !PUBLISH.some((re) => re.test(all))) return;

  // Exemptions are per push. A push reads its own repository without the terms
  // guard-exempt.json exempts for that repository. The command's text and the files it
  // names drop only the terms every publish in the command exempts, so a command that
  // just commits and pushes an exempted repo may name it, while a deploy, upload or post
  // beside the push gets no exemption at all (it has none of its own). What a deploy or
  // an upload sends is always read with every term.
  const scans = pushes.map(({ c, plan }) => {
    const repo = pushRepo(c, plan, expand);
    const ex = repo ? exemptTermsFor(repo.root) : new Set();
    return { plan, repo, ex, list: terms.filter((t) => !ex.has(t.toLowerCase())) };
  });
  let common = null;
  if (scans.length && !sends.length && !cmds.some((c) => publishesAt(c))) {
    for (const s of scans) common = common ? new Set([...common].filter((t) => s.ex.has(t))) : s.ex;
  }
  const textTerms = common ? terms.filter((t) => !common.has(t.toLowerCase())) : terms;

  const hits = new Set();
  const check = (text, list) => {
    const hay = String(text).toLowerCase();
    for (const t of list) if (hay.includes(t.toLowerCase())) hits.add(t);
  };

  // 4a. the command text, with literal base64 blobs decoded. Threshold 12 (not 40) so
  // a short confidential term encoded alone (e.g. "Acme" -> ~16 chars) is still decoded
  // and caught; decode is cheap and gibberish simply won't substring-match any term.
  const text = [all];
  for (const m of all.matchAll(/[A-Za-z0-9+/]{12,}={0,2}/g)) {
    try {
      const dec = Buffer.from(m[0], 'base64').toString('utf8');
      if (/[\x20-\x7e]/.test(dec)) text.push(dec);
    } catch (_) {}
  }
  // 4b. files referenced by the command (incl. `base64 < FILE`, `@FILE`, `--input FILE`, `< FILE`, `cat FILE`)
  // a bare path stops at ( ) and ` too, so the file in "$(cat FILE)" or `<FILE` is read
  const PP = `("[^"]+"|'[^']+'|(?:\\$\\(\\s*pwd\\s*\\)|[^\\s;&|<>()\`])+)`;
  const filePats = [
    new RegExp(`<\\s*${PP}`, 'g'),
    new RegExp(`(?:--input|-T|--upload-file|--data-binary|--data)\\s+@?${PP}`, 'g'),
    new RegExp(`(?:^|\\s)@${PP}`, 'g'),
    new RegExp(`(?:^|\\s)-[A-Za-z]*?d\\s*["']?@${PP}`, 'g'),   // curl -d@file, -sd@file, -d '@file'
    new RegExp(`(?:^|\\s)-[A-Za-z]*?T\\s*${PP}`, 'g'),           // curl -T file, -sT file, -sTfile
    new RegExp(`(?:^|\\s)["']@([^"'\\s]+)`, 'g'),                // a quoted '@file'
    new RegExp(`--data-urlencode[\\s=]+["']?[^\\s@"'=]*=?@${PP}`, 'g'), // curl --data-urlencode name@file
    new RegExp(`--(?:post|body)-file[\\s=]+${PP}`, 'g'),           // wget --post-file, --body-file
    new RegExp(`=@${PP}`, 'g'),             // curl -F field=@file / -d name=@file
    new RegExp(`\\bcat\\s+${PP}`, 'g'),
    new RegExp(`\\bbase64\\b(?:\\s+-{1,2}\\w+)*\\s+${PP}`, 'g'),
  ];
  const named = new Set();
  for (const re of filePats) {
    for (const m of all.matchAll(re)) {
      // quotes (escaped ones too) around the name and an @ before it are not part of it
      const p = (m[1] || '').replace(/^(?:\\?["'])+|(?:\\?["'])+$/g, '').replace(/^@(?:\\?["'])*/, '');
      if (!p || p.startsWith('-')) continue;
      // curl's own {a,b} list in a file name (-T "{a,b}") names each file
      const b = /^(.*)\{([^{}]*)\}(.*)$/.exec(p);
      if (b) for (const alt of b[2].split(',')) named.add(b[1] + alt + b[3]);
      else named.add(p);
    }
  }
  // A relative name is read in every directory the command's parts run in (after a cd,
  // under git -C, at the top of the work tree for a `!` alias).
  const dirs = new Set([cwd0, ...cmds.map((c) => c.cwd).filter(Boolean)]);
  for (const p of named) {
    const fp = expand(p);
    if (path.isAbsolute(fp)) text.push(fileText(fp, false));
    else for (const d of dirs) text.push(fileText(path.join(d, fp), false));
  }
  check(text.join('\n'), textTerms);

  // 4c. `git push`: what the push sends (historyText) plus HEAD and the tracked working
  // tree, each read with its own repository's term list.
  const escape = (list) => list.map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|');
  let pushRoot = '';
  for (const s of scans) {
    if (!s.repo) continue;
    if (!pushRoot) pushRoot = s.repo.root;
    if (!s.list.length) continue;                         // every term is exempt here
    const pat = escape(s.list);
    check(treeText(s.repo, pat), s.list);
    check(historyText(s.repo, pushRevs(s.repo, s.plan), destRemote(s.repo, s.plan), pat), s.list);
  }

  // 4d. `gh repo create --push` pushes the local history to a brand-new remote.
  for (const c of cmds) {
    if (path.basename(c.w[c.at]) !== 'gh' || c.w[c.at + 1] !== 'repo' || c.w[c.at + 2] !== 'create' || !c.w.includes('--push')) continue;
    const src = flagValue(c.w.slice(c.at + 3), ['--source', '-s']) || '.';
    const root = toplevel(path.resolve(c.cwd || process.cwd(), expand(src)));
    if (!root) continue;
    const repo = { root, git: ['-C', root] }, pat = escape(terms);
    check(treeText(repo, pat), terms);
    check(historyText(repo, ['HEAD'], null, pat), terms);
  }

  // 4e. deploys, uploads and posts: the folders and files they send.
  for (const { plan } of sends) {
    for (const f of plan.files) check(fileText(f, false), terms);
    for (const d of plan.dirs) for (const t of dirTerms(d, terms, plan.skip)) hits.add(t);
    for (const p of plan.paths) {
      let dir = false;
      try { dir = fs.statSync(p).isDirectory(); } catch (_) {}
      if (dir) for (const t of dirTerms(p, terms, plan.skip)) hits.add(t);
      else check(fileText(p, true), terms);
    }
  }

  if (hits.size) {
    const found = [...hits];
    let remote = '';
    try {
      remote = execSync('git remote get-url origin 2>/dev/null',
        { cwd: pushRoot || data.cwd || process.cwd(), stdio: ['ignore', 'pipe', 'ignore'], timeout: 3000 }).toString().trim();
    } catch (_) {}
    denyBlock(
      `🚫 BLOCKED by guard-publish: this publish-class command would expose confidential term(s): ${found.join(', ')}.\n` +
      `Where: ${pushRoot || data.cwd || '?'}${remote ? ' → ' + remote : ''}\n` +
      `These are on your private "Confidential Terms" denylist.\n` +
      `Do NOT publish this. If it's a false positive, edit the term out of that vault page; if the content is genuinely confidential, remove it before publishing.`,
      { hits: found, cwd: data.cwd, root: pushRoot, remote, cmd }
    );
  }
}

// From here the guard runs as a hook. Anything that goes wrong blocks while terms are
// armed (it cannot vouch for a call it did not check, and a crash would let the call
// run) and allows when the list is empty (there is nothing to protect). A scan that
// runs out of time blocks the same way, with its own message.
function internalError(e, armed) {
  const msg = e && e.message ? e.message : String(e);
  if (!armed) {
    try { process.stderr.write(`guard-publish: internal error (${msg}); the denylist is empty, so allowing\n`); } catch (_) {}
    process.exit(0);
  }
  logLine(`BLOCK-ERROR error=${msg} cwd=${data.cwd || '?'} cmd=${(cmd || String(data.tool_name || '?')).slice(0, 200)}`);
  try {
    process.stderr.write(
      `🚫 BLOCKED by guard-publish: internal error (${msg}), so it cannot vouch for this call.\n` +
      `Run it yourself in a terminal if you are sure, or fix guard-publish.js.\n`);
  } catch (_) {}
  process.exit(2);
}

let armed = true;
try {
  const { terms } = loadTerms();
  armed = terms.length > 0;
  const raw = fs.readFileSync(0, 'utf8');
  if (!armed) allowSilently();
  data = JSON.parse(raw || '{}') || {};
  if (data.tool_name !== 'Bash') {
    toolCall(terms);
  } else {
    // String() so a malformed non-string command reads as text instead of crashing
    cmd = String((data.tool_input && data.tool_input.command) || '');
    if (cmd.trim()) bash(terms);
  }
  allowSilently();
} catch (e) {
  if (e instanceof Incomplete) incompleteBlock(e.message, { cwd: data.cwd, cmd });
  internalError(e, armed);
}
