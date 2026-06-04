#!/usr/bin/env python3
"""Shared helpers for the exec-approval skill.

Self-contained: resolves the workspace + Discord config the same way the
core does, but does NOT import from src/ (so the skill stays portable and a
broken src/ never takes the safety gate down with it). Python 3.9-safe.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.request
import urllib.error
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
POLICY_FILE = SKILL_DIR / "policy.json"
DISCORD_ENV = Path.home() / ".claude" / "channels" / "discord" / ".env"
_UA = "DiscordBot (https://github.com/amkunte/sutando, 1.0)"


def resolve_workspace() -> Path:
    """Mirror the core's resolution: $SUTANDO_WORKSPACE, else ~/.sutando/workspace."""
    env = os.environ.get("SUTANDO_WORKSPACE")
    if env:
        return Path(os.path.expanduser(env))
    return Path.home() / ".sutando" / "workspace"


def approvals_dir() -> Path:
    d = resolve_workspace() / "state" / "approvals"
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_policy() -> dict:
    return json.loads(POLICY_FILE.read_text())


def discord_config() -> dict:
    p = resolve_workspace() / "state" / "discord-config.json"
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def approvals_channel_id() -> str:
    """The #approvals channel id from discord-config.json, '' if unset."""
    return str(discord_config().get("channels", {}).get("approvals", "") or "")


def _discord_token() -> str:
    for line in DISCORD_ENV.read_text().splitlines():
        if line.startswith("DISCORD_BOT_TOKEN="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError("DISCORD_BOT_TOKEN not found")


def post_to_approvals(text: str) -> bool:
    """Post to #approvals via the bot. Returns True on success, False (and
    prints a warning) on any failure — the caller still records the pending
    approval to disk so nothing is silently lost."""
    cid = approvals_channel_id()
    if not cid:
        print("[exec-approval] WARN: no 'approvals' channel in discord-config.json; approval not posted to Discord")
        return False
    try:
        tok = _discord_token()
    except Exception as e:
        print(f"[exec-approval] WARN: {e}; approval not posted to Discord")
        return False
    body = json.dumps({"content": text[:1990]}).encode()
    req = urllib.request.Request(
        f"https://discord.com/api/v10/channels/{cid}/messages",
        data=body, method="POST",
        headers={"Authorization": f"Bot {tok}", "User-Agent": _UA,
                 "Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(req, timeout=20)
        return True
    except urllib.error.HTTPError as e:
        print(f"[exec-approval] WARN: Discord post failed ({e.code}); approval recorded to disk only")
        return False
    except Exception as e:
        print(f"[exec-approval] WARN: Discord post failed ({e}); approval recorded to disk only")
        return False


def new_id() -> str:
    """Short, sortable, collision-resistant id (base36 of ms time + 2 rand chars)."""
    ms = int(time.time() * 1000)
    # base36 without random/Date dependencies that some sandboxes block
    chars = "0123456789abcdefghijklmnopqrstuvwxyz"
    n, s = ms, ""
    while n:
        n, r = divmod(n, 36)
        s = chars[r] + s
    return s[-8:]


def classify(kind: str = "", command: str = "", tier: str = "owner") -> dict:
    """Decide allow|confirm|deny for an action.

    Match priority: explicit --kind first; else scan --command against each
    rule's regex patterns (first match wins). Unknown → default_decision.
    """
    policy = load_policy()
    tier = tier if tier in policy.get("tiers", ["owner"]) else "owner"
    rules = policy.get("rules", [])

    matched = None
    if kind:
        matched = next((r for r in rules if r.get("kind") == kind), None)
    if matched is None and command:
        for r in rules:
            for pat in r.get("patterns", []):
                if re.search(pat, command, flags=re.IGNORECASE):
                    matched = r
                    break
            if matched:
                break

    if matched is None:
        return {"decision": policy.get("default_decision", "allow"),
                "kind": kind or "unknown", "rule": None,
                "reason": "no rule matched; default applied"}

    decision = matched.get("decision", {}).get(tier, "confirm")
    return {"decision": decision, "kind": matched["kind"], "rule": matched["kind"],
            "summary": matched.get("summary", ""),
            "reason": f"matched rule '{matched['kind']}' for tier '{tier}'"}
