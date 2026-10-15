#!/usr/bin/env python3
"""One alert channel for every Organism job. Every alert routes here, and this sends
it to the Telegram bot you configure (if any) plus a local macOS banner. Keeping it in
one place means a new alert source just calls this.

  CLI:  python3 ~/.claude/hooks/alert.py --title "..." --text "..." [--no-banner]
  lib:  from alert import send; send(title, text)

Best-effort and silent: never raises, never blocks the caller. Telegram creds live
in ~/.claude/state/telegram-alerts.json (outside the synced vault)."""
import os, sys, json, argparse, subprocess, datetime
import urllib.request, urllib.parse
import socket, ssl, http.client

HOME = os.environ.get("HOME") or os.path.expanduser("~")
CREDS = os.path.join(HOME, ".claude", "state", "telegram-alerts.json")
# api.telegram.org is normally a single A record; when the route to that datacenter
# dies upstream the Bot API is unreachable even though the network is otherwise fine.
# On failure we re-attempt against these known Bot-API IPs, connecting to the IP directly
# while keeping TLS SNI + cert validation pinned to api.telegram.org (the urllib/socket
# equivalent of `curl --resolve`). Best-effort: if the whole DC range is unreachable
# these also fail and send() falls back to a banner so the alert is never fully silent.
TELEGRAM_API_FALLBACK_IPS = ["149.154.167.220", "149.154.167.197"]
# Ledger: every alert is recorded here verbatim BEFORE fan-out, so a claim can
# never fire without a checkable trace. Recording on
# attempt (not on delivery) means an alert that failed to reach the phone is still
# recoverable here once the user looks. Append-only JSONL: {ts, title, text}.
LEDGER = os.path.join(HOME, ".claude", "logs", "alerts.jsonl")


def _ledger(title, text):
    try:
        os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
        row = {"ts": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
               "title": title or "", "text": text or ""}
        with open(LEDGER, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _send_one(tok, chat, body, ip=None, timeout=6, reply_markup=None):
    """One sendMessage attempt. ip=None uses system DNS; otherwise connect to that IP
    with TLS SNI + cert still validated against api.telegram.org. Returns True iff the
    API replied {"ok": true}. Raises on any transport error (caller catches).
    reply_markup: optional Bot-API inline keyboard dict (reconcile's ✅ Ack-all
    button; whatever serves your bot handles its callback, not this repo)."""
    path = f"/bot{tok}/sendMessage"
    payload = {"chat_id": chat, "text": body}
    if reply_markup is not None:
        payload["reply_markup"] = json.dumps(reply_markup)
    data = urllib.parse.urlencode(payload).encode()
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    if ip is None:
        req = urllib.request.Request("https://api.telegram.org" + path, data=data, headers=headers)
        raw = urllib.request.urlopen(req, timeout=timeout).read()
    else:
        sock = socket.create_connection((ip, 443), timeout=timeout)
        try:
            tls = ssl.create_default_context().wrap_socket(sock, server_hostname="api.telegram.org")
            conn = http.client.HTTPSConnection("api.telegram.org", 443, timeout=timeout)
            conn.sock = tls
            conn.request("POST", path, body=data, headers=headers)
            raw = conn.getresponse().read()
            conn.close()
        finally:
            try:
                sock.close()
            except Exception:
                pass
    try:
        return bool(json.loads(raw.decode()).get("ok"))
    except Exception:
        return False


def _telegram(title, text, reply_markup=None):
    """Best-effort Telegram delivery. Returns True iff a message was accepted by the
    API, so a failed delivery can trigger a banner fallback instead of going silent.
    Tries system DNS first, then the curated Bot-API IPs to route around a dead DC."""
    try:
        c = json.load(open(CREDS, encoding="utf-8"))
    except Exception:
        return False
    tok, chat = c.get("bot_token"), c.get("chat_id")
    if not tok or not chat:
        return False
    body = (f"{title}\n{text}".strip() if title else text)[:3500]
    for ip in [None] + TELEGRAM_API_FALLBACK_IPS:
        try:
            if _send_one(tok, chat, body, ip=ip, reply_markup=reply_markup):
                return True
        except Exception:
            continue
    return False


def _banner(title, text):
    try:
        t = (text or "").replace("\\", "").replace('"', "'")[:200]
        ti = (title or "Alert").replace("\\", "").replace('"', "'")[:80]
        subprocess.run(["osascript", "-e", f'display notification "{t}" with title "{ti}"'],
                       capture_output=True, timeout=10)
    except Exception:
        pass


def send(title, text, banner=True, reply_markup=None):
    _ledger(title, text)  # record first, the ledger trace must survive a failed send
    if banner:
        _banner(title, text)
    delivered = _telegram(title, text, reply_markup=reply_markup)
    if not delivered and not banner:
        # Telegram unreachable AND no banner was shown, fall back to a banner so the
        # alert is never fully silent. Silence must mean "the job didn't run", not "the
        # phone channel was down", or the dead-man's-switch is defeated.
        _banner((title or "Alert") + " ⚠ Telegram unreachable", text)
    return delivered


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--title", default="Alert")
    ap.add_argument("--text", default="")
    ap.add_argument("--no-banner", action="store_true")
    ap.add_argument("--require-delivery", action="store_true",
                    help="exit 3 (not 0) if Telegram delivery was not confirmed, so a caller "
                         "can log/retry a silent failure. Default stays exit 0 for back-compat.")
    a = ap.parse_args()
    delivered = send(a.title, a.text, banner=not a.no_banner)
    sys.exit(0 if (delivered or not a.require_delivery) else 3)
