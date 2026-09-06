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
    # A real presenter sentinel holds an ISO-8601 expiry (scripts/presenter-mode.sh);
    # an empty file is malformed and must NOT read as paused. See
    # tests/loop-paused-sentinel-expiry.test.py.
    future = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 1800))
    (ws / "state" / "presenter-mode.sentinel").write_text(future + "\n")
    assert mod.check_core_proactive_loop()["status"] == "ok"


def test_stale_idle_still_warns_when_the_pause_has_expired():
    """A sentinel left behind past its window must not mute the check forever."""
    mod = _load(); ws = _ws(mod)
    _status(ws, "idle", 5400)
    past = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 3600))
    (ws / "state" / "presenter-mode.sentinel").write_text(past + "\n")
    assert mod.check_core_proactive_loop()["status"] == "warn"


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


def _alive(ws, started_ago):
    d = ws / "state" / "cores"
    d.mkdir(exist_ok=True)
    (d / "testhost.alive").write_text(
        json.dumps({"host": "testhost", "started_at": time.time() - started_ago})
    )


def test_stale_idle_suppressed_right_after_a_core_restart():
    """core-status.json survives a restart; a fresh core inherits a stale idle."""
    mod = _load(); ws = _ws(mod)
    _status(ws, "idle", 5400)
    _alive(ws, 60)            # core booted a minute ago
    assert mod.check_core_proactive_loop()["status"] == "ok"


def test_stale_idle_still_warns_when_the_core_is_long_running():
    """The suppressor must not mask a genuinely dead loop on an old core."""
    mod = _load(); ws = _ws(mod)
    _status(ws, "idle", 5400)
    _alive(ws, 86400)         # core up for a day
    assert mod.check_core_proactive_loop()["status"] == "warn"


def test_boot_grace_fails_open_when_heartbeat_is_missing():
    mod = _load(); ws = _ws(mod)
    _status(ws, "idle", 5400)
    # no state/cores at all
    assert mod.check_core_proactive_loop()["status"] == "warn"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"{len(fns)} passed")
