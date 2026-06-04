#!/usr/bin/env python3
"""Approve or deny a pending approval by id.

Run by the core agent when the owner's "approve <id>" / "deny <id>" reply
arrives (as a task). On approve, prints the held action spec as JSON so the
caller knows exactly what was greenlit and can execute it. Idempotent: a
second resolve of an already-decided id is a no-op that reports the prior
decision (so a duplicate owner reply can't double-execute).

Usage:
    resolve_approval.py <id> approve [--note "..."] [--by owner]
    resolve_approval.py <id> deny    [--note "changed my mind"]
Exit: 0 on success, 3 if id unknown, 4 on bad decision arg.
"""
from __future__ import annotations

import argparse
import json
import time

from common import approvals_dir, post_to_approvals


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("id")
    ap.add_argument("decision", choices=["approve", "deny"])
    ap.add_argument("--note", default="")
    ap.add_argument("--by", default="owner")
    args = ap.parse_args()

    path = approvals_dir() / f"{args.id}.json"
    if not path.exists():
        print(json.dumps({"error": f"unknown approval id '{args.id}'"}))
        return 3
    rec = json.loads(path.read_text())

    if rec["status"] != "pending":
        # Idempotent: report the existing decision, do not flip it.
        print(json.dumps({"already_decided": True, **rec}, ensure_ascii=False))
        return 0

    rec["status"] = "approved" if args.decision == "approve" else "denied"
    rec["decided_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    rec["decided_by"] = args.by
    rec["note"] = args.note or None
    path.write_text(json.dumps(rec, indent=2, ensure_ascii=False))

    emoji = "✅" if rec["status"] == "approved" else "🚫"
    post_to_approvals(f"{emoji} `{args.id}` **{rec['status']}** by {args.by}"
                      + (f" — {args.note}" if args.note else ""))

    print(json.dumps(rec, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
