#!/usr/bin/env python3
"""Regression guards: hostname-resolution resilience (2026-06-22).

Two related fragilities found when a node's `hostname` is a DHCP-assigned
name (e.g. `Chis-MBP.hsd1.wa.comcast.net`) that is unstable and not
DNS-resolvable:

1. `agent-api.py` did `socket.gethostbyname(socket.gethostname())` at startup
   for an informational log line. An unresolvable hostname raises `gaierror`
   (an `OSError`) and CRASHED agent-api on boot. Fix: `_resolve_local_ip()`
   catches `OSError` and falls back to loopback.

2. `core_heartbeat._hostname()` used the raw `socket.gethostname()` for the
   `<label>.alive` filename, diverging from `util_paths._host_label()` (which
   honors `$SUTANDO_HOST_LABEL`). On a DHCP-drifting host that produced TWO
   divergent `.alive` files and ignored the per-host label pin. Fix: delegate
   to `_host_label()`.

Run: python3 tests/hostname-resolution-resilience.test.py
Exit: 0 = all pass, 1 = failure
"""
from __future__ import annotations
import importlib.util
import os
import re
import socket
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


def _load_core_heartbeat():
    spec = importlib.util.spec_from_file_location(
        "core_heartbeat", ROOT / "src" / "core_heartbeat.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class CoreHeartbeatHostnameTests(unittest.TestCase):
    def setUp(self):
        os.environ.pop("SUTANDO_HOST_LABEL", None)

    def tearDown(self):
        os.environ.pop("SUTANDO_HOST_LABEL", None)

    def test_hostname_honors_label_pin(self):
        os.environ["SUTANDO_HOST_LABEL"] = "Chis-MacBook-Pro"
        ch = _load_core_heartbeat()
        self.assertEqual(
            ch._hostname(), "Chis-MacBook-Pro",
            "_hostname() must honor $SUTANDO_HOST_LABEL (via _host_label), "
            "so the .alive label survives DHCP hostname drift",
        )

    def test_hostname_fallback_matches_short_hostname(self):
        # Tier 3 of _host_label()'s precedence (env -> scutil LocalHostName ->
        # short hostname). Clearing the env only drops tier 1, so on macOS this
        # previously landed on tier 2 and compared LocalHostName ("Abhis-Mac-mini")
        # against the short hostname ("abhis-mac-mini") — a guaranteed failure on
        # any host whose Bonjour name is not byte-identical to its DHCP hostname,
        # which is exactly the drift this file exists to guard. Force tier 3 by
        # making the scutil probe fail, the way it does on Linux.
        import util_paths
        real_run = util_paths.subprocess.run

        def _no_scutil(cmd, *a, **kw):
            if cmd and "scutil" in cmd[0]:
                raise FileNotFoundError("scutil unavailable (simulated non-macOS)")
            return real_run(cmd, *a, **kw)

        util_paths.subprocess.run = _no_scutil
        try:
            ch = _load_core_heartbeat()
            self.assertEqual(ch._hostname(), socket.gethostname().split(".")[0])
        finally:
            util_paths.subprocess.run = real_run

    def test_hostname_prefers_localhostname_over_dhcp_hostname(self):
        # Tier 2: with no env pin and scutil available, the stable Bonjour name
        # must win over the DHCP-assigned short hostname. This is the actual
        # anti-drift guarantee (2026-06-22 incident); nothing asserted it before.
        import util_paths
        try:
            probe = util_paths.subprocess.run(
                ["scutil", "--get", "LocalHostName"],
                capture_output=True, text=True, timeout=5,
            )
        except (OSError, Exception):
            self.skipTest("scutil unavailable (non-macOS) — tier 2 not applicable")
        local_host_name = (probe.stdout or "").strip()
        if probe.returncode != 0 or not local_host_name:
            self.skipTest("scutil returned no LocalHostName on this host")
        ch = _load_core_heartbeat()
        self.assertEqual(
            ch._hostname(), local_host_name,
            "_hostname() must prefer the stable scutil LocalHostName over the "
            "DHCP-drifting short hostname, or per-host paths split (two "
            "hosts/<label>/ dirs, phantom state/cores/<label>.alive)",
        )


class AgentApiGethostbynameGuardTests(unittest.TestCase):
    SRC = (ROOT / "src" / "agent-api.py").read_text()

    def test_gethostbyname_is_wrapped(self):
        self.assertIn("_resolve_local_ip", self.SRC,
                      "the local-IP resolution must go through the guarded helper")
        self.assertIn("except OSError", self.SRC,
                      "gethostbyname must be wrapped so an unresolvable hostname "
                      "can't crash startup")
        self.assertIn('return "127.0.0.1"', self.SRC,
                      "must fall back to loopback on resolution failure")

    def test_no_bare_crashing_call_remains(self):
        # The exact bare call that crashed must not be reintroduced at module top level.
        self.assertNotRegex(
            self.SRC,
            r"\n    local_ip = socket\.gethostbyname\(socket\.gethostname\(\)\)",
            "the bare unguarded gethostbyname call must not return",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
