#!/usr/bin/env python3
"""Create a pending approval, post it to #approvals, and return the id.

NON-BLOCKING by design: it records the request and notifies the owner, then
returns immediately. The agent must NOT execute the held action until the
owner approves (see resolve_approval.py). This matches Sutando's async task
model — the owner's "approve <id>" reply comes back as a normal task.

Usage:
    request_approval.py --kind email_send --summary "Email Artico re: scheduling" \\
        --command "python3 src/send_email.py ..."        # command is optional context
Prints the approval id to stdout.
"""
from __future__ import annotations

import argparse
import json
import time

from common import approvals_dir, new_id, post_to_approvals


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", required=True)
    ap.add_argument("--summary", required=True)
    ap.add_argument("--command", default="")
    ap.add_argument("--tier", default="owner")
    ap.add_argument("--requested-by", default="core")
    args = ap.parse_args()

    aid = new_id()
    rec = {
        "id": aid,
        "status": "pending",
        "kind": args.kind,
        "summary": args.summary,
        "command": args.command,
        "tier": args.tier,
        "requested_by": args.requested_by,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "decided_at": None,
        "decided_by": None,
        "note": None,
    }
    path = approvals_dir() / f"{aid}.json"
    path.write_text(json.dumps(rec, indent=2, ensure_ascii=False))

    card = (
        f"🔐 **Approval needed** — `{aid}`\n"
        f"**Action:** {args.kind}\n"
        f"{args.summary}\n"
    )
    if args.command:
        card += f"```\n{args.command[:300]}\n```\n"
    card += f"Reply **@Maverick approve {aid}** or **@Maverick deny {aid}**."
    post_to_approvals(card)

    print(aid)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
