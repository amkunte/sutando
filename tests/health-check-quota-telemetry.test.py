#!/usr/bin/env python3
"""Regression test: check_quota_telemetry must surface a proxy that is up but
producing no quota state.

The gap it covers: quota-state.json is written by the credential proxy from
upstream response headers, so it only appears if a core actually ROUTES
through the proxy. src/startup.sh is the only thing exporting
ANTHROPIC_BASE_URL=http://localhost:7846, and a supervisor-launched core
never runs startup.sh. On such a host the proxy is healthy and listening,
every check is green, and quota telemetry is silently absent forever — the
proactive loop's budget check reads "unknown" every pass with no explanation.

The pre-existing credential-proxy check cannot catch this: it is a plain
TCP-listening probe (correct for a forwarding proxy with no liveness
endpoint), so "listening" is the most it can ever assert.

Run: python3 tests/health-check-quota-telemetry.test.py
Exit: 0 on pass, 1 on fail.
"""
from __future__ import annotations
import importlib.util
import os
import tempfile
import time
import unittest
from unittest import mock
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _load_health_check():
    spec = importlib.util.spec_from_file_location(
        "health_check_quota_test", REPO / "src" / "health-check.py"
    )
    hc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hc)
    return hc


class TestQuotaTelemetryCheck(unittest.TestCase):
    def setUp(self):
        self.hc = _load_health_check()
        self._tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self._tmp.name)
        (self.ws / "state").mkdir(parents=True, exist_ok=True)
        self.hc.WORKSPACE_DIR = self.ws

    def tearDown(self):
        self._tmp.cleanup()

    def _write_quota(self, mtime_age_sec: float = 0.0) -> Path:
        p = self.ws / "state" / "quota-state.json"
        p.write_text('{"remaining_pct": 42}')
        if mtime_age_sec:
            past = time.time() - mtime_age_sec
            os.utime(p, (past, past))
        return p

    def test_proxy_up_but_no_quota_state_warns(self):
        """The actual bug: green everywhere, telemetry silently dead."""
        r = self.hc.check_quota_telemetry("ok")
        self.assertEqual(r["status"], "warn")
        self.assertIn("never written quota-state.json", r["detail"])
        # The detail must name the cause, not just the symptom — otherwise the
        # reader has no idea why an up proxy produces nothing.
        self.assertIn("ANTHROPIC_BASE_URL", r["detail"])

    def test_proxy_down_stays_silent(self):
        """Not every host routes through the proxy, and its own check already
        reports it as down. Warning twice would be noise."""
        for status in ("warn", "down"):
            r = self.hc.check_quota_telemetry(status)
            self.assertEqual(r["status"], "ok", f"status={status}")
            self.assertIn("not expected", r["detail"])

    def test_quota_state_present_is_ok_with_age(self):
        self._write_quota()
        r = self.hc.check_quota_telemetry("ok")
        self.assertEqual(r["status"], "ok")
        self.assertIn("present", r["detail"])

    def test_old_quota_state_with_no_live_core_does_not_warn(self):
        """Deliberate: a quiet core legitimately writes nothing for a long
        time, so age ALONE must never warn — that would fire on healthy idle
        hosts. Pin it so nobody 'improves' this into a flaky check later.

        Refined 2026-07-23: the pin is specifically "stale + no live core ->
        ok". Staleness is now consulted, but only in combination with the core
        heartbeat (see the live-core test below) — which is what makes it a
        real signal instead of the flaky one this test was written to prevent.
        """
        self._write_quota(mtime_age_sec=60 * 60 * 24 * 3)
        r = self.hc.check_quota_telemetry("ok")
        self.assertEqual(r["status"], "ok")
        self.assertIn("4320m ago", r["detail"])

    def _beat(self, age_sec: float = 0.0) -> Path:
        """Write a core heartbeat, as src/core_heartbeat.py does every 30s."""
        cores = self.ws / "state" / "cores"
        cores.mkdir(parents=True, exist_ok=True)
        p = cores / "testhost.alive"
        p.write_text('{"host": "testhost"}')
        if age_sec:
            past = time.time() - age_sec
            os.utime(p, (past, past))
        return p

    def test_stale_quota_state_with_live_core_warns(self):
        """The second, subtler half of the same bug — and the one that actually
        bit on 2026-07-23.

        Absence only catches a host that NEVER routed through the proxy. A host
        that routed once and then restarted without ANTHROPIC_BASE_URL keeps
        quota-state.json forever, so presence stays true while the data goes
        dead. On Goose the file sat 4h stale (its 5h window had already reset)
        while this check said `ok` and read-quota.py said "STALE" off the SAME
        file. The loop budgeted every pass against that dead record.

        A live heartbeat means the core is making API calls; the proxy rewrites
        this file on every upstream response. So live core + stale file cannot
        be idleness — it is broken wiring.
        """
        self._write_quota(mtime_age_sec=60 * 60 * 4)
        self._beat()
        r = self.hc.check_quota_telemetry("ok")
        self.assertEqual(r["status"], "warn")
        self.assertIn("ANTHROPIC_BASE_URL", r["detail"])
        self.assertIn("240m stale", r["detail"])

    def test_fresh_quota_state_with_live_core_is_ok(self):
        """The healthy routed host — the common case. Must stay quiet, or the
        warning is worthless."""
        self._write_quota()
        self._beat()
        r = self.hc.check_quota_telemetry("ok")
        self.assertEqual(r["status"], "ok")

    def test_stale_quota_state_with_dead_heartbeat_does_not_warn(self):
        """A heartbeat file that exists but is old means the core is gone, not
        that wiring is broken — no API calls are being made, so nothing should
        be updating quota state. Guards the boundary from the other side: it is
        heartbeat *freshness* that discriminates, not mere file presence."""
        self._write_quota(mtime_age_sec=60 * 60 * 4)
        self._beat(age_sec=60 * 60)
        r = self.hc.check_quota_telemetry("ok")
        self.assertEqual(r["status"], "ok")

    def test_stat_failure_still_reports_present(self):
        """`exists()` true but `stat()` raising is rare (file removed mid-check,
        permissions changed) — but a health tick must degrade to a less precise
        detail, never raise. Without this guard one unlucky race takes down the
        whole check run, which is strictly worse than losing the age string."""
        self._write_quota()
        # `exists()` calls stat() internally and swallows OSError by returning
        # False — patching stat alone would silently exercise the ABSENT branch
        # instead, so exists() is pinned True to isolate the one being tested.
        with mock.patch.object(Path, "exists", return_value=True), mock.patch.object(
            Path, "stat", side_effect=OSError("boom")
        ):
            r = self.hc.check_quota_telemetry("ok")
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["detail"], "quota state present")


if __name__ == "__main__":
    unittest.main(verbosity=2)
