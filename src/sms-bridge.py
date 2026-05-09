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


DELIVERY_CHECK_DELAY = 4   # seconds to wait before polling status
DELIVERY_FAIL_STATES = {"undelivered", "failed"}
DELIVERY_OK_STATES = {"delivered", "sent", "queued", "sending", "accepted"}


def _twilio_auth_header() -> str:
    auth = (TWILIO_ACCOUNT_SID + ":" + TWILIO_AUTH_TOKEN).encode()
    return "Basic " + base64.b64encode(auth).decode()


def send_sms(to_num: str, body: str) -> tuple[bool, str]:
    """Enqueue an SMS via Twilio. Returns (ok, sid_or_err) where ok=True means
    the message was accepted by Twilio (HTTP 201). Delivery status comes
    later via twilio_message_status()."""
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
    req.add_header("Authorization", _twilio_auth_header())
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


def twilio_message_status(sid: str) -> tuple[str, str]:
    """Fetch (status, error_code) for a Twilio Message SID. Empty strings on error."""
    if not (TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and sid):
        return "", ""
    url = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Messages/{sid}.json"
    req = urllib.request.Request(url)
    req.add_header("Authorization", _twilio_auth_header())
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            payload = json.loads(resp.read())
            return payload.get("status", ""), str(payload.get("error_code") or "")
    except Exception:
        return "", ""


def telegram_fallback(body: str, reason: str) -> bool:
    """Drop a proactive- file so the existing telegram-bridge delivers it.
    Use when SMS shows undeliverable so the owner still gets the reply."""
    try:
        RESULTS_DIR.mkdir(exist_ok=True)
        ts = int(time.time() * 1000)
        path = RESULTS_DIR / f"proactive-sms-fallback-{ts}.txt"
        path.write_text(f"[SMS undelivered: {reason} — sent via Telegram instead]\n\n{body}")
        return True
    except Exception:
        return False


def main() -> None:
    load_env()
    if not (TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_PHONE_NUMBER):
        print("SMS bridge: TWILIO_* env vars missing, exiting.", flush=True)
        sys.exit(1)

    STATE_DIR.mkdir(exist_ok=True)
    TASKS_DIR.mkdir(exist_ok=True)
    RESULTS_DIR.mkdir(exist_ok=True)

    pending: dict[str, str] = {}  # task_id -> from_number
    # SIDs we've enqueued but haven't yet confirmed delivery for.
    # value = (task_id, sender, body, sent_ts)
    in_flight: dict[str, tuple[str, str, str, float]] = {}
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
                print(f"  → SMS enqueued to {sender} (sid={sid_or_err}, {len(body)}b)", flush=True)
                # Don't unlink yet — wait for delivery status to confirm. If
                # carrier rejects (e.g. A2P 10DLC error 30034), we fall back
                # to Telegram so the owner still gets the reply.
                in_flight[sid_or_err] = (task_id, sender, body, time.time())
                result_path.unlink(missing_ok=True)
                pending.pop(task_id, None)
            else:
                # HTTP-level failure (Twilio rejected the request, e.g. auth) —
                # fall back to Telegram once, then drop. Don't loop forever.
                age = time.time() - result_path.stat().st_mtime
                if age > 60:
                    print(f"  giving up on {task_id} after {int(age)}s: {sid_or_err}", flush=True)
                    telegram_fallback(body, f"twilio enqueue failed: {sid_or_err}")
                    result_path.unlink(missing_ok=True)
                    pending.pop(task_id, None)
                else:
                    print(f"  enqueue failed for {task_id}: {sid_or_err} (will retry)", flush=True)

        # 3. Verify delivery status for in-flight messages. If Twilio reports
        # undelivered/failed, fall back to Telegram so the user still gets it.
        # We give Twilio a few seconds before checking — initial state is
        # always "queued" and only flips once the carrier responds.
        now = time.time()
        for sid in list(in_flight.keys()):
            task_id, sender, body, sent_ts = in_flight[sid]
            age = now - sent_ts
            if age < DELIVERY_CHECK_DELAY:
                continue
            status, err = twilio_message_status(sid)
            if status in DELIVERY_FAIL_STATES:
                print(f"  ✗ SMS {sid} status={status} err={err} → Telegram fallback", flush=True)
                telegram_fallback(body, f"twilio status={status} err={err}")
                in_flight.pop(sid, None)
            elif status in {"delivered", "sent"}:
                print(f"  ✓ SMS {sid} delivered", flush=True)
                in_flight.pop(sid, None)
            elif age > 60:
                # Stuck in queued/sending for >60s — treat as failed and fall back.
                print(f"  ⚠ SMS {sid} stuck status={status} after {int(age)}s → Telegram fallback", flush=True)
                telegram_fallback(body, f"twilio stuck status={status}")
                in_flight.pop(sid, None)
            elif status and status not in DELIVERY_OK_STATES:
                # Unknown status — log once and let it age out.
                print(f"  ? SMS {sid} unexpected status={status}", flush=True)

        time.sleep(POLL_SECS)


if __name__ == "__main__":
    main()
