#!/usr/bin/env python3
"""Charlie owner-tier capability: relay content to Maverick via #bot2bot.

Option B, Charlie's half. When an OWNER-tier message tells Charlie to hand
something to Maverick — e.g. "Charlie, send that summary to Maverick" or "pass
this to bot2bot" — Charlie POSTs it into the shared #bot2bot channel (in the
owner's server) via its own Discord token. Maverick observes / processes that
channel.

Content resolution:
  - "send <explicit text> to maverick"          → relay that text
  - "send this / that / it to maverick"         → relay Charlie's LAST reply
    (the responder stores it at state/last-reply.txt)

This is owner-gated (only callers Charlie treats as owner reach here) and the
ONLY thing it does is post text to one channel — it cannot command Maverick.
The trust boundary lives on Maverick's side: Maverick treats #bot2bot peer posts
as untrusted informational coordination, never as owner commands.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

# Shared coordination channel in the OWNER's server.
BOT2BOT_CHANNEL_ID = os.environ.get("CHARLIE_BOT2BOT_CHANNEL_ID", "1512203128294998076")
API = "https://discord.com/api/v10"

# Intent: "<relay-verb> ... (to) maverick | bot2bot"
RELAY_INTENT_RE = re.compile(
    r"\b(send|pass|relay|forward|hand|give|share)\b.*\b(maverick|bot2bot|bot-2-bot|<@1511143214500024361>)\b",
    re.IGNORECASE,
)
# Anaphora — "this / that / it / the summary" → use Charlie's last reply.
ANAPHORA_RE = re.compile(
    r"\b(this|that|it|the\s+(summary|above|last\s+(one|reply|message)|previous))\b",
    re.IGNORECASE,
)


def is_relay_command(message: str) -> bool:
    return bool(RELAY_INTENT_RE.search(message))


def _token() -> str:
    cdir = Path.home() / ".claude" / "channels" / "discord-charlie"
    for line in (cdir / ".env").read_text().splitlines():
        if line.startswith("DISCORD_BOT_TOKEN="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError("Charlie token not found")


def _post_to_bot2bot(content: str) -> bool:
    body = json.dumps({"content": content[:1900]}).encode()
    req = urllib.request.Request(
        f"{API}/channels/{BOT2BOT_CHANNEL_ID}/messages",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bot {_token()}",
            "Content-Type": "application/json",
            "User-Agent": "charlie-node/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return 200 <= r.status < 300
    except urllib.error.HTTPError:
        return False


def _extract_payload(message: str, last_reply: str | None) -> tuple[str | None, str]:
    """Return (payload, mode). mode is 'anaphora' or 'explicit' or 'empty'."""
    if ANAPHORA_RE.search(message):
        if last_reply and last_reply.strip():
            return last_reply.strip(), "anaphora"
        return None, "anaphora-missing"
    # explicit: strip the relay verb + target, keep the rest as the payload
    stripped = re.sub(
        r"<@!?\d+>|\b(hey|charlie|please|can you|could you|send|pass|relay|forward|"
        r"hand|give|share|to|the|maverick|bot2bot|bot-2-bot)\b",
        " ", message, flags=re.IGNORECASE,
    )
    stripped = re.sub(r"\s+", " ", stripped).strip(" :,-")
    if len(stripped) >= 3:
        return stripped, "explicit"
    return None, "empty"


def handle(message: str, last_reply: str | None) -> str:
    """Owner-tier entry. Posts to #bot2bot; returns a reply for the responder."""
    payload, mode = _extract_payload(message, last_reply)
    if mode == "anaphora-missing":
        return ("You asked me to send 'this' to Maverick, but I don't have a recent "
                "message to relay. Tell me what to send, or ask me something first.")
    if not payload:
        return ("Tell me what to send to Maverick — e.g. 'Charlie, send that summary to "
                "Maverick', or 'pass <text> to bot2bot'.")
    relayed = f"📨 Relayed from Charlie (at owner's request):\n\n{payload}"
    if _post_to_bot2bot(relayed):
        preview = payload[:120] + ("…" if len(payload) > 120 else "")
        return f"✅ Sent to Maverick via #bot2bot:\n> {preview}"
    return ("I couldn't post to #bot2bot — check that my role there still has Send "
            "Messages (Discord rejected the post).")
