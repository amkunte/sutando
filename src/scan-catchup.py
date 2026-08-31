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
(env or .env file). When set, this node never scans -- but it does still check
whether the owner node has stopped, emitting `SCANSTALE <name> :: <why>` when the
state it pulled via fleet-sync has gone far past cadence. Mirrors the
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

sys.path.insert(0, str(REPO_DIR / "src"))
from util_paths import claude_home_path  # noqa: E402  (needs REPO_DIR on sys.path)

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
        "state": REPO_DIR / "skills/trip-radar/state/trips.json",
        "cadence_hours": 24,
        "hint": "Run the trip-radar scan per skills/trip-radar/scan-prompt.md; "
                "post #travel only on new trip / material change / imminent check-in, else silent.",
    },
    {
        "name": "karts-air",
        # roaming_observable=False: state file is gitignored in the fleet repo, so a roaming node
        # only ever sees its own never-refreshed copy.
        "roaming_observable": False,
        "state": claude_home_path("skills/karts-air/state/karts-air-data.json"),
        "cadence_hours": 24,
        "hint": "Run the Cirrus SR22T deal-hunter per the karts-air skill's scan-prompt.md; "
                "post #deals only on new/changed airframes, else silent.",
    },
    {
        "name": "frontier-scan",
        # roaming_observable=False: no fleet-sync entry at all, so a roaming node
        # only ever sees its own never-refreshed copy.
        "roaming_observable": False,
        "state": REPO_DIR / "skills/frontier-scan/state/seen.json",
        "cadence_hours": 168,  # weekly
        "hint": "Run the Frontier Scan per skills/frontier-scan/scan-prompt.md; "
                "post #skills-dev only on a new capability delta, else silent.",
    },
]

GRACE = 1.5  # flag only after 1.5x cadence elapsed (absorbs one missed tick)

# Roaming node: only speak when the owner node looks DOWN, not merely late.
# Deliberately much wider than GRACE -- GRACE absorbs one missed tick, this has
# to absorb the owner node being legitimately off for a while.
ROAMING_STALE_MULT = 4


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


def _blocked_sources(data: dict) -> list[str]:
    """Names of blocked sources, but ONLY when every source is blocked.

    `sources_status` has two shapes in the wild (verified on disk 2026-08-13):
      * a bare string — amazon-orders / parcel-radar / trip-radar use "ok"
      * a per-source dict — karts-air uses
        {"barnstormers": "blocked", "aircraftforsale": "blocked", ...}
      * absent entirely — frontier-scan

    A `sources_status == "blocked"` equality check (the "one-line fix" this
    started as) would silently miss the dict form, i.e. exactly the scan that
    motivated the fix. Returns [] for partial blockage — a scan still producing
    from some sources is degraded, not dead, and firing on it would cry wolf.
    """
    ss = data.get("sources_status")
    if isinstance(ss, str):
        return ["all"] if ss.strip().lower() == "blocked" else []
    if isinstance(ss, dict) and ss:
        vals = [str(v).strip().lower() for v in ss.values()]
        if all(v == "blocked" for v in vals):
            return sorted(ss.keys())
    return []


def _report_owner_node_stall(now: datetime) -> None:
    """Roaming node: do not scan, but do not go blind either.

    A roaming node pulls the owner node's scan state via fleet-sync, so it is
    already holding the evidence that the owner node stopped -- it was simply
    never allowed to look at it. `main()` returned before reading a single
    `last_scan`, which is why #orders/#parcels/#travel went dark for three days
    after the home node's session ended on 2026-08-17: the only node still
    running was the one node gated out of noticing.

    Emits SCANSTALE, never SCANDUE. This node must not run the scan -- that is
    what SKIP_SKILL_SCANS exists to prevent, and a second runner double-posts to
    the channel.

    It reports an OBSERVATION, not a diagnosis. `last_scan` freshness here is a
    function of two independent systems -- the owner node scanning, and fleet-sync
    delivering -- and this script can see neither. An earlier draft asserted "the
    owner node has probably stopped"; kill the fleet-sync clone and that sentence
    sends the owner to fix a machine that is fine. Naming both candidates costs a
    clause and cannot be wrong.

    Repetition is the CONSUMER's problem, deliberately. This script stays
    read-only (see the scan-catchup entry in tests/state-paths-adoption.test.py's
    ALLOWLIST: it composes skill-local state and owns no workspace runtime-state,
    so a dedup sentinel does not belong here). The proactive loop already has
    surface-once-per-changed-set machinery in step 6.5; step 2.6 routes these
    lines through it.

    A state file this node does not carry is skipped, not flagged. fleet-sync
    only syncs manifest-listed items, so absence here is a gap in what was
    pulled, not evidence about the owner node.
    """
    for s in SCANS:
        try:
            data = json.loads(s["state"].read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("suspended"):
            continue
        if not s.get("roaming_observable", True):
            # This scan's state never reaches a roaming node, so the file being
            # aged here is this node's OWN copy and will never refresh. Ageing it
            # produces an alert that cannot clear no matter what the owner node
            # does -- which trains the reader to ignore the whole SCANSTALE class.
            continue
        last = _parse(data.get("last_scan"))
        if last is None:
            continue
        hours = (now - last).total_seconds() / 3600.0
        if hours > s["cadence_hours"] * ROAMING_STALE_MULT:
            print(f"SCANSTALE {s['name']} :: last_scan has not advanced in "
                  f"{hours:.1f}h (cadence {s['cadence_hours']}h, flagged past "
                  f"{ROAMING_STALE_MULT}x) and this node is gated out of scanning. "
                  f"Either the owner node stopped or fleet-sync stopped delivering "
                  f"-- check both. Do NOT scan here (double-post).")


def main() -> None:
    now = datetime.now(timezone.utc)
    if _node_skips_scans():
        _report_owner_node_stall(now)
        return
    for s in SCANS:
        try:
            data = json.loads(s["state"].read_text())
            last = _parse(data.get("last_scan"))
        except (OSError, json.JSONDecodeError):
            # No readable state yet -> treat as due (first run).
            print(f"SCANDUE {s['name']} :: {s['hint']}")
            continue
        if data.get("suspended"):
            # Operator/owner suspended this scan for now (e.g. all sources
            # blocked). Skip silently so the self-healing backstop doesn't
            # re-fire a scan the cron layer was deliberately turned off.
            continue
        # Running-but-producing-nothing. `last_scan` age alone cannot see this:
        # a scan that runs, finds every source blocked, and stamps a fresh
        # last_scan looks perfectly healthy to the age check — which is the
        # actual state #orders/#parcels/#travel were in for days (2026-07-24+).
        # Deliberately placed AFTER the `suspended` check above so it does not
        # undo #142: karts-air is both suspended AND all-blocked, and a
        # suspended scan must stay silent.
        #
        # Emitted as SCANBLOCKED, not SCANDUE, on purpose — re-running a scan
        # whose sources are blocked just reproduces the nothing. The loop should
        # surface this to the owner, not retry it.
        blocked = _blocked_sources(data)
        if blocked:
            print(f"SCANBLOCKED {s['name']} :: all sources blocked "
                  f"({', '.join(blocked)}) — last_scan is fresh, so the age check "
                  f"cannot see this. Re-running will not help; surface it.")
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
