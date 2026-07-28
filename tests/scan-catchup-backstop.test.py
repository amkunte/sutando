#!/usr/bin/env python3
"""CI guard for `src/scan-catchup.py` — the self-healing skill-scan backstop.

This module is the thing that makes a silently-stopped scan impossible to miss:
it re-derives "is a scan overdue?" from `last_scan` on disk rather than from a
live cron, so cron expiry or a session restart cannot quietly turn a channel off
(see build_log 2026-06-20, #orders going silent mid-trip).

It had **no test coverage at all** until this file. That is the wrong gap to
leave open: an untested backstop can fail silently, and silent failure is
precisely the failure class it exists to catch. A regression here does not
announce itself — the scans just stop being flagged, exactly as if everything
were fine.

Covers current behaviour only:
  * the roaming-node gate (`SKIP_SKILL_SCANS`) short-circuits before any scan
  * a fresh scan is silent
  * an overdue scan emits SCANDUE
  * the GRACE boundary (1.5x cadence) absorbs one missed tick
  * a missing state file is treated as due (first run)
  * an unparseable `last_scan` is treated as due

Run: python3 tests/scan-catchup-backstop.test.py
Also run under /usr/bin/python3 (3.9.6) — this module ships to a launchd host.
"""
import contextlib
import importlib.util
import io
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MODULE = REPO / "src" / "scan-catchup.py"


def _load():
    """Import the hyphenated module by path (not a valid identifier)."""
    spec = importlib.util.spec_from_file_location("scan_catchup", MODULE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run(mod, state_path, cadence_hours=24):
    """Drive main() over a single synthetic scan, capturing stdout."""
    mod.SCANS = [{
        "name": "fixture-scan",
        "state": state_path,
        "cadence_hours": cadence_hours,
        "hint": "HINT",
    }]
    mod._node_skips_scans = lambda: False
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        mod.main()
    return buf.getvalue().strip()


def _write(path, hours_ago=None, raw=None):
    if raw is not None:
        path.write_text(raw)
        return
    ts = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
    path.write_text(json.dumps({"last_scan": ts}))


def test_roaming_node_gate_short_circuits():
    """SKIP_SKILL_SCANS=1 must return before evaluating any scan.

    Guards the Maverick/Goose split: the roaming node must not double-post.
    """
    mod = _load()
    prev = os.environ.get("SKIP_SKILL_SCANS")
    os.environ["SKIP_SKILL_SCANS"] = "1"
    try:
        assert mod._node_skips_scans() is True
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.json"
            _write(p, hours_ago=1000)  # wildly overdue
            mod.SCANS = [{"name": "x", "state": p, "cadence_hours": 24, "hint": "H"}]
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                mod.main()
            assert buf.getvalue().strip() == "", "gated node must emit nothing"
    finally:
        if prev is None:
            os.environ.pop("SKIP_SKILL_SCANS", None)
        else:
            os.environ["SKIP_SKILL_SCANS"] = prev


def test_fresh_scan_is_silent():
    mod = _load()
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "s.json"
        _write(p, hours_ago=1)
        assert _run(mod, p) == ""


def test_overdue_scan_emits_scandue():
    mod = _load()
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "s.json"
        _write(p, hours_ago=100)
        out = _run(mod, p)
        assert out.startswith("SCANDUE fixture-scan"), out
        assert "overdue" in out and "HINT" in out, out


def test_grace_absorbs_one_missed_tick():
    """1.4x cadence stays silent; 1.6x fires. Pins GRACE=1.5.

    Without this, someone "tightening" GRACE to 1.0 would make the backstop
    re-fire on every ordinary late tick — noisy enough that the real signal
    gets ignored.
    """
    mod = _load()
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "s.json"
        _write(p, hours_ago=24 * 1.4)
        assert _run(mod, p) == "", "inside grace must stay silent"
        _write(p, hours_ago=24 * 1.6)
        assert _run(mod, p).startswith("SCANDUE"), "past grace must fire"


def test_missing_state_treated_as_due():
    mod = _load()
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "does-not-exist.json"
        out = _run(mod, p)
        assert out.startswith("SCANDUE fixture-scan"), out


def test_unparseable_last_scan_treated_as_due():
    """Both a corrupt file and a valid-JSON-but-garbage timestamp must fire.

    Fail-safe direction: unknown freshness surfaces rather than silently
    suppressing, which is what makes the backstop trustworthy.
    """
    mod = _load()
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "s.json"
        _write(p, raw="{not json")
        assert _run(mod, p).startswith("SCANDUE"), "corrupt JSON must fire"
        _write(p, raw=json.dumps({"last_scan": "not-a-timestamp"}))
        assert _run(mod, p).startswith("SCANDUE"), "bad timestamp must fire"


TESTS = [
    test_roaming_node_gate_short_circuits,
    test_fresh_scan_is_silent,
    test_overdue_scan_emits_scandue,
    test_grace_absorbs_one_missed_tick,
    test_missing_state_treated_as_due,
    test_unparseable_last_scan_treated_as_due,
]


if __name__ == "__main__":
    failed = 0
    for t in TESTS:
        try:
            t()
            print(f"PASS: {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL: {t.__name__}: {e}")
    print(f"{len(TESTS) - failed} passed, {failed} failed")
    raise SystemExit(1 if failed else 0)
