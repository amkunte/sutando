#!/usr/bin/env python3
"""Query the session-search FTS5 index.

Usage:
    search.py "twilio webhook signature"          # top matches, ranked
    search.py --role user "did I ask about X"      # only my messages
    search.py --role assistant "what did I say"
    search.py --kind doc "exec approval"           # only indexed docs
    search.py -n 20 "skill synth"                  # more results
    search.py --json "query"                       # machine-readable

Query syntax is SQLite FTS5: bare words AND-match by default; use OR, NOT,
"quoted phrases", and prefix* . Results are ranked by bm25 and show a
highlighted snippet with source + timestamp.
"""
from __future__ import annotations

import argparse
import json
import sys

from common import connect, db_path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("query", nargs="+")
    ap.add_argument("-n", "--limit", type=int, default=10)
    ap.add_argument("--role", choices=["user", "assistant", "doc"])
    ap.add_argument("--kind", choices=["transcript", "doc"])
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    q = " ".join(args.query)
    if not db_path().exists():
        print("session-search: no index yet — run index.py first", file=sys.stderr)
        return 2
    con = connect()
    cur = con.cursor()

    where = ["docs_fts MATCH ?"]
    params = [q]
    if args.role:
        where.append("d.role = ?"); params.append(args.role)
    if args.kind:
        where.append("d.kind = ?"); params.append(args.kind)
    params.append(args.limit)

    sql = (
        "SELECT d.source, d.kind, d.role, d.ts, d.line_no, "
        "snippet(docs_fts, 0, '«', '»', ' … ', 14) AS snip, bm25(docs_fts) AS score "
        "FROM docs_fts JOIN docs d ON d.id = docs_fts.rowid "
        f"WHERE {' AND '.join(where)} ORDER BY score LIMIT ?"
    )
    try:
        rows = cur.execute(sql, params).fetchall()
    except Exception as e:
        print(f"session-search: bad query ({e}). FTS5 syntax: words, OR, NOT, \"phrase\", prefix*",
              file=sys.stderr)
        return 2

    if args.json:
        out = [{"source": r[0], "kind": r[1], "role": r[2], "ts": r[3],
                "line": r[4], "snippet": r[5], "score": r[6]} for r in rows]
        print(json.dumps(out, ensure_ascii=False))
        return 0

    if not rows:
        print(f"no matches for: {q}")
        return 0
    from pathlib import Path
    for r in rows:
        src, kind, role, ts, line, snip, _ = r
        tag = f"{role}" if kind == "transcript" else f"doc:{Path(src).name}"
        when = (ts or "")[:16].replace("T", " ")
        print(f"• [{tag}] {when}  (L{line})")
        print(f"    {snip.strip()}")
    print(f"\n{len(rows)} match(es). Source files under: {Path(rows[0][0]).parent}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
