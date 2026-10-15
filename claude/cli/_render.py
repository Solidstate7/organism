#!/usr/bin/env python3
"""Renderer for `organism status` / `organism agents`, turns the machine formats
(the installer's pipe-delimited --list, launchctl's tab table, the state/ marker
files) into one aligned, color-coded table. Color follows convention: green =
running/ok, red = failed, yellow = stale/attention, dim = inactive/secondary.
Plain text when piped or NO_COLOR is set; CLICOLOR_FORCE=1 forces color."""
import os, sys, subprocess, datetime

MODE, REPO = sys.argv[1], sys.argv[2]
HOME = os.path.expanduser("~")
STATE = os.path.join(HOME, ".claude", "state")

on = os.environ.get("CLICOLOR_FORCE") == "1" or (
    sys.stdout.isatty() and not os.environ.get("NO_COLOR"))
def c(code, txt): return f"\033[{code}m{txt}\033[0m" if on else txt
B, DIM, G, R, Y, C = "1", "2", "32", "31", "33", "36"

def sh(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=30).stdout
    except Exception:
        return ""

# 1. the manifest (+ host-local file): name|role|assigned|schedule|marker
rows = []
for ln in sh(["bash", os.path.join(REPO, "launchd", "install-agents.sh"), "--list"]).splitlines():
    p = ln.strip().split("|")
    if len(p) >= 4 and p[1] in ("per_host", "singleton_bookkeeping", "singleton_sideeffect"):
        rows.append({"name": p[0], "role": p[1], "here": p[2] == "yes",
                     "sched": p[3], "marker": p[4] if len(p) > 4 else ""})

# 2. launchd reality: label -> (pid, last exit status)
live = {}
for ln in sh(["launchctl", "list"]).splitlines():
    p = ln.split("\t")
    if len(p) == 3 and ".organism." in p[2]:
        live[p[2].split(".organism.", 1)[1]] = (p[0], p[1])

def pretty_sched(s):
    W = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
    try:
        if s == "keepalive":
            return "always on"
        k, *a = s.split(":")
        if k == "cal":
            return f"daily {int(a[0]):02d}:{int(a[1]):02d}"
        if k == "calw":
            return f"{W[int(a[0])]} {int(a[1]):02d}:{int(a[2]):02d}"
        if k == "interval":
            n = int(a[0]); return f"every {n//60}m" if n >= 60 else f"every {n}s"
    except Exception:
        pass
    return s

def stale_after(s):  # schedule-aware staleness horizon, in days
    return 2 if s.startswith("cal:") else 8 if s.startswith("calw:") else 2

today = datetime.date.today()
def marker_cell(r):
    if not r["marker"]:
        return c(DIM, "-")
    f = os.path.join(STATE, r["marker"] + "-last-success")
    try:
        d = datetime.date.fromisoformat(open(f).read().strip())
    except Exception:
        return c(DIM, "never")
    age = (today - d).days
    label = "today" if age == 0 else f"{age}d ago"
    return c(Y, f"{label} ⚠") if age > stale_after(r["sched"]) else c(G, label)

def state_cell(r):
    if not r["here"]:
        return c(DIM, "○ other machine")
    if r["name"] not in live:
        return c(R, "✗ not loaded")
    pid, status = live[r["name"]]
    if status not in ("0", "-"):
        return c(R, f"✗ exit {status}")
    return c(G, "● running" if pid != "-" else "● scheduled")

ROLE = {"per_host": "machine", "singleton_bookkeeping": "singleton",
        "singleton_sideeffect": "singleton"}

rows.sort(key=lambda r: (not r["here"], r["role"] != "per_host", r["name"]))
title = "organism agents: this machine" if MODE == "status" else "organism agents: manifest"
print(c(B, title))
head = ("", "JOB", "STATE", "ROLE", "SCHEDULE", "LAST OK")
data = []
for r in rows:
    if MODE == "status" and not r["here"]:
        continue
    data.append(("", r["name"], state_cell(r), ROLE[r["role"]],
                 pretty_sched(r["sched"]), marker_cell(r) if MODE == "status" else r["marker"] or c(DIM, "-")))

def vislen(s):  # width without ANSI codes
    import re
    return len(re.sub(r"\033\[[0-9;]*m", "", s))

w = [max(vislen(row[i]) for row in [head] + data) for i in range(6)]
def fmt(row, dimhead=False):
    cells = [row[i] + " " * (w[i] - vislen(row[i])) for i in range(6)]
    line = "  ".join(cells).rstrip()
    return c(DIM, line) if dimhead else line
print(fmt(head, dimhead=True))
for row in data:
    print(fmt(row))
if MODE == "status":
    n_live = sum(1 for r in rows if r["here"] and r["name"] in live)
    print(c(DIM, f"\n{n_live} loaded · markers in ~/.claude/state · full manifest: organism agents"))
