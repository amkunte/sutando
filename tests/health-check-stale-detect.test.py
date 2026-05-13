#!/usr/bin/env python3
"""
Tests for `_file_unchanged_since` in `src/health-check.py`.

Background: 2026-05-13. The commit-time-based logic had a hole — an
upstream merge that brought new file content via commits with timestamps
PREDATING proc_start would incorrectly return True (suppress stale flag).
voice-agent stayed silently 'ok' for 45 minutes while running old code.

The reflog + content-hash strategy fixes this. These tests lock the
contract in place.

Cases:
  a) src_mtime <= proc_start → fast-path True (file untouched).
  b) src_mtime > proc_start AND content matches HEAD-at-proc_start → True
     (idempotent mtime bump, e.g., `git checkout` no-op).
  c) src_mtime > proc_start AND content differs from HEAD-at-proc_start
     → False (real stale: e.g., upstream merge changed content).
  d) reflog has no entry at-or-before proc_start → False (fail safe).
  e) git show errors (file didn't exist at old_head) → False.
  f) subprocess timeout / OSError → False (defensive).

Run: python3 tests/health-check-stale-detect.test.py
Exit 0 on pass, 1 on fail.
"""

from __future__ import annotations
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent


def _load_module():
    spec = importlib.util.spec_from_file_location("hc", REPO / "src" / "health-check.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


hc = _load_module()


def _init_git_repo(tmpdir: Path) -> None:
    """Initialize a small git repo with one file + one commit so reflog works."""
    subprocess.run(["git", "init", "-q", "--initial-branch=main"], cwd=tmpdir, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "config", "user.email", "t@t"], cwd=tmpdir, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmpdir, check=True)


def _commit_file(tmpdir: Path, path: str, content: bytes, msg: str) -> str:
    f = tmpdir / path
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(content)
    subprocess.run(["git", "add", path], cwd=tmpdir, check=True)
    subprocess.run(["git", "commit", "-q", "-m", msg], cwd=tmpdir, check=True)
    sha = subprocess.run(["git", "rev-parse", "HEAD"],
                         cwd=tmpdir, capture_output=True, text=True).stdout.strip()
    return sha


class StaleDetectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        _init_git_repo(self.repo)
        self._patch = patch.object(hc, "REPO_DIR", self.repo)
        self._patch.start()
        self.addCleanup(self._patch.stop)

    # (a) — mtime fast-path
    def test_untouched_file_returns_true(self):
        f = self.repo / "src" / "app.py"
        _commit_file(self.repo, "src/app.py", b"v1\n", "v1")
        # Process started AFTER file mtime → file untouched since.
        proc_start = f.stat().st_mtime + 100
        self.assertTrue(hc._file_unchanged_since(f, proc_start))

    # (b) — mtime bumped but content identical (git checkout no-op simulation)
    def test_mtime_bumped_content_identical_returns_true(self):
        f = self.repo / "src" / "app.py"
        _commit_file(self.repo, "src/app.py", b"v1\n", "v1")
        proc_start = f.stat().st_mtime + 0.1  # process started just after commit
        # Simulate `git checkout` mtime bump: rewrite file with SAME content
        import time
        time.sleep(1.5)  # > 1s so git's integer-second timestamps differ
        f.write_bytes(b"v1\n")  # identical content, new mtime
        self.assertGreater(f.stat().st_mtime, proc_start)
        self.assertTrue(hc._file_unchanged_since(f, proc_start))

    # (c) — content actually changed (the original bug)
    def test_mtime_bumped_content_changed_returns_false(self):
        f = self.repo / "src" / "app.py"
        sha1 = _commit_file(self.repo, "src/app.py", b"v1\n", "v1")
        # Process started while HEAD was sha1 (v1 content)
        proc_start = f.stat().st_mtime + 0.1
        import time
        time.sleep(1.5)  # > 1s so git's integer-second timestamps differ
        # Later: a new version landed (mimics upstream merge bringing new content)
        _commit_file(self.repo, "src/app.py", b"v2 with more lines\n", "v2")
        # File mtime is now > proc_start, content differs from HEAD-at-proc_start
        self.assertGreater(f.stat().st_mtime, proc_start)
        self.assertFalse(hc._file_unchanged_since(f, proc_start))

    # (d) — reflog has no entry before proc_start → fail safe
    def test_empty_reflog_before_proc_start_returns_false(self):
        f = self.repo / "src" / "app.py"
        _commit_file(self.repo, "src/app.py", b"v1\n", "v1")
        # mtime > proc_start to trigger the slow path
        import time
        time.sleep(1.5)  # > 1s so git's integer-second timestamps differ
        f.write_bytes(b"v2\n")
        # proc_start way in the past, before any reflog entry exists
        proc_start = 1.0  # epoch + 1 second
        self.assertFalse(hc._file_unchanged_since(f, proc_start))

    # (e) — git show errors (file didn't exist at old_head) → False
    def test_file_didnt_exist_at_old_head_returns_false(self):
        # Commit something else first so reflog has a HEAD entry
        _commit_file(self.repo, "src/other.py", b"x\n", "other")
        # Capture pre-existence proc_start
        proc_start = (self.repo / "src" / "other.py").stat().st_mtime + 0.1
        import time
        time.sleep(1.5)  # > 1s so git's integer-second timestamps differ
        # Now add a NEW file (didn't exist at old_head)
        f = self.repo / "src" / "newfile.py"
        _commit_file(self.repo, "src/newfile.py", b"new\n", "new")
        # mtime > proc_start, but the file didn't exist at old_head → git show errors
        self.assertFalse(hc._file_unchanged_since(f, proc_start))

    # (f) — subprocess timeout / OSError → False (defensive)
    def test_oserror_returns_false(self):
        # Point at a path that doesn't exist → OSError on stat
        bogus = Path("/nonexistent/totally/bogus")
        self.assertFalse(hc._file_unchanged_since(bogus, 1.0))


if __name__ == "__main__":
    runner = unittest.TextTestRunner(verbosity=2)
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(StaleDetectTests)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
