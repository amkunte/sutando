#!/usr/bin/env python3
"""CI guard: the two core-liveness checks must notice a signal that STOPPED,
not just evaluate the value it last carried.

Both checks were blind in the same way. `check_core_proactive_loop` returned ok
for any status != "running" — but the loop writes "idle" at the END of every
pass, so a loop that died leaves a permanently-valid-looking "idle".
`check_core_supervisor` read a payload with no timestamp and never stat'd the
file, so a monitor that died months ago still reported its last-known state as
current. Both were observed live on 2026-08-30 (supervisor frozen 401h).

Fixture ages deliberately SPAN each threshold on both sides — a suite sitting
entirely below a boundary proves nothing about a scale-dependent bug.

Run: python3 tests/health-check-staleness.test.py
"""
import importlib.util
import json
import os
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))


def _load():
    spec = importlib.util.spec_from_file_location("hc", REPO / "src" / "health-check.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["hc"] = mod
    spec.loader.exec_module(mod)
    return mod


def _ws(mod):
    ws = Path(tempfile.mkdtemp())
    (ws / "state").mkdir()
    mod.WORKSPACE_DIR = ws
    mod.status_read_path = lambda n, w=None: ws / "state" / n
    return ws


def _status(ws, state, age):
    (ws / "state" / "core-status.json").write_text(
        json.dumps({"status": state, "ts": time.time() - age})
    )


def test_fresh_idle_is_ok():
    mod = _load(); ws = _ws(mod)
    _status(ws, "idle", 60)
    assert mod.check_core_proactive_loop()["status"] == "ok"


def test_stale_idle_warns():
    """The regression this guards: a dead loop looks exactly like a finished pass."""
    mod = _load(); ws = _ws(mod)
    _status(ws, "idle", 5400)
    r = mod.check_core_proactive_loop()
    assert r["status"] == "warn", r
    assert "idle for" in r["detail"]


def test_stale_idle_suppressed_while_deliberately_paused():
    mod = _load(); ws = _ws(mod)
    _status(ws, "idle", 5400)
    (ws / "state" / "presenter-mode.sentinel").write_text("")
    assert mod.check_core_proactive_loop()["status"] == "ok"


def test_stale_running_still_warns():
    """Regression guard: pre-existing behaviour must be unchanged."""
    mod = _load(); ws = _ws(mod)
    _status(ws, "running", 5400)
    assert mod.check_core_proactive_loop()["status"] == "warn"


def test_supervisor_fresh_is_ok():
    mod = _load(); ws = _ws(mod)
    p = ws / "state" / "core-supervisor.json"
    p.write_text(json.dumps({"state": "idle-ready"}))
    assert mod.check_core_supervisor()["status"] == "ok"


def test_supervisor_stale_warns():
    mod = _load(); ws = _ws(mod)
    p = ws / "state" / "core-supervisor.json"
    p.write_text(json.dumps({"state": "idle-ready"}))
    old = time.time() - 401 * 3600
    os.utime(p, (old, old))
    r = mod.check_core_supervisor()
    assert r["status"] == "warn", r
    assert "stale" in r["detail"]


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"{len(fns)} passed")
