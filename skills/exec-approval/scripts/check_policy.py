#!/usr/bin/env python3
"""Classify a high-risk action against policy.json → allow | confirm | deny.

Usage:
    check_policy.py --kind email_send [--tier owner]
    check_policy.py --command "gh pr create --repo sonichi/sutando ..."
    check_policy.py --kind file_delete --command "rm -rf /tmp/x"   # kind wins

Prints a JSON verdict to stdout. Exit code: 0=allow, 10=confirm, 20=deny —
so a bash caller can branch without parsing JSON:
    python3 check_policy.py --command "$CMD"; case $? in 0) ;; 10) ... ;; 20) ... ;; esac
"""
from __future__ import annotations

import argparse
import json
import sys

from common import classify

_EXIT = {"allow": 0, "confirm": 10, "deny": 20}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", default="")
    ap.add_argument("--command", default="")
    ap.add_argument("--tier", default="owner")
    args = ap.parse_args()
    if not args.kind and not args.command:
        print(json.dumps({"error": "provide --kind and/or --command"}))
        return 2
    verdict = classify(kind=args.kind, command=args.command, tier=args.tier)
    print(json.dumps(verdict, ensure_ascii=False))
    return _EXIT.get(verdict["decision"], 10)


if __name__ == "__main__":
    sys.exit(main())
