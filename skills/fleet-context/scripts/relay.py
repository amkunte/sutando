#!/usr/bin/env python3
"""fleet-context relay — share task-context / preferences across the fleet.

When a node accepts a non-trivial task from the owner (Viper), or captures a
durable preference the owner stated in passing, it calls this to:
  1. APPEND a durable, append-only line to this node's own log
     `<memory>/fleet/context-<NodeName>.md`  (synced to peers via the memory repo)
  2. POST a `context:` / `pref:` line to #bot2bot for INSTANT peer awareness
     (the other node ingests it live via the peer pipeline).

Node-parameterized (reads `node_name` from discord-config) so it's the SAME code
on every node — Maverick writes context-Maverick.md, Goose writes context-Goose.md,
each node only ever writes its OWN file → zero shared-file git conflicts.

Usage:
  relay.py --kind context --summary "Viper asked me to <X>; status <Y>"
  relay.py --kind pref    --summary "Viper prefers <X>"   # save to memory FIRST
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

MEMORY_DIR = Path(
    os.environ.get("SUTANDO_MEMORY_DIR",
                   str(Path.home() / ".claude" / "projects" / "-Users-abhi-sutando" / "memory"))
).expanduser()
FLEET_DIR = MEMORY_DIR / "fleet"


def _workspace() -> Path:
    return Path(os.environ.get("SUTANDO_WORKSPACE", str(Path.home() / ".sutando" / "workspace"))).expanduser()


def _discord_config() -> dict:
    try:
        return json.loads((_workspace() / "state" / "discord-config.json").read_text())
    except Exception:
        return {}


def node_name() -> str:
    return _discord_config().get("node_name") or os.environ.get("SUTANDO_NODE_NAME") or "Unknown"


def bot2bot_channel() -> str | None:
    return _discord_config().get("channels", {}).get("bot2bot")


def _discord_token() -> str:
    tok = os.environ.get("DISCORD_BOT_TOKEN", "")
    if tok:
        return tok
    env = Path.home() / ".claude" / "channels" / "discord" / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.startswith("DISCORD_BOT_TOKEN="):
                return line.split("=", 1)[1].strip()
    return ""


def append_log(node: str, kind: str, summary: str) -> Path:
    FLEET_DIR.mkdir(parents=True, exist_ok=True)
    log = FLEET_DIR / f"context-{node}.md"
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    line = f"- [{node} {ts}] ({kind}) {summary.strip()}\n"
    with open(log, "a") as f:        # append-only — never rewrites peers' lines
        f.write(line)
    return log


def post_bot2bot(kind: str, summary: str, node: str) -> bool:
    cid = bot2bot_channel()
    tok = _discord_token()
    if not cid or not tok:
        print("WARN: no bot2bot channel id or token; posted to log only", file=sys.stderr)
        return False
    content = f"{kind}: {node} → fleet — {summary.strip()}"
    body = json.dumps({"content": content[:1900], "allowed_mentions": {"parse": []}}).encode()
    req = urllib.request.Request(
        f"https://discord.com/api/v10/channels/{cid}/messages",
        data=body, method="POST",
        headers={"Authorization": f"Bot {tok}", "Content-Type": "application/json",
                 "User-Agent": "fleet-context/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return 200 <= r.status < 300
    except urllib.error.HTTPError as e:
        print(f"WARN: bot2bot post failed HTTP {e.code}", file=sys.stderr)
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", choices=["context", "pref"], default="context")
    ap.add_argument("--summary", required=True)
    ap.add_argument("--no-post", action="store_true", help="log only, skip #bot2bot")
    a = ap.parse_args()
    node = node_name()
    log = append_log(node, a.kind, a.summary)
    posted = False if a.no_post else post_bot2bot(a.kind, a.summary, node)
    print(f"fleet-context: appended to {log.name}" + (f"; relayed to #bot2bot" if posted else "; log-only"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
