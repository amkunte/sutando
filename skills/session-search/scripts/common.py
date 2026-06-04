#!/usr/bin/env python3
"""Shared helpers for session-search — an FTS5 full-text index over Sutando's
own session history (Claude Code conversation transcripts) plus a few project
docs, so "when did we discuss X?" is a sub-second query instead of grepping
hundreds of MB of JSONL.

Self-contained, stdlib-only, Python 3.9-safe.
"""
from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path

# ---- paths ---------------------------------------------------------------

def resolve_workspace() -> Path:
    env = os.environ.get("SUTANDO_WORKSPACE")
    if env:
        return Path(os.path.expanduser(env))
    return Path.home() / ".sutando" / "workspace"


def repo_dir() -> Path:
    env = os.environ.get("SUTANDO_REPO_DIR")
    if env:
        return Path(os.path.expanduser(env))
    # scripts live at <repo>/skills/session-search/scripts/common.py
    return Path(__file__).resolve().parents[3]


def db_path() -> Path:
    d = resolve_workspace() / "state"
    d.mkdir(parents=True, exist_ok=True)
    return d / "session-search.db"


def _mangle(p: str) -> str:
    """Claude Code names its per-project transcript dir by replacing every
    non-alphanumeric char in the absolute project path with '-'."""
    return re.sub(r"[^a-zA-Z0-9]", "-", p)


def transcript_dir() -> Path:
    """Where this project's Claude Code .jsonl transcripts live.

    Override with SESSION_SEARCH_TRANSCRIPT_DIR. Default: the
    ~/.claude/projects/<mangled-repo-path> dir; if that exact dir is absent,
    fall back to the projects subdir whose name contains the repo basename."""
    env = os.environ.get("SESSION_SEARCH_TRANSCRIPT_DIR")
    if env:
        return Path(os.path.expanduser(env))
    projects = Path.home() / ".claude" / "projects"
    exact = projects / _mangle(str(repo_dir()))
    if exact.is_dir():
        return exact
    # fallback: match by repo basename suffix
    base = repo_dir().name
    if projects.is_dir():
        cands = sorted(p for p in projects.iterdir() if p.is_dir() and p.name.endswith(_mangle(base)))
        if cands:
            return cands[0]
    return exact  # may not exist; callers handle empty


def doc_sources() -> list:
    """Best-effort extra docs to index alongside transcripts. Each may be
    absent (skipped silently)."""
    ws = resolve_workspace()
    repo = repo_dir()
    out = []
    for p in [repo / "build_log.md", ws / "build_log.md"]:
        if p.is_file():
            out.append(p)
    for root in [repo / "notes", ws / "notes"]:
        if root.is_dir():
            out.extend(sorted(root.rglob("*.md")))
    # de-dup while preserving order
    seen, uniq = set(), []
    for p in out:
        rp = str(p.resolve())
        if rp not in seen:
            seen.add(rp); uniq.append(p)
    return uniq


# ---- schema --------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS docs(
  id INTEGER PRIMARY KEY,
  source TEXT NOT NULL,
  kind TEXT NOT NULL,          -- 'transcript' | 'doc'
  session_id TEXT,
  role TEXT,                   -- user | assistant | doc
  ts TEXT,
  line_no INTEGER,
  text TEXT NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS docs_fts USING fts5(
  text, role, session_id,
  content='docs', content_rowid='id',
  tokenize='porter unicode61'
);
CREATE TRIGGER IF NOT EXISTS docs_ai AFTER INSERT ON docs BEGIN
  INSERT INTO docs_fts(rowid, text, role, session_id)
    VALUES (new.id, new.text, new.role, new.session_id);
END;
CREATE TRIGGER IF NOT EXISTS docs_ad AFTER DELETE ON docs BEGIN
  INSERT INTO docs_fts(docs_fts, rowid, text, role, session_id)
    VALUES ('delete', old.id, old.text, old.role, old.session_id);
END;
CREATE TABLE IF NOT EXISTS files(
  path TEXT PRIMARY KEY,
  offset INTEGER NOT NULL DEFAULT 0,  -- bytes consumed (transcripts); 0 for docs
  mtime REAL,
  nrows INTEGER NOT NULL DEFAULT 0,   -- transcripts: last raw-LINE cursor (carries line_no
                                      -- across incremental runs; NOT a row count). docs: rows.
  indexed_at TEXT
);
"""


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(str(db_path()), timeout=30)
    # WAL lets search.py read concurrently while index.py writes (no
    # "database is locked" during a reindex). synchronous=NORMAL is safe
    # under WAL and much faster for the bulk insert.
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.executescript(SCHEMA)
    return con


def index_lock():
    """Acquire an exclusive, non-blocking file lock for indexing. Returns the
    open lock-file handle on success (keep it alive for the run), or None if
    another index run already holds it — callers should exit cleanly to avoid
    two indexers double-inserting (no row-level dedup exists)."""
    import fcntl
    lf = (resolve_workspace() / "state" / ".session-search.index.lock").open("w")
    try:
        fcntl.flock(lf, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return lf
    except OSError:
        lf.close()
        return None
