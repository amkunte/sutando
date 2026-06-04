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


def _fence_safe(s: str) -> str:
    """Neutralize ``` so untrusted command/summary text can't break out of the
    code fence into live Discord markdown (N2)."""
    return s.replace("`", "ˋ")  # U+02CB modifier-letter grave: renders, can't fence


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", required=True)
    ap.add_argument("--summary", required=True)
    ap.add_argument("--command", default="")
    ap.add_argument("--tier", default="owner")
    ap.add_argument("--requested-by", default="core")
    args = ap.parse_args()

    rec_base = {
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
    # Create with O_EXCL so a same-ms id collision can never silently clobber
    # an existing pending approval (B6). Retry with a fresh id on the rare hit.
    adir = approvals_dir()
    aid = None
    for _ in range(5):
        cand = new_id()
        path = adir / f"{cand}.json"
        try:
            with open(path, "x", encoding="utf-8") as fh:
                json.dump({"id": cand, **rec_base}, fh, indent=2, ensure_ascii=False)
            aid = cand
            break
        except FileExistsError:
            continue
    if aid is None:
        raise SystemExit("request_approval: could not allocate a unique id after 5 tries")

    # Cap summary + command at build time so the actionable footer (the reply
    # instruction + id) always survives the 1990-char Discord cap and is never
    # truncated away (G3).
    card = (
        f"🔐 **Approval needed** — `{aid}`\n"
        f"**Action:** {_fence_safe(args.kind[:80])}\n"
        f"{_fence_safe(args.summary[:600])}\n"
    )
    if args.command:
        card += f"```\n{_fence_safe(args.command[:300])}\n```\n"
    card += f"Reply **@Maverick approve {aid}** or **@Maverick deny {aid}**."
    post_to_approvals(card)

    print(aid)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
