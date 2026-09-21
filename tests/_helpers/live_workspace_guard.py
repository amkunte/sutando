"""Shared "the fixture must not write the live workspace" guard.

Two suites had grown near-identical copies of this (dm-result-multipart-upload,
discord-bridge-reply-directive) and both carried the same defect: a whole-tree
before/after fingerprint flags files a LIVE core rewrites on its own, so the guard
was red on every host running production and green only on CI — the state that
trains people to ignore a detector.

Measured on a running host with NO test executing: 4 of 61,225 paths moved in 40s
(logs/voice-agent.log, state/call-tiers.json, state/services-status.json,
state/cores/<host>.alive). The set is not fixed — a later run flagged
state/task-workstream-classifier.json instead — which is why this discriminates by
RE-OBSERVING a suspect rather than by naming paths. A name list cannot cover a set
that changes between runs.
"""
from __future__ import annotations

import hashlib
import time
from pathlib import Path

#: Seconds to watch a suspect before calling it a fixture escape. Sized from the
#: slowest emitter, measured not read: telegram-bridge's heartbeat gate gates on
#: `>= 60` but its poll loop puts the real inter-write gap at 69.4s, and a 35s
#: window left exactly that file as a false positive. Anything slower than this
#: still trips the guard — raise it against a fresh measurement, don't add a name.
AMBIENT_SETTLE_S = 90.0


def workspace_fingerprint(ws) -> dict:
    """(size, mtime) per file; CONTENT HASH under results/.

    Whole-tree rather than an enumerated list of the paths a fixture is known to
    touch — an assertion only catches what it looks at.
    """
    out: dict = {}
    ws = Path(ws)
    if not ws.is_dir():
        return out
    for f in ws.rglob("*"):
        if not f.is_file():
            continue
        try:
            if "results" in f.parts:
                out[str(f)] = ("sha", hashlib.sha256(f.read_bytes()).hexdigest())
            else:
                st = f.stat()
                out[str(f)] = (st.st_size, st.st_mtime)
        except OSError:
            pass
    return out


def _stat_key(path: str):
    p = Path(path)
    try:
        if "results" in p.parts:
            return ("sha", hashlib.sha256(p.read_bytes()).hexdigest())
        st = p.stat()
        return (st.st_size, st.st_mtime)
    except OSError:
        return None


def ambient_subset(suspects, settle_s: float = AMBIENT_SETTLE_S) -> set:
    """Of the paths that changed, which are STILL changing on their own?

    Call only AFTER the fixture has finished: anything that moves again is a live
    service, anything written once is an escape. Returns early once every suspect
    is accounted for, so a clean run costs nothing.
    """
    suspects = set(suspects)
    if not suspects:
        return set()
    first = {k: _stat_key(k) for k in suspects}
    deadline = time.monotonic() + settle_s
    ambient: set = set()
    while time.monotonic() < deadline and len(ambient) < len(suspects):
        time.sleep(1.0)
        for k in suspects - ambient:
            if _stat_key(k) != first[k]:
                ambient.add(k)
    return ambient


def escapes(before: dict, now: dict, settle_s: float = AMBIENT_SETTLE_S):
    """(deleted, modified, added) that the fixture is actually responsible for."""
    suspects = (set(before) ^ set(now)) | {
        k for k in (set(before) & set(now)) if before[k] != now[k]
    }
    ambient = ambient_subset(suspects, settle_s)
    deleted = sorted(k for k in before if k not in now and k not in ambient)
    added = sorted(k for k in now if k not in before and k not in ambient)
    modified = sorted(k for k in (set(before) & set(now))
                      if before[k] != now[k] and k not in ambient)
    return deleted, modified, added, ambient, suspects
