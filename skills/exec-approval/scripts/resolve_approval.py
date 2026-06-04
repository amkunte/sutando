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
import fcntl
import json
import time

from common import approvals_dir, post_to_approvals, valid_id


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("id")
    ap.add_argument("decision", choices=["approve", "deny"])
    ap.add_argument("--note", default="")
    ap.add_argument("--by", default="owner")
    # The tier of the task that carried this approve/deny reply. Set from the
    # resolving task's access_tier — the bridge is the auth root for tiers.
    # Defaults to the LEAST-privileged tier so an omitted flag can't approve.
    ap.add_argument("--approver-tier", default="other")
    args = ap.parse_args()

    # Reject any id that isn't the fixed alphabet — blocks path traversal
    # (e.g. "../secret") turning this into an arbitrary-.json-write (B7).
    if not valid_id(args.id):
        print(json.dumps({"error": f"invalid approval id '{args.id}'"}))
        return 4

    # Authority check (G1): only an owner-tier reply may APPROVE. A non-owner
    # (or unset) approver-tier cannot swing the gate open even if a prompt-
    # injected task tricks the agent into running this with `approve`. Deny is
    # always allowed (anyone may withdraw/reject).
    if args.decision == "approve" and args.approver_tier != "owner":
        print(json.dumps({"error": "approve requires owner approver-tier",
                          "approver_tier": args.approver_tier}))
        return 5

    path = approvals_dir() / f"{args.id}.json"
    if not path.exists():
        print(json.dumps({"error": f"unknown approval id '{args.id}'"}))
        return 3

    # Hold an exclusive lock across the whole read-modify-write so two
    # near-simultaneous resolves (Discord double-tap / retried task) can't
    # both see status=='pending' and both execute (B5 TOCTOU).
    with open(path, "r+", encoding="utf-8") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        rec = json.load(fh)

        if rec.get("status") != "pending":
            # Idempotent: report the existing decision, do not flip it.
            fcntl.flock(fh, fcntl.LOCK_UN)
            print(json.dumps({"already_decided": True, **rec}, ensure_ascii=False))
            return 0

        rec["status"] = "approved" if args.decision == "approve" else "denied"
        rec["decided_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        rec["decided_by"] = args.by
        rec["note"] = args.note or None
        fh.seek(0)
        fh.truncate()
        json.dump(rec, fh, indent=2, ensure_ascii=False)
        fcntl.flock(fh, fcntl.LOCK_UN)

    emoji = "✅" if rec["status"] == "approved" else "🚫"
    post_to_approvals(f"{emoji} `{args.id}` **{rec['status']}** by {args.by}"
                      + (f" — {args.note}" if args.note else ""))

    print(json.dumps(rec, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
