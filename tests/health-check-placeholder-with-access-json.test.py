#!/usr/bin/env python3
"""A stub bridge token must be named as such even when access.json exists.

The placeholder branch used to be gated on `not access_file.exists()`, so it was
unreachable on exactly the hosts that had completed onboarding: the probe fell
through to pgrep and reported the generic "configured but not running", which
reads as a crashed bridge rather than a credential that cannot authenticate.

Runs the real health-check against a temp CLAUDE_CONFIG_DIR rather than
re-implementing the predicate — the defect was in whether the predicate is
reached, which a copied predicate cannot test.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
STUB = "DISCORD_BOT_TOKEN=test-stub-token\n"
REAL_SHAPE = "DISCORD_BOT_TOKEN=" + "x" * 70 + "\n"


def _discord_row(env_line: str, with_access: bool) -> dict:
    with tempfile.TemporaryDirectory() as d:
        chan = Path(d) / "channels" / "discord"
        chan.mkdir(parents=True)
        (chan / ".env").write_text(env_line)
        if with_access:
            (chan / "access.json").write_text(json.dumps({"allowFrom": ["1"]}))
        env = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(Path.home()),
               "CLAUDE_CONFIG_DIR": d}
        r = subprocess.run([sys.executable, str(REPO / "src" / "health-check.py"), "--json"],
                           capture_output=True, text=True, timeout=180, cwd=str(REPO), env=env)
        checks = json.loads(r.stdout)
        checks = checks.get("checks", checks) if isinstance(checks, dict) else checks
        for c in checks:
            if c.get("name") == "discord-bridge":
                return c
    return {}


stub_row = _discord_row(STUB, with_access=True)
assert stub_row, "discord-bridge row absent for a stub token with access.json present"
assert stub_row.get("status") == "warn", stub_row
assert "placeholder" in stub_row.get("detail", ""), \
    "stub token with access.json did not report as a placeholder: %r" % stub_row
assert "test-stub-token" not in stub_row.get("detail", ""), \
    "detail echoed the credential slot's value"

# Unconfigured host (no access.json) keeps the pre-existing silent skip.
assert _discord_row(STUB, with_access=False) == {}, \
    "stub token without access.json should stay silent, not warn"

# A real-shape token must still reach the liveness probe, not the placeholder path.
real_row = _discord_row(REAL_SHAPE, with_access=True)
assert "placeholder" not in real_row.get("detail", ""), \
    "70-char token misreported as a placeholder: %r" % real_row

print("ok: placeholder tokens are named even when access.json exists")
