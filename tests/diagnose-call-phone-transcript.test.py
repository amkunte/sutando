#!/usr/bin/env python3
"""
Regression test — diagnose-call.py returned an empty transcript for every phone call.

Pre-fix (main branch): find_call() resolved the call SID as `call_sid or session_id`
and returned it as "callSid", but then built the transcript from the *raw*
`sessions.session_id` column:

    "transcript": _build_transcript(conn, chosen["session_id"]),

Phone sessions leave `sessions.session_id` NULL and carry the id in `call_sid`, so
_build_transcript() received None, hit its `if not session_id: return ""` guard, and
returned an empty string. diagnose-call.py then printed:

    turns: 0 (0 sutando, 0 user)
    ✓ no obvious issues from transcript heuristics

...for every phone call ever recorded. That is a *false all-clear*: a call with no
retrievable transcript is indistinguishable from a clean call, which is worse than an
error because the diagnostic silently agrees with you.

Voice sessions were unaffected — they populate session_id and leave call_sid NULL.

Post-fix (this PR): the transcript is built from `chosen["callSid"]`, which is already
`call_sid or session_id` and therefore correct for BOTH surfaces.

This script builds a synthetic conversation.sqlite containing one phone session (id in
call_sid, session_id NULL) and one voice session (id in session_id, call_sid NULL), then
asserts:
  - phone transcript is non-empty and contains both speakers   (FAILS on main)
  - voice transcript is unchanged                              (guards against regression)

Run: python3 tests/diagnose-call-phone-transcript.test.py
Exit code: 0 on pass, 1 on fail.
"""

import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "skills" / "regression-search" / "scripts" / "diagnose-call.py"

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        print(f"PASS: {name}")
        PASS += 1
    else:
        print(f"FAIL: {name}" + (f" — {detail}" if detail else ""))
        FAIL += 1


def build_db(path: str) -> None:
    """Minimal schema mirroring the per-surface layout diagnose-call.py reads."""
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE sessions (
            ts_unix REAL, source TEXT, session_id TEXT, call_sid TEXT,
            duration_ms INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE phone (
            id INTEGER PRIMARY KEY, ts_unix REAL, kind TEXT, text TEXT,
            duration_ms INTEGER, session_id TEXT
        );
        CREATE TABLE voice (
            id INTEGER PRIMARY KEY, ts_unix REAL, kind TEXT, text TEXT,
            duration_ms INTEGER, session_id TEXT
        );
        """
    )
    # Phone: id lives in call_sid, session_id is NULL (the bug's trigger).
    conn.execute(
        "INSERT INTO sessions (ts_unix, source, session_id, call_sid) VALUES (?,?,?,?)",
        (1_780_000_000.0, "phone", None, "CATESTPHONE0000000000000000000001"),
    )
    for i, (kind, text) in enumerate(
        [("agent", "Hi, this is Sutando."), ("user", "I need help."), ("agent", "Sure.")]
    ):
        conn.execute(
            "INSERT INTO phone (ts_unix, kind, text, duration_ms, session_id) VALUES (?,?,?,?,?)",
            (1_780_000_000.0 + i, kind, text, 0, "CATESTPHONE0000000000000000000001"),
        )
    # Voice: id lives in session_id, call_sid is NULL (must stay working).
    conn.execute(
        "INSERT INTO sessions (ts_unix, source, session_id, call_sid) VALUES (?,?,?,?)",
        (1_780_001_000.0, "voice", "session_TEST_VOICE_0001", None),
    )
    for i, (kind, text) in enumerate([("user", "voice line one"), ("agent", "voice reply")]):
        conn.execute(
            "INSERT INTO voice (ts_unix, kind, text, duration_ms, session_id) VALUES (?,?,?,?,?)",
            (1_780_001_000.0 + i, kind, text, 0, "session_TEST_VOICE_0001"),
        )
    conn.commit()
    conn.close()


def diagnose(db: str, sid: str) -> str:
    env = dict(os.environ, SUTANDO_CONVERSATION_DB=db)
    return subprocess.run(
        [sys.executable, str(SCRIPT), sid],
        capture_output=True, text=True, env=env, cwd=str(REPO),
    ).stdout


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        db = str(Path(td) / "conversation.sqlite")
        build_db(db)

        phone = diagnose(db, "CATESTPHONE0000000000000000000001")
        check(
            "phone call yields a non-empty transcript (FAILS on main — proves the bug)",
            "turns: 0 " not in phone and "turns:" in phone,
            f"got: {phone.strip()!r}",
        )
        check(
            "phone transcript counts both speakers",
            "turns: 3 (2 sutando, 1 user)" in phone,
            f"got: {phone.strip()!r}",
        )

        voice = diagnose(db, "session_TEST_VOICE_0001")
        check(
            "voice session transcript unchanged (regression guard)",
            "turns: 2 (1 sutando, 1 user)" in voice,
            f"got: {voice.strip()!r}",
        )

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
