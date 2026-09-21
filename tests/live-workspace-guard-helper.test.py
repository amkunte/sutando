#!/usr/bin/env python3
"""The shared live-workspace guard must excuse live services WITHOUT excusing an escape.

A widened exemption and a working guard both look like a green suite, so the
discriminator is asserted here rather than trusted. Both directions are pinned:
a continuously-rewritten path is ambient, a written-once path is not.
"""
from __future__ import annotations

import importlib.util
import shutil
import tempfile
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "live_workspace_guard", REPO / "tests" / "_helpers" / "live_workspace_guard.py")
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)

d = Path(tempfile.mkdtemp(prefix="lwg-"))
try:
    static, churn = d / "escape.txt", d / "churner.txt"
    static.write_text("written once, like a fixture escape")
    churn.write_text("0")

    stop = threading.Event()

    def _beat():
        i = 0
        while not stop.is_set():
            time.sleep(0.5)
            i += 1
            churn.write_text(str(i))

    t = threading.Thread(target=_beat, daemon=True)
    t.start()
    try:
        ambient = g.ambient_subset({str(static), str(churn)}, settle_s=6.0)
    finally:
        stop.set()
        t.join(timeout=5)

    assert str(churn) in ambient, \
        "a continuously-written path was not recognised as ambient churn"
    assert str(static) not in ambient, \
        "a write-once path was excused as ambient — the escape guard is disabled"

    # No suspects must cost nothing and must not block for the settle window.
    t0 = time.monotonic()
    assert g.ambient_subset(set()) == set()
    assert time.monotonic() - t0 < 1.0, "empty suspect set waited for the settle window"

    # fingerprint must actually see files — an empty walk is a broken instrument,
    # not a clean workspace (same hole tests/hermetic-workspace-guard.test.py pins).
    fp = g.workspace_fingerprint(d)
    assert len(fp) >= 2, fp
    assert g.workspace_fingerprint(d / "does-not-exist") == {}

    # escapes() must attribute a write-once file and excuse nothing spuriously.
    before = g.workspace_fingerprint(d)
    (d / "planted.txt").write_text("fixture escape")
    after = g.workspace_fingerprint(d)
    deleted, modified, added, ambient2, suspects = g.escapes(before, after, settle_s=3.0)
    assert str(d / "planted.txt") in added, (added, ambient2)
finally:
    shutil.rmtree(d, ignore_errors=True)

print("ok: live-workspace guard discriminates churn from escape, both directions")
