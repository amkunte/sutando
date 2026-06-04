#!/usr/bin/env python3
"""(Re)build the session-search FTS5 index — incrementally.

Transcripts are append-only JSONL, so we remember the byte offset already
indexed per file and only parse the tail on each run. Docs are re-indexed
whole when their mtime changes. A transcript that shrank (compaction/rewrite)
is fully re-indexed.

Usage:
    index.py                # incremental update
    index.py --rebuild      # drop everything and reindex from scratch
    index.py --stats        # print row/file counts, don't index
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from common import connect, transcript_dir, doc_sources, index_lock

# Block types in an assistant message whose text we index. 'thinking' is
# excluded (internal, huge) and tool_use/tool_result are excluded (noisy).
_TEXT_BLOCKS = {"text"}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _extract(line: str):
    """Parse one transcript JSONL line → (role, ts, text) or None to skip."""
    try:
        d = json.loads(line)
    except Exception:
        return None
    t = d.get("type")
    if t not in ("user", "assistant"):
        return None
    msg = d.get("message")
    if not isinstance(msg, dict):
        return None
    content = msg.get("content")
    ts = d.get("timestamp") or ""
    parts = []
    if isinstance(content, str):
        parts.append(content)
    elif isinstance(content, list):
        for b in content:
            if isinstance(b, dict) and b.get("type") in _TEXT_BLOCKS:
                txt = b.get("text")
                # Type-guard: a non-str text value (int/list/None from an
                # unexpected content shape) would otherwise crash str.join,
                # abort the whole file's indexing, leave its offset unadvanced,
                # and re-crash every future run — permanently hiding that
                # transcript from search (B2).
                if isinstance(txt, str) and txt:
                    parts.append(txt)
    text = "\n".join(parts).strip()
    if not text:
        return None
    return (t, ts, text)


def _index_transcript(con, path: Path) -> int:
    cur = con.cursor()
    row = cur.execute("SELECT offset, nrows FROM files WHERE path=?", (str(path),)).fetchone()
    size = path.stat().st_size
    start_offset, base_lineno = (row[0], row[1]) if row else (0, 0)

    # Rewrite/compaction detection (B1). We ALWAYS advance the offset to just
    # past a '\n', so the invariant is: byte[offset-1] == '\n'. A pure append
    # preserves the bytes before offset, so the invariant still holds. A
    # rewrite (shrink OR same/larger size with new content) changes those
    # bytes, so byte[offset-1] is no longer that newline → reset and reindex.
    # The size-only `> size` check missed same-or-larger rewrites entirely.
    rewritten = start_offset > size
    if not rewritten and start_offset > 0:
        with path.open("rb") as _fh:
            _fh.seek(start_offset - 1)
            rewritten = _fh.read(1) != b"\n"
    if rewritten:
        cur.execute("DELETE FROM docs WHERE source=?", (str(path),))
        start_offset, base_lineno = 0, 0

    added = 0
    consumed = start_offset
    lineno = base_lineno
    with path.open("rb") as fh:
        fh.seek(start_offset)
        data = fh.read()
    if not data:
        return 0
    # Only process complete lines (ending in \n); leave a partial trailing
    # line for the next run so a mid-write append isn't half-indexed.
    last_nl = data.rfind(b"\n")
    if last_nl == -1:
        return 0
    block = data[: last_nl + 1]
    consumed = start_offset + len(block)
    for raw in block.split(b"\n"):
        if not raw:
            continue
        lineno += 1
        rec = _extract(raw.decode("utf-8", "replace"))
        if rec is None:
            continue
        role, ts, text = rec
        cur.execute(
            "INSERT INTO docs(source,kind,session_id,role,ts,line_no,text) VALUES(?,?,?,?,?,?,?)",
            (str(path), "transcript", path.stem, role, ts, lineno, text),
        )
        added += 1
    cur.execute(
        "INSERT INTO files(path,offset,mtime,nrows,indexed_at) VALUES(?,?,?,?,?) "
        "ON CONFLICT(path) DO UPDATE SET offset=excluded.offset, mtime=excluded.mtime, "
        "nrows=excluded.nrows, indexed_at=excluded.indexed_at",
        (str(path), consumed, path.stat().st_mtime, lineno, _now()),
    )
    con.commit()
    return added


def _chunk_doc(text: str):
    """Split a markdown doc into paragraph-ish chunks (blank-line separated),
    skipping empties. Keeps chunks searchable + snippet-sized."""
    chunks, buf = [], []
    for ln in text.splitlines():
        if ln.strip() == "":
            if buf:
                chunks.append("\n".join(buf)); buf = []
        else:
            buf.append(ln)
    if buf:
        chunks.append("\n".join(buf))
    return [c for c in chunks if c.strip()]


def _index_doc(con, path: Path) -> int:
    cur = con.cursor()
    mtime = path.stat().st_mtime
    row = cur.execute("SELECT mtime FROM files WHERE path=?", (str(path),)).fetchone()
    if row and row[0] == mtime:
        return 0  # unchanged
    # changed (or new) → replace all rows for this source
    cur.execute("DELETE FROM docs WHERE source=?", (str(path),))
    added = 0
    for i, chunk in enumerate(_chunk_doc(path.read_text(encoding="utf-8", errors="replace")), 1):
        cur.execute(
            "INSERT INTO docs(source,kind,session_id,role,ts,line_no,text) VALUES(?,?,?,?,?,?,?)",
            (str(path), "doc", path.stem, "doc", _now(), i, chunk),
        )
        added += 1
    cur.execute(
        "INSERT INTO files(path,offset,mtime,nrows,indexed_at) VALUES(?,?,?,?,?) "
        "ON CONFLICT(path) DO UPDATE SET offset=0, mtime=excluded.mtime, "
        "nrows=excluded.nrows, indexed_at=excluded.indexed_at",
        (str(path), 0, mtime, added, _now()),
    )
    con.commit()
    return added


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuild", action="store_true")
    ap.add_argument("--stats", action="store_true")
    args = ap.parse_args()

    con = connect()
    cur = con.cursor()

    if args.stats:
        nrows = cur.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
        nfiles = cur.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        byrole = dict(cur.execute("SELECT role, COUNT(*) FROM docs GROUP BY role").fetchall())
        print(f"indexed rows: {nrows}  files: {nfiles}  by-role: {byrole}")
        return 0

    # Serialize indexers: a cron run + a manual run overlapping would both read
    # the same offset and double-insert (no row-level dedup). Hold an exclusive
    # file lock for the whole run; if another indexer has it, exit cleanly.
    lock = index_lock()
    if lock is None:
        print("session-search: another index run is in progress; skipping")
        return 0

    if args.rebuild:
        cur.executescript("DELETE FROM docs; DELETE FROM files; "
                          "INSERT INTO docs_fts(docs_fts) VALUES('delete-all');")
        con.commit()
        print("index reset")

    total = 0
    tdir = transcript_dir()
    tfiles = sorted(tdir.glob("*.jsonl")) if tdir.is_dir() else []
    for f in tfiles:
        try:
            total += _index_transcript(con, f)
        except Exception as e:
            print(f"  WARN transcript {f.name}: {type(e).__name__}: {e}", file=sys.stderr)
    for d in doc_sources():
        try:
            total += _index_doc(con, d)
        except Exception as e:
            print(f"  WARN doc {d.name}: {type(e).__name__}: {e}", file=sys.stderr)

    print(f"indexed +{total} new rows from {len(tfiles)} transcript(s) + docs "
          f"(dir: {tdir})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
