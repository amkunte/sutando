#!/usr/bin/env python3
"""Tests for health-check's --notify-discord (#health) transition logic.
pytest-free; injects a fake poster + channel-getter, no real Discord.
Run: python3 tests/health-check-notify-discord.test.py
"""
from __future__ import annotations

import importlib.util
import tempfile
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "healthcheck", str(Path(__file__).resolve().parent.parent / "src" / "health-check.py"))
hc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hc)

_fails = []


def check(name, cond):
    print(("PASS" if cond else "FAIL") + f"  {name}")
    if not cond:
        _fails.append(name)


def main() -> int:
    posts = []
    poster = lambda cid, msg: (posts.append((cid, msg)) or True)
    getch = lambda name: "12345"
    sf = Path(tempfile.mkdtemp()) / "st.json"

    ok = [{"name": "a", "status": "ok"}, {"name": "b", "status": "ok"}]
    bad = [{"name": "a", "status": "ok"}, {"name": "b", "status": "down", "detail": "not running"}]
    warn_only = [{"name": "discord-voice", "status": "warn", "detail": "on-demand, not running"}]

    hc.notify_discord_health(ok, sf, poster, getch)
    check("all-ok, no prior → silent", len(posts) == 0)

    hc.notify_discord_health(warn_only, sf, poster, getch)
    check("on-demand 'warn' does NOT alarm (no cry-wolf)", len(posts) == 0)

    hc.notify_discord_health(bad, sf, poster, getch)
    check("real outage posts", len(posts) == 1 and "🔴" in posts[-1][1] and "new: b" in posts[-1][1])

    hc.notify_discord_health(bad, sf, poster, getch)
    check("steady outage stays silent (no per-pass spam)", len(posts) == 1)

    hc.notify_discord_health(ok, sf, poster, getch)
    check("recovery posts", len(posts) == 2 and "recovered" in posts[-1][1].lower())

    hc.notify_discord_health(ok, sf, poster, getch)
    check("steady-ok stays silent", len(posts) == 2)

    # no channel configured → never posts, never raises
    posts2 = []
    hc.notify_discord_health(bad, Path(tempfile.mkdtemp()) / "s2.json",
                             lambda c, m: posts2.append(1) or True, lambda n: "")
    check("missing #health channel → no post, no crash", len(posts2) == 0)

    print()
    if _fails:
        print(f"{len(_fails)} FAILED: {_fails}"); return 1
    print("all tests passed"); return 0


if __name__ == "__main__":
    raise SystemExit(main())
