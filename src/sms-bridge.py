#!/usr/bin/env python3
"""
SMS reply bridge — polls tasks/ for inbound SMS tasks (source=twilio_sms)
and polls results/ for matching reply files, then sends each result back to
the original sender via the Twilio REST API.

Mirrors src/telegram-bridge.py for the SMS path. Inbound SMS itself lands
via skills/phone-conversation/scripts/conversation-server.ts (POST /twilio/sms),
which writes tasks/task-{ts}.txt with:

    id: task-{ts}
    timestamp: <iso>
    task: SMS from +1...: body
    source: twilio_sms
    from: +1...

When the proactive loop processes that task and writes results/task-{ts}.txt,
this bridge picks it up, sends the body to From via Twilio Messages API, and
deletes the result file.

Required env (from .env):
    TWILIO_ACCOUNT_SID
    TWILIO_AUTH_TOKEN
    TWILIO_PHONE_NUMBER

Optional:
    SMS_BRIDGE_POLL_SECS  (default 2)
    SMS_BRIDGE_MAX_BODY   (default 1500 — Twilio splits >1600)
"""
import base64
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[1]
TASKS_DIR = WORKSPACE / "tasks"
RESULTS_DIR = WORKSPACE / "results"
STATE_DIR = WORKSPACE / "state"
HEARTBEAT = STATE_DIR / "sms-bridge.heartbeat"

# Loaded from .env
TWILIO_ACCOUNT_SID = ""
TWILIO_AUTH_TOKEN = ""
TWILIO_PHONE_NUMBER = ""
POLL_SECS = 2
MAX_BODY = 1500


def load_env() -> None:
    global TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER, POLL_SECS, MAX_BODY
    env_path = WORKSPACE / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        v = v.strip().strip('"').strip("'")
        if k.strip() == "TWILIO_ACCOUNT_SID":
            TWILIO_ACCOUNT_SID = v
        elif k.strip() == "TWILIO_AUTH_TOKEN":
            TWILIO_AUTH_TOKEN = v
        elif k.strip() == "TWILIO_PHONE_NUMBER":
            TWILIO_PHONE_NUMBER = v
        elif k.strip() == "SMS_BRIDGE_POLL_SECS":
            try:
                POLL_SECS = max(1, int(v))
            except ValueError:
                pass
        elif k.strip() == "SMS_BRIDGE_MAX_BODY":
            try:
                MAX_BODY = max(160, int(v))
            except ValueError:
                pass


HDR_KV_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.+?)\s*$")


def parse_task_file(path: Path) -> dict | None:
    """Parse the simple key:value header in a task file."""
    try:
        text = path.read_text()
    except Exception:
        return None
    out: dict[str, str] = {}
    for line in text.splitlines():
        m = HDR_KV_RE.match(line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def send_sms(to_num: str, body: str) -> tuple[bool, str]:
    if not (TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_PHONE_NUMBER):
        return False, "missing TWILIO_* env"
    url = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Messages.json"
    body = body[:MAX_BODY]
    data = urllib.parse.urlencode({
        "From": TWILIO_PHONE_NUMBER,
        "To": to_num,
        "Body": body,
    }).encode()
    req = urllib.request.Request(url, data=data)
    auth = (TWILIO_ACCOUNT_SID + ":" + TWILIO_AUTH_TOKEN).encode()
    req.add_header("Authorization", "Basic " + base64.b64encode(auth).decode())
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = json.loads(resp.read())
            return True, payload.get("sid", "")
    except urllib.error.HTTPError as e:
        try:
            body_err = e.read().decode("utf-8", errors="replace")[:200]
        except Exception:
            body_err = ""
        return False, f"HTTP {e.code}: {body_err}"
    except Exception as e:
        return False, str(e)


def main() -> None:
    load_env()
    if not (TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_PHONE_NUMBER):
        print("SMS bridge: TWILIO_* env vars missing, exiting.", flush=True)
        sys.exit(1)

    STATE_DIR.mkdir(exist_ok=True)
    TASKS_DIR.mkdir(exist_ok=True)
    RESULTS_DIR.mkdir(exist_ok=True)

    pending: dict[str, str] = {}  # task_id -> from_number
    print(f"SMS bridge started. Polling tasks/ + results/ every {POLL_SECS}s.", flush=True)

    # Backfill: any existing tasks/ files from twilio_sms get registered so that
    # results that show up later still find a destination.
    for p in TASKS_DIR.glob("task-*.txt"):
        info = parse_task_file(p) or {}
        if info.get("source") == "twilio_sms" and info.get("from"):
            pending[info.get("id", p.stem)] = info["from"]

    while True:
        try:
            HEARTBEAT.write_text(str(int(time.time())))
        except Exception:
            pass

        # 1. Index any new SMS tasks.
        for p in TASKS_DIR.glob("task-*.txt"):
            info = parse_task_file(p) or {}
            if info.get("source") != "twilio_sms":
                continue
            task_id = info.get("id") or p.stem
            sender = info.get("from")
            if not sender:
                continue
            if task_id not in pending:
                pending[task_id] = sender
                print(f"  registered SMS task {task_id} from {sender}", flush=True)

        # 2. Deliver any results matching pending SMS tasks.
        for task_id in list(pending.keys()):
            result_path = RESULTS_DIR / f"{task_id}.txt"
            if not result_path.exists():
                continue
            try:
                body = result_path.read_text().strip()
            except Exception as e:
                print(f"  read failed for {result_path}: {e}", flush=True)
                continue
            if not body:
                # Empty result — drop the placeholder so we don't loop on it.
                result_path.unlink(missing_ok=True)
                pending.pop(task_id, None)
                continue
            sender = pending[task_id]
            ok, sid_or_err = send_sms(sender, body)
            if ok:
                print(f"  → SMS to {sender} (sid={sid_or_err}, {len(body)}b)", flush=True)
                result_path.unlink(missing_ok=True)
                pending.pop(task_id, None)
            else:
                # Permanent failure (4xx) shouldn't loop forever — drop after 5 min.
                age = time.time() - result_path.stat().st_mtime
                if age > 300:
                    print(f"  giving up on {task_id} after {int(age)}s: {sid_or_err}", flush=True)
                    pending.pop(task_id, None)
                else:
                    print(f"  send failed for {task_id}: {sid_or_err} (will retry)", flush=True)

        time.sleep(POLL_SECS)


if __name__ == "__main__":
    main()
