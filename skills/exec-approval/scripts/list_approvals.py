#!/usr/bin/env python3
"""List approvals (default: pending only). Used by the proactive loop to
re-surface stale pending approvals the owner hasn't acted on.

Usage:
    list_approvals.py                 # pending only, text
    list_approvals.py --all --json
"""
from __future__ import annotations

import argparse
import json

from common import approvals_dir


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="include decided")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    recs = []
    for p in sorted(approvals_dir().glob("*.json")):
        try:
            r = json.loads(p.read_text())
        except Exception:
            continue
        if args.all or r.get("status") == "pending":
            recs.append(r)

    if args.json:
        print(json.dumps(recs, ensure_ascii=False))
    else:
        if not recs:
            print("(no pending approvals)")
        for r in recs:
            print(f"{r['id']}  [{r['status']}]  {r['kind']}: {r['summary']}  ({r['created_at']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
