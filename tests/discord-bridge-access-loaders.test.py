#!/usr/bin/env python3
"""
Tests for the three access.json loaders in src/discord-bridge.py:
  - load_allowed       (allowFrom set for DMs)
  - load_policy        (dmPolicy: pairing | allowlist | open)
  - load_channel_config (per-channel requireMention + allowFrom)

Added 2026-05-13 alongside the discord-bridge half of the bare-except
sweep (companion to PR #18 for telegram-bridge).

Background: all three loaders previously used bare `except:` clauses
that collapsed three distinct failure modes (missing-file, corrupt-JSON,
permission-error) into one silent default outcome. A transient OS error
on access.json would silently reject every Discord message or treat
every channel as unconfigured, while the bridge process stayed alive
and kept passing the watchdog's process-alive check.

The fix differentiates:
  - FileNotFoundError → silent default (pre-pairing legitimate state)
  - (JSONDecodeError, OSError, ValueError) → loud WARN + safe default
  - other exceptions propagate

Tests duplicate the loaders rather than import — discord-bridge.py
starts long-polling at import time. Pattern matches
tests/telegram-bridge-load-allowed.test.py and
tests/health-check-skip-placeholder-bridge.test.py.

Run: python3 tests/discord-bridge-access-loaders.test.py
Exit 0 on pass, 1 on fail.
"""

from __future__ import annotations
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path


def load_allowed(p: Path) -> set:
    """Mirror of src/discord-bridge.py:load_allowed."""
    try:
        data = json.loads(p.read_text())
        return set(data.get("allowFrom", []))
    except FileNotFoundError:
        return set()
    except (json.JSONDecodeError, OSError, ValueError) as e:
        print(
            f"WARN load_allowed: {type(e).__name__} reading {p}: {e}. "
            f"Falling back to empty allowlist.",
            flush=True,
        )
        return set()


def load_policy(p: Path) -> str:
    """Mirror of src/discord-bridge.py:load_policy."""
    try:
        data = json.loads(p.read_text())
        return data.get("dmPolicy", "pairing")
    except FileNotFoundError:
        return "pairing"
    except (json.JSONDecodeError, OSError, ValueError) as e:
        print(
            f"WARN load_policy: {type(e).__name__} reading {p}: {e}. "
            f"Falling back to 'pairing' policy.",
            flush=True,
        )
        return "pairing"


def load_channel_config(p: Path, channel_id: str):
    """Mirror of src/discord-bridge.py:load_channel_config."""
    try:
        data = json.loads(p.read_text())
        groups = data.get("groups", {})
        if channel_id in groups:
            cfg = groups[channel_id]
            if cfg is True:
                return (False, None)
            return (cfg.get("requireMention", True), set(cfg.get("allowFrom", [])))
        return None
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, OSError, ValueError) as e:
        print(
            f"WARN load_channel_config({channel_id}): {type(e).__name__} reading "
            f"{p}: {e}. Treating channel as unconfigured.",
            flush=True,
        )
        return None


class LoadAllowedTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.path = Path(self.tmpdir) / "access.json"

    def tearDown(self):
        for f in Path(self.tmpdir).iterdir():
            f.unlink()
        os.rmdir(self.tmpdir)

    def test_valid_returns_set(self):
        self.path.write_text(json.dumps({"allowFrom": ["a", "b"]}))
        self.assertEqual(load_allowed(self.path), {"a", "b"})

    def test_missing_file_silent_empty(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            result = load_allowed(self.path)  # never written
        self.assertEqual(result, set())
        self.assertNotIn("WARN", buf.getvalue())

    def test_corrupt_json_loud_empty(self):
        self.path.write_text("{ not json")
        buf = io.StringIO()
        with redirect_stdout(buf):
            result = load_allowed(self.path)
        self.assertEqual(result, set())
        self.assertIn("WARN", buf.getvalue())
        self.assertIn("JSONDecodeError", buf.getvalue())


class LoadPolicyTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.path = Path(self.tmpdir) / "access.json"

    def tearDown(self):
        for f in Path(self.tmpdir).iterdir():
            f.unlink()
        os.rmdir(self.tmpdir)

    def test_valid_returns_dmpolicy(self):
        self.path.write_text(json.dumps({"dmPolicy": "allowlist"}))
        self.assertEqual(load_policy(self.path), "allowlist")

    def test_missing_file_returns_default_pairing(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            result = load_policy(self.path)
        self.assertEqual(result, "pairing")
        self.assertNotIn("WARN", buf.getvalue())

    def test_corrupt_json_loud_default_pairing(self):
        self.path.write_text("not json")
        buf = io.StringIO()
        with redirect_stdout(buf):
            result = load_policy(self.path)
        self.assertEqual(result, "pairing")
        self.assertIn("WARN", buf.getvalue())

    def test_missing_dmpolicy_key_returns_default(self):
        # File parses but no dmPolicy key → default "pairing", no warn.
        self.path.write_text(json.dumps({"allowFrom": []}))
        buf = io.StringIO()
        with redirect_stdout(buf):
            result = load_policy(self.path)
        self.assertEqual(result, "pairing")
        self.assertNotIn("WARN", buf.getvalue())


class LoadChannelConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.path = Path(self.tmpdir) / "access.json"

    def tearDown(self):
        for f in Path(self.tmpdir).iterdir():
            f.unlink()
        os.rmdir(self.tmpdir)

    def test_configured_channel_returns_tuple(self):
        self.path.write_text(json.dumps({
            "groups": {"ch1": {"requireMention": False, "allowFrom": ["u1"]}}
        }))
        self.assertEqual(load_channel_config(self.path, "ch1"), (False, {"u1"}))

    def test_open_channel_true_value(self):
        self.path.write_text(json.dumps({"groups": {"ch1": True}}))
        self.assertEqual(load_channel_config(self.path, "ch1"), (False, None))

    def test_unconfigured_channel_returns_none(self):
        self.path.write_text(json.dumps({"groups": {"other": True}}))
        self.assertIsNone(load_channel_config(self.path, "ch1"))

    def test_missing_file_returns_none(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            result = load_channel_config(self.path, "ch1")
        self.assertIsNone(result)
        self.assertNotIn("WARN", buf.getvalue())

    def test_corrupt_json_loud_none(self):
        self.path.write_text("not valid")
        buf = io.StringIO()
        with redirect_stdout(buf):
            result = load_channel_config(self.path, "ch1")
        self.assertIsNone(result)
        self.assertIn("WARN", buf.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
