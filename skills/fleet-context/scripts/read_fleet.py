#!/usr/bin/env python3
"""Fleet-context READER (ingest/awareness half).

Merges ALL nodes' per-node logs (`<memory>/fleet/context-*.md`) into one
time-ordered view of recent fleet activity. Run at session start / on demand
so this node knows what its peers (Maverick, Goose, …) have been doing without
waiting to be told. The durable copies arrive via the memory-sync repo
(cadence tightened to ~5 min); the live #bot2bot relay covers the gap between
syncs.

Usage:
    python3 skills/fleet-context/scripts/read_fleet.py [--limit N] [--since-hours H]
Default: last 15 entries across all nodes.
"""
from __future__ import annotations

import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

LINE = re.compile(r"^\[(?P<node>\S+)\s+(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}Z)\]\s+(?P<rest>.*)$")


def memory_dir() -> Path:
    env = os.environ.get("SUTANDO_MEMORY_DIR")
    if env:
        return Path(os.path.expanduser(env))
    return Path.home() / ".claude" / "projects" / "-Users-abhi-sutando" / "memory"


def main() -> int:
    limit = 15
    since_hours = None
    a = sys.argv[1:]
    for i, tok in enumerate(a):
        if tok == "--limit" and i + 1 < len(a):
            limit = int(a[i + 1])
        elif tok == "--since-hours" and i + 1 < len(a):
            since_hours = float(a[i + 1])

    # Flat top-level per-node files `<memory>/fleet-context-<Node>.md` (see
    # fleet_relay.py for why not a subdir — it must ride the synced top-level glob).
    entries = []  # (ts, node, line)
    for f in sorted(memory_dir().glob("fleet-context-*.md")):
        try:
            for raw in f.read_text().splitlines():
                m = LINE.match(raw.strip())
                if not m:
                    continue
                ts = datetime.strptime(m["ts"], "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc)
                entries.append((ts, m["node"], raw.strip()))
        except OSError:
            continue

    if since_hours is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=since_hours)
        entries = [e for e in entries if e[0] >= cutoff]

    entries.sort(key=lambda e: e[0])
    shown = entries[-limit:]
    if not shown:
        print("(fleet context: no recent entries)")
        return 0
    nodes = sorted({n for _, n, _ in entries})
    print(f"=== Fleet context — {len(shown)} recent entries (nodes: {', '.join(nodes)}) ===")
    for _, _, line in shown:
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
