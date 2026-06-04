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
import secrets
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

# Valid approval-id shape. The id flows from the owner's free-text reply
# ("approve <id>") and is used to build a filesystem path — restrict it to a
# fixed alphabet so it can never traverse out of the approvals dir (B7).
_ID_RE = re.compile(r"^[0-9a-z]{1,16}$")


def valid_id(s: str) -> bool:
    return bool(_ID_RE.match(s or ""))


def _warn(msg: str) -> None:
    """Warnings go to STDERR — stdout is reserved for the id / JSON the caller
    captures via $(...). Mixing them poisoned `ID=$(request_approval.py ...)` (N1)."""
    print(msg, file=sys.stderr)

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
        _warn("[exec-approval] WARN: no 'approvals' channel in discord-config.json; approval not posted to Discord")
        return False
    try:
        tok = _discord_token()
    except Exception as e:
        _warn(f"[exec-approval] WARN: {e}; approval not posted to Discord")
        return False
    # allowed_mentions parse:[] neutralizes @everyone/@here/role pings that
    # could be injected via untrusted summary/command text (N2).
    body = json.dumps({"content": text[:1990], "allowed_mentions": {"parse": []}}).encode()
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
        _warn(f"[exec-approval] WARN: Discord post failed ({e.code}); approval recorded to disk only")
        return False
    except Exception as e:
        _warn(f"[exec-approval] WARN: Discord post failed ({e}); approval recorded to disk only")
        return False


def new_id() -> str:
    """Short, sortable, collision-resistant id: base36(ms time) + 4 random
    base36 chars. The random suffix prevents same-millisecond collisions that
    would let one request silently overwrite another's pending file (B6).
    request_approval.py additionally creates the file with O_EXCL as a belt-
    and-suspenders guard."""
    chars = "0123456789abcdefghijklmnopqrstuvwxyz"
    n, s = int(time.time() * 1000), ""
    while n:
        n, r = divmod(n, 36)
        s = chars[r] + s
    rand = "".join(secrets.choice(chars) for _ in range(4))
    return (s[-8:] + rand)[-12:]


def classify(kind: str = "", command: str = "", tier: str = "owner") -> dict:
    """Decide allow|confirm|deny for an action.

    Match priority: explicit --kind first; else scan --command against each
    rule's regex patterns (first match wins). Unknown → default_decision.
    """
    policy = load_policy()
    # Unknown/garbage tier coerces to the LEAST-privileged tier, never owner
    # (B3) — a typo or injected tier must not escalate. 'other' is denied for
    # every high-risk class, so an unrecognized caller gets the safe answer.
    known = policy.get("tiers", ["owner", "team", "other"])
    least = known[-1] if known else "other"
    tier = tier if tier in known else least
    rules = policy.get("rules", [])

    # Collect EVERY matching rule — both the explicit --kind AND any rule whose
    # patterns match --command — then take the MOST RESTRICTIVE verdict (G4).
    # A short-circuit on --kind would let `--kind <some-allow-rule> --command
    # "rm -rf /"` skip the command scan once an allow-rule exists in policy.
    matched = []
    if kind:
        r = next((r for r in rules if r.get("kind") == kind), None)
        if r:
            matched.append(r)
    if command:
        for r in rules:
            if any(re.search(p, command, flags=re.IGNORECASE) for p in r.get("patterns", [])):
                matched.append(r)

    default = policy.get("default_decision", "confirm")
    if not matched:
        return {"decision": default, "kind": kind or "unknown", "rule": None,
                "reason": "no rule matched; fail-closed default applied"}

    rank = {"allow": 0, "confirm": 1, "deny": 2}
    best = None
    for r in matched:
        d = r.get("decision", {}).get(tier, "confirm")
        if best is None or rank.get(d, 1) > rank.get(best[0], 1):
            best = (d, r)
    decision, rule = best
    return {"decision": decision, "kind": rule["kind"], "rule": rule["kind"],
            "summary": rule.get("summary", ""),
            "reason": f"most-restrictive of {len(matched)} matched rule(s) for tier '{tier}'"}
