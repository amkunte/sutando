#!/usr/bin/env python3
"""Self-healing catch-up for skill scans (durable, 2026-06-20).

The skill scans — amazon-orders (#orders), parcel-radar (#parcels), trip-radar
(#travel), karts-air (#deals) — are driven by session-only CronCreate jobs that
(a) die when the registering session ends/compacts and (b) auto-expire after 7
days. During a long unattended period (e.g. the owner traveling 2+ weeks) the
scans silently stop and the channels go quiet — not because nothing changed, but
because nothing ran. The state file's `last_scan` simply stops advancing.
Observed 2026-06-18..20 on Goose: #orders silent for ~2 days.

This script runs every proactive-loop pass (the loop fires reliably, kept alive
by the com.sutando.core launchd watchdog — independent of any session-only cron).
It reads each scan's `last_scan` from its own state file and prints
`SCANDUE <name> :: <hint>` for any scan now overdue by more than cadence * GRACE.
The loop then runs the flagged scan per its scan-prompt, which updates `last_scan`
— so a scan that fired on time is never re-run, and a missed one is recovered
within one loop pass. Because the trigger is `last_scan` on disk (not a cron),
cron expiry and session restart can no longer silently stop scans.

Output (stdout): one `SCANDUE <name> :: <hint>` per overdue scan, else nothing.
Exit 0 always (never break the loop).

Roaming gate: a node that should NOT run skill scans (e.g. Maverick, which roams
with the owner while Goose stays home and owns the scans) sets SKIP_SKILL_SCANS=1
(env or .env file). When set, main() returns silently. Mirrors the
SKIP_SCHEDULED_DELIVERIES gate in scheduled-catchup.py.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent.parent  # src/ -> repo root
HOME = Path.home()

# cadence_hours: how often the scan should run. A scan is flagged overdue only
# after cadence_hours * GRACE, so a single missed cron tick doesn't churn.
SCANS = [
    {
        "name": "amazon-orders",
        "state": REPO_DIR / "skills/amazon-orders/state/orders.json",
        "cadence_hours": 6,
        "hint": "Run the Amazon orders scan per skills/amazon-orders/scan-prompt.md; "
                "post #orders only on add/deliver, else silent.",
    },
    {
        "name": "parcel-radar",
        "state": REPO_DIR / "skills/parcel-radar/state/parcels.json",
        "cadence_hours": 6,
        "hint": "Run the Parcel Radar scan per skills/parcel-radar/scan-prompt.md "
                "(EXCLUDE Amazon); post #parcels only on add/deliver/exception, else silent.",
    },
    {
        "name": "trip-radar",
        "state": HOME / ".claude/skills/trip-radar/state/trips.json",
        "cadence_hours": 24,
        "hint": "Run the trip-radar scan per ~/.claude/skills/trip-radar/scan-prompt.md; "
                "post #travel only on new trip / material change / imminent check-in, else silent.",
    },
    {
        "name": "karts-air",
        "state": HOME / ".claude/skills/karts-air/state/karts-air-data.json",
        "cadence_hours": 24,
        "hint": "Run the Cirrus SR22T deal-hunter per ~/.claude/skills/karts-air/scan-prompt.md; "
                "post #deals only on new/changed airframes, else silent.",
    },
]

GRACE = 1.5  # flag only after 1.5x cadence elapsed (absorbs one missed tick)


def _node_skips_scans() -> bool:
    """True if this node is gated OUT of skill scans (roaming node)."""
    if os.environ.get("SKIP_SKILL_SCANS") == "1":
        return True
    try:
        return "SKIP_SKILL_SCANS=1" in (REPO_DIR / ".env").read_text()
    except OSError:
        return False


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def main() -> None:
    if _node_skips_scans():
        return
    now = datetime.now(timezone.utc)
    for s in SCANS:
        try:
            data = json.loads(s["state"].read_text())
            last = _parse(data.get("last_scan"))
        except (OSError, json.JSONDecodeError):
            # No readable state yet -> treat as due (first run).
            print(f"SCANDUE {s['name']} :: {s['hint']}")
            continue
        if last is None:
            print(f"SCANDUE {s['name']} :: {s['hint']}")
            continue
        hours = (now - last).total_seconds() / 3600.0
        if hours > s["cadence_hours"] * GRACE:
            print(f"SCANDUE {s['name']} :: overdue {hours:.1f}h "
                  f"(cadence {s['cadence_hours']}h) :: {s['hint']}")


if __name__ == "__main__":
    main()
