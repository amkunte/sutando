#!/usr/bin/env python3
"""
Tests for the load_allowed() error-handling contract in
src/telegram-bridge.py, added 2026-05-13.

Background: load_allowed() previously used a bare `except:` that
collapsed three distinct conditions into one silent "deny all" outcome:
  - FileNotFoundError (uninitialized — legitimate)
  - JSONDecodeError / OSError / ValueError (real fault — should be loud)
  - any other unexpected error (should propagate)

A transient OS error on access.json would silently reject every
Telegram message while the bridge process remained alive and passed
the watchdog's process-alive check. Owner-facing symptom: messages
just stop arriving with no indication of why.

The fix: catch FileNotFoundError quietly, catch
(JSONDecodeError, OSError, ValueError) with a loud log line that
includes the exception type + path, and surface other exceptions.

These tests duplicate the function rather than import it — the
bridge module starts long-polling at import time (no clean import
boundary), and the repo's test pattern is duplicate-logic-with-sync
note. Keep this function in sync with src/telegram-bridge.py:213.
"""

from __future__ import annotations
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path


def load_allowed(path: Path):
    """Mirror of src/telegram-bridge.py:load_allowed (line 226).

    Kept in sync manually. If you change one, change the other.

    Returns: set of allowed sender IDs, OR None if access.json doesn't exist.
    The None vs empty-set distinction matters for TOFU auto-onboarding (see
    docstring on the real function).
    """
    try:
        data = json.loads(path.read_text())
        return set(data.get("allowFrom", []))
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, OSError, ValueError) as e:
        print(
            f"WARN load_allowed: {type(e).__name__} reading {path}: {e}. "
            f"Denying all messages until config is readable.",
            flush=True,
        )
        return set()


class LoadAllowedTests(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        for f in Path(self.tmpdir).iterdir():
            f.unlink()
        os.rmdir(self.tmpdir)

    def _write(self, content: str) -> Path:
        p = Path(self.tmpdir) / "access.json"
        p.write_text(content)
        return p

    def test_valid_config_returns_allowfrom_set(self):
        p = self._write(json.dumps({"allowFrom": ["111", "222"]}))
        self.assertEqual(load_allowed(p), {"111", "222"})

    def test_missing_file_returns_None_for_tofu(self):
        # File missing → return None, NOT empty set. The None vs empty-set
        # distinction is the trust-on-first-use signal: the bridge auto-onboards
        # the first DM sender as owner when access.json doesn't exist. Empty
        # set (file present, allowFrom: []) means "admin locked down, never
        # TOFU". Conflating the two — as the original test asserted before
        # the 2026-05-19 rebase merge — silently breaks first-time setup.
        # No warn line either: missing file is a legitimate uninitialized state.
        buf = io.StringIO()
        with redirect_stdout(buf):
            result = load_allowed(Path(self.tmpdir) / "nonexistent.json")
        self.assertIsNone(result)
        self.assertNotIn("WARN", buf.getvalue())

    def test_explicit_empty_allowfrom_returns_empty_set(self):
        # Distinct from missing-file: file IS present with empty allowFrom.
        # That's the "locked down, never TOFU" admin choice. Must return
        # set(), not None — otherwise the bridge would auto-onboard the
        # next sender despite the admin's explicit lockdown.
        p = self._write(json.dumps({"allowFrom": []}))
        result = load_allowed(p)
        self.assertEqual(result, set())
        self.assertIsNotNone(result)

    def test_corrupt_json_is_loud_empty(self):
        # Real fault — must warn loudly so owner sees it in bridge logs.
        p = self._write("{ not valid json at all")
        buf = io.StringIO()
        with redirect_stdout(buf):
            result = load_allowed(p)
        self.assertEqual(result, set())
        self.assertIn("WARN", buf.getvalue())
        self.assertIn("JSONDecodeError", buf.getvalue())

    def test_missing_allowfrom_key_returns_empty(self):
        # Structurally valid JSON without the key is fine — just no allow-list.
        # No warn (the file parsed OK).
        p = self._write(json.dumps({"dmPolicy": "pairing"}))
        buf = io.StringIO()
        with redirect_stdout(buf):
            result = load_allowed(p)
        self.assertEqual(result, set())
        self.assertNotIn("WARN", buf.getvalue())

    def test_permission_denied_is_loud_empty(self):
        # OSError class — should land in the loud branch.
        p = self._write(json.dumps({"allowFrom": ["999"]}))
        os.chmod(p, 0o000)
        try:
            buf = io.StringIO()
            with redirect_stdout(buf):
                result = load_allowed(p)
            # On macOS root may bypass chmod 0; only assert on non-root runs.
            if os.geteuid() != 0:
                self.assertEqual(result, set())
                self.assertIn("WARN", buf.getvalue())
        finally:
            os.chmod(p, 0o644)


if __name__ == "__main__":
    unittest.main(verbosity=2)
