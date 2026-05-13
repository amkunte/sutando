#!/usr/bin/env python3
"""
Tests for the `pending_replies` persistence helpers in `telegram-bridge.py`.

Background: 2026-05-13 ~11:35–11:56 PT, owner's RKLB question + 'are you
awake' ping were silently orphaned by a bridge restart that wiped the
in-RAM `pending_replies` map between message-receive and result-write.
PR #8 added disk persistence as a defensive backstop. These tests lock in
the contract so a future refactor can't silently re-introduce the data-loss
window.

Cases for _load / _save:
  a) round-trip — chat_id mapping preserved (int).
  b) re-save does NOT bump `added_at` for entries already on disk
     (otherwise the 24h age-prune is defeated by writer activity).
  c) entries older than 24h are dropped on load.
  d) malformed entries (chat_id not int) are dropped on load.
  e) malformed entries (missing `added_at`) treated as age 0 → dropped.
  f) non-dict entries (legacy raw-int schema) are dropped on load
     — there's no migration path; old format yields empty map.
  g) atomic write via .tmp leaves no leftover on success.
  h) load from missing file returns {} (no crash, no error).
  i) load from corrupted JSON returns {} (defensive).
  j) save survives transient OSError (silent on failure — persistence is
     defensive, not load-bearing).

Run: python3 tests/telegram-bridge-pending-replies-persist.test.py
Exit 0 on pass, 1 on fail.
"""

from __future__ import annotations
import importlib.util
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent

# TELEGRAM_BOT_TOKEN must be set or the module aborts at import.
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy-token-for-import-test")


def _load_module():
    spec = importlib.util.spec_from_file_location("tg", REPO / "src" / "telegram-bridge.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


tg = _load_module()


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        # Redirect persistence paths to a temp dir per test for isolation.
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self._patches = [
            patch.object(tg, "STATE_DIR", Path(self.tmp.name)),
            patch.object(tg, "PENDING_REPLIES_FILE", Path(self.tmp.name) / "p.json"),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)

    # (a)
    def test_roundtrip_chat_id_int(self):
        tg._save_pending_replies({"task-1": 100, "task-2": 200})
        self.assertEqual(tg._load_pending_replies(), {"task-1": 100, "task-2": 200})

    # (b)
    def test_resave_preserves_added_at(self):
        tg._save_pending_replies({"task-1": 100})
        raw1 = json.loads(tg.PENDING_REPLIES_FILE.read_text())
        time.sleep(0.05)
        tg._save_pending_replies({"task-1": 100})
        raw2 = json.loads(tg.PENDING_REPLIES_FILE.read_text())
        self.assertEqual(raw1["task-1"]["added_at"], raw2["task-1"]["added_at"])

    # (c)
    def test_aged_entries_dropped_on_load(self):
        tg._save_pending_replies({"keep": 1, "drop": 2})
        raw = json.loads(tg.PENDING_REPLIES_FILE.read_text())
        raw["drop"]["added_at"] = 0  # epoch start, way > 24h
        tg.PENDING_REPLIES_FILE.write_text(json.dumps(raw))
        out = tg._load_pending_replies()
        self.assertEqual(out, {"keep": 1})

    # (d)
    def test_malformed_chat_id_dropped(self):
        bad = {"task-x": {"chat_id": "not-an-int", "added_at": time.time()}}
        tg.PENDING_REPLIES_FILE.write_text(json.dumps(bad))
        self.assertEqual(tg._load_pending_replies(), {})

    # (e)
    def test_missing_added_at_treated_as_age_zero(self):
        bad = {"task-x": {"chat_id": 100}}  # no added_at
        tg.PENDING_REPLIES_FILE.write_text(json.dumps(bad))
        # _load defaults missing added_at to 0 in the age check → drops it
        # (now - 0 > 24h is always true).
        self.assertEqual(tg._load_pending_replies(), {})

    # (f)
    def test_non_dict_entry_legacy_raw_int_dropped(self):
        bad = {"task-x": 8606877252}  # what RAM dict would JSON-dump as
        tg.PENDING_REPLIES_FILE.write_text(json.dumps(bad))
        self.assertEqual(tg._load_pending_replies(), {})

    # (g)
    def test_atomic_write_no_tmp_leftover(self):
        tg._save_pending_replies({"task-1": 100})
        leftovers = list(Path(self.tmp.name).glob("*.tmp"))
        self.assertEqual(leftovers, [])

    # (h)
    def test_load_missing_file_returns_empty(self):
        # File never created
        self.assertEqual(tg._load_pending_replies(), {})

    # (i)
    def test_load_corrupted_json_returns_empty(self):
        tg.PENDING_REPLIES_FILE.write_text("{not valid json")
        self.assertEqual(tg._load_pending_replies(), {})

    # (j)
    def test_save_silent_on_oserror(self):
        # Point at an unwritable directory to trigger OSError on mkdir/write.
        with patch.object(tg, "STATE_DIR", Path("/dev/null/nonexistent")), \
             patch.object(tg, "PENDING_REPLIES_FILE", Path("/dev/null/nonexistent/p.json")):
            # Should not raise; should not crash.
            tg._save_pending_replies({"task-1": 100})


if __name__ == "__main__":
    runner = unittest.TextTestRunner(verbosity=2)
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(PersistenceTests)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
