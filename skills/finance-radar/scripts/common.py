#!/usr/bin/env python3
"""Shared helpers for finance-radar. Self-contained, stdlib-only, py3.9-safe.

The Drive-MCP read of the sheet is done by the core agent (per scan-prompt.md)
— these scripts only handle the deterministic diff / alert / render / post /
state, so the financial figures never depend on flaky parsing.
"""
from __future__ import annotations

import json
import os
import urllib.request
import urllib.error
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
CONFIG = SKILL_DIR / "config.json"
DISCORD_ENV = Path.home() / ".claude" / "channels" / "discord" / ".env"
_UA = "DiscordBot (https://github.com/amkunte/sutando, 1.0)"


def resolve_workspace() -> Path:
    env = os.environ.get("SUTANDO_WORKSPACE")
    if env:
        return Path(os.path.expanduser(env))
    return Path.home() / ".sutando" / "workspace"


def load_config() -> dict:
    return json.loads(CONFIG.read_text())


def state_dir() -> Path:
    # Financial state is sensitive → keep it under the skill's own gitignored
    # state/, NOT the shared workspace.
    d = SKILL_DIR / "state"
    d.mkdir(parents=True, exist_ok=True)
    return d


def prior_path() -> Path:
    return state_dir() / "finance-state.json"


def latest_path() -> Path:
    return state_dir() / "latest-snapshot.json"


def load_json(p: Path):
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def finance_channel_id() -> str:
    p = resolve_workspace() / "state" / "discord-config.json"
    try:
        cid = json.loads(p.read_text()).get("channels", {}).get("finance", "")
    except Exception:
        return ""
    # Only a scalar string/int id is valid — a list/dict would str() into a
    # bogus-but-truthy value that posts to a 404 URL. Reject non-scalars.
    if isinstance(cid, (str, int)) and str(cid).strip().isdigit():
        return str(cid).strip()
    return ""


def to_num(v):
    """Coerce a sheet-derived value to float. Numbers pass through; strings
    like '$9,183,708', '26.66%', '(1,234)' are cleaned; anything that can't be
    parsed → None. Lets the agent's extraction be comma/$-tolerant without the
    digest silently dropping a field (a bare isinstance check would treat
    '9,183,708' as absent and suppress every delta/alert)."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if not isinstance(v, str):
        return None
    s = v.strip().replace(",", "").replace("$", "").replace("%", "").strip()
    neg = s.startswith("(") and s.endswith(")")
    if neg:
        s = s[1:-1]
    try:
        n = float(s)
    except ValueError:
        return None
    return -n if neg else n


def _discord_token() -> str:
    for line in DISCORD_ENV.read_text().splitlines():
        if line.startswith("DISCORD_BOT_TOKEN="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError("DISCORD_BOT_TOKEN not found")


def post_to_finance(text: str) -> bool:
    """Post to #finance ONLY. Returns True on success. No web page, no
    results/ fallback (this skill's output is private — #finance or nothing)."""
    cid = finance_channel_id()
    if not cid:
        print("[finance-radar] ERROR: no 'finance' channel in discord-config.json — not posting")
        return False
    try:
        tok = _discord_token()
    except Exception as e:
        print(f"[finance-radar] ERROR: {e}")
        return False
    for chunk in [text[i:i + 1900] for i in range(0, len(text), 1900)] or [""]:
        body = json.dumps({"content": chunk, "allowed_mentions": {"parse": []}}).encode()
        req = urllib.request.Request(
            f"https://discord.com/api/v10/channels/{cid}/messages",
            data=body, method="POST",
            headers={"Authorization": f"Bot {tok}", "User-Agent": _UA,
                     "Content-Type": "application/json"},
        )
        try:
            urllib.request.urlopen(req, timeout=20)
        except (urllib.error.HTTPError, urllib.error.URLError, OSError) as e:
            print(f"[finance-radar] ERROR posting to #finance: {e}")
            return False
    return True


def money(n) -> str:
    """Format a number as $X,XXX (with sign). None → 'n/a'."""
    if n is None:
        return "n/a"
    try:
        n = float(n)
    except (TypeError, ValueError):
        return str(n)
    sign = "-" if n < 0 else ""
    return f"{sign}${abs(n):,.0f}"


def signed(n) -> str:
    if n is None:
        return "n/a"
    s = money(n)
    return s if n < 0 else f"+{s}"
