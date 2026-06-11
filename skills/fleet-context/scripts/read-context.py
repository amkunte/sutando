#!/usr/bin/env python3
"""fleet-context read — show the full fleet context (all nodes' logs).

Reads every `<memory>/fleet/context-*.md` (this node's own + peers' synced copies)
and prints them newest-last. Run at session catchup, or on demand when you need to
know what the other node has been doing for the owner.

Usage:
  read-context.py            # all nodes, last 25 lines each
  read-context.py --tail 50  # last 50 lines each
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

MEMORY_DIR = Path(
    os.environ.get("SUTANDO_MEMORY_DIR",
                   str(Path.home() / ".claude" / "projects" / "-Users-abhi-sutando" / "memory"))
).expanduser()
FLEET_DIR = MEMORY_DIR / "fleet"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tail", type=int, default=25)
    a = ap.parse_args()
    if not FLEET_DIR.exists():
        print("(no fleet context yet)")
        return 0
    logs = sorted(FLEET_DIR.glob("context-*.md"))
    if not logs:
        print("(no fleet context yet)")
        return 0
    for log in logs:
        node = log.stem.replace("context-", "")
        lines = [ln for ln in log.read_text().splitlines() if ln.strip()]
        print(f"\n=== {node} ({len(lines)} entries) ===")
        for ln in lines[-a.tail:]:
            print(ln)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
