#!/usr/bin/env python3
"""Self-healing catch-up for daily scheduled deliveries (Option A, 2026-06-01).

The morning briefing (06:00) and Siemens-prep drip (07:33) are session-only
CronCreate jobs. If the host sleeps overnight or the session is busy at the
fire minute, the slot is silently SKIPPED (not deferred) — so the owner just
doesn't get that day's delivery. Observed repeatedly ~2026-05-31..06-01.

This script runs every proactive-loop pass (the loop fires reliably every
~10 min) and reports any daily delivery that is OVERDUE today (now past its
scheduled time) AND not yet delivered (no per-day sentinel) AND still inside a
useful catch-up window. The loop then runs the flagged delivery, which writes
its own sentinel — so a delivery that fired on time (and wrote its sentinel)
is never re-run, and a missed one is recovered on the next pass.

Output (stdout): one `CATCHUP <name>` line per delivery to run now, else
nothing. Exit 0 always (never break the loop).

Sentinels (written by the delivery paths, NOT here):
  <workspace>/state/<key>-delivered-<YYYY-MM-DD>.sentinel
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from workspace_default import resolve_workspace  # noqa: E402

WORKSPACE = resolve_workspace()
STATE_DIR = WORKSPACE / "state"

# Daily deliveries to guard. `until` (YYYY-MM-DD, inclusive) marks a job that
# expires — after it, the job is skipped (and the loop should drop its cron).
# `window_hours` caps how late a catch-up is still useful (a 6 AM briefing
# recovered at noon is fine; at 11 PM it's stale — skip it).
JOBS = [
    {
        "name": "morning-briefing",
        "key": "briefing",
        "hour": 6, "minute": 0,
        "window_hours": 8,        # recover until ~14:00
        "until": None,
        "run_hint": "/morning-briefing",
    },
    {
        "name": "siemens-prep",
        "key": "siemens-drip",
        "hour": 7, "minute": 33,
        "window_hours": 14,       # prep content useful all day → recover until ~21:30
        "until": "2026-06-09",
        "run_hint": "Siemens prep daily drip (read notes/siemens-prep/dossier.md; see crons.json siemens-interview-prep prompt)",
    },
]


def main() -> None:
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    to_run = []

    for job in JOBS:
        if job["until"] and today > job["until"]:
            continue  # expired job — skip
        sentinel = STATE_DIR / f"{job['key']}-delivered-{today}.sentinel"
        if sentinel.exists():
            continue  # already delivered today
        scheduled = now.replace(hour=job["hour"], minute=job["minute"],
                                second=0, microsecond=0)
        if now < scheduled:
            continue  # not due yet today
        hours_late = (now - scheduled).total_seconds() / 3600.0
        if hours_late > job["window_hours"]:
            # Too late to be useful — note it (stderr) and suppress so we don't
            # fire a stale delivery; do NOT write the sentinel (next session can
            # see it was missed in logs). Just skip the auto-run.
            print(f"# {job['name']}: missed today (overdue {hours_late:.1f}h "
                  f"> {job['window_hours']}h window) — not auto-running",
                  file=sys.stderr)
            continue
        to_run.append(job)

    for job in to_run:
        print(f"CATCHUP {job['name']} :: {job['run_hint']}")


if __name__ == "__main__":
    main()
