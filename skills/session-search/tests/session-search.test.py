#!/usr/bin/env python3
"""Tests for session-search. pytest-free; uses a temp workspace + synthetic
transcript so it never touches real session data or Discord.

Run: python3 skills/session-search/tests/session-search.test.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
SCRIPTS = SKILL / "scripts"
_fails = []


def check(name, cond):
    print(("PASS" if cond else "FAIL") + f"  {name}")
    if not cond:
        _fails.append(name)


def run(script, *a):
    return subprocess.run([sys.executable, str(SCRIPTS / script), *a],
                          capture_output=True, text=True, env=os.environ)


def write_jsonl(path, records):
    with path.open("a", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="session-search-test-")
    os.environ["SUTANDO_WORKSPACE"] = tmp
    tdir = Path(tmp) / "transcripts"
    tdir.mkdir(parents=True, exist_ok=True)
    os.environ["SESSION_SEARCH_TRANSCRIPT_DIR"] = str(tdir)
    # no doc sources in temp repo → keep it transcript-only
    os.environ["SUTANDO_REPO_DIR"] = tmp

    t = tdir / "sess1.jsonl"
    write_jsonl(t, [
        {"type": "user", "timestamp": "2026-01-01T10:00:00Z",
         "message": {"role": "user", "content": "Please build the kangaroo telemetry exporter"}},
        {"type": "assistant", "timestamp": "2026-01-01T10:00:05Z",
         "message": {"role": "assistant", "content": [
             {"type": "thinking", "text": "internal pelican reasoning should NOT be indexed"},
             {"type": "text", "text": "Done — the kangaroo exporter ships metrics over UDP."}]}},
        {"type": "system", "timestamp": "x", "message": {"content": "ignore me"}},
    ])

    # build
    r = run("index.py")
    check("index build succeeds", r.returncode == 0)

    # search finds the user phrase
    r = run("search.py", "--json", "kangaroo")
    out = json.loads(r.stdout or "[]")
    check("search finds 'kangaroo' (both messages)", len(out) >= 2)

    # thinking blocks are NOT indexed
    r = run("search.py", "--json", "pelican")
    check("thinking blocks excluded from index", json.loads(r.stdout or "[]") == [])

    # role filter
    r = run("search.py", "--json", "--role", "user", "kangaroo")
    rows = json.loads(r.stdout or "[]")
    check("role=user filter returns only user rows", rows and all(x["role"] == "user" for x in rows))

    # system-type lines skipped
    r = run("search.py", "--json", "ignore me")
    check("non user/assistant types skipped", json.loads(r.stdout or "[]") == [])

    # incremental: append new lines, reindex adds only the new ones
    r = run("index.py", "--stats")
    before = r.stdout
    write_jsonl(t, [
        {"type": "user", "timestamp": "2026-01-02T09:00:00Z",
         "message": {"role": "user", "content": "Add a wombat dashboard please"}},
    ])
    r = run("index.py")
    check("incremental reindex reports +1", "+1 new rows" in r.stdout)
    r = run("search.py", "--json", "wombat")
    check("new content searchable after incremental", len(json.loads(r.stdout or "[]")) == 1)

    # re-run with no changes → +0
    r = run("index.py")
    check("no-change reindex adds +0", "+0 new rows" in r.stdout)

    # rebuild from scratch still works and dedups (no doubled rows)
    r = run("index.py", "--rebuild")
    r = run("search.py", "--json", "kangaroo")
    check("rebuild does not duplicate rows", len(json.loads(r.stdout or "[]")) == 2)

    # phrase + prefix query
    r = run("search.py", "--json", "\"kangaroo exporter\"")
    check("quoted phrase match works", len(json.loads(r.stdout or "[]")) >= 1)

    print()
    if _fails:
        print(f"{len(_fails)} FAILED: {_fails}"); return 1
    print("all tests passed"); return 0


if __name__ == "__main__":
    raise SystemExit(main())
