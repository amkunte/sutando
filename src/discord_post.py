#!/usr/bin/env python3
"""Post a message to a Discord channel via the bot token.

Usage:
    discord_post.py <channel_id> "message text"
    echo "message" | discord_post.py <channel_id>

Reads the bot token from ~/.claude/channels/discord/.env (DISCORD_BOT_TOKEN).
Chunks at Discord's 2000-char message limit. Used by skills that deliver to a
specific channel (morning-briefing → #dailybriefings, siemens prep → #siemens)
deterministically, bypassing the proactive-DM routing.
"""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

_TOK_FILE = Path.home() / ".claude" / "channels" / "discord" / ".env"
_UA = "DiscordBot (https://github.com/amkunte/sutando, 1.0)"


def _token() -> str:
    for line in _TOK_FILE.read_text().splitlines():
        if line.startswith("DISCORD_BOT_TOKEN="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("discord_post: DISCORD_BOT_TOKEN not found")


def post(channel_id: str, text: str) -> None:
    tok = _token()
    # Discord hard-limits a message to 2000 chars; chunk with margin.
    chunks = [text[i:i + 1900] for i in range(0, len(text), 1900)] or [""]
    for chunk in chunks:
        body = json.dumps({"content": chunk}).encode()
        req = urllib.request.Request(
            f"https://discord.com/api/v10/channels/{channel_id}/messages",
            data=body, method="POST",
            headers={"Authorization": f"Bot {tok}", "User-Agent": _UA,
                     "Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=20)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("usage: discord_post.py <channel_id> [message|stdin]")
    cid = sys.argv[1]
    msg = sys.argv[2] if len(sys.argv) > 2 else sys.stdin.read()
    if not msg.strip():
        raise SystemExit("discord_post: empty message")
    post(cid, msg)
    print(f"posted to {cid}")
