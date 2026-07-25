#!/usr/bin/env python3
"""Regression test for mark_stale_if_outdated's compiled-artifact branch.

The branch compares `src_mtime - bin_mtime > threshold_sec` and flags
"rebuild needed". It used to early-return on that mtime comparison alone,
making it the ONLY mtime comparison in health-check.py without a content
cross-check -- the proc_start path (`_file_unchanged_since` at the bottom of
the same function) and the bridges path both have one.

Consequence: any operation that re-stamps the source without changing its
content -- `git checkout`, pull, rebase, stash pop -- permanently flags
"rebuild needed". Observed on Maverick 2026-07-25: the daily 08:07
upstream-sync re-stamped src/Sutando/main.swift (content unchanged since
07-18), src-minus-binary went -10 min -> +20 h, and sutando-app reported
"rebuild needed" every single day.

These tests pin both directions: an mtime-only bump must NOT flag, a real
content change MUST still flag.

Run: python3 tests/health-check-compiled-artifact-content-check.test.py
"""
import importlib.util
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

spec = importlib.util.spec_from_file_location("hc", REPO / "src" / "health-check.py")
hc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hc)

failures = []


def check(cond, label):
    if cond:
        print("  ok  %s" % label)
    else:
        print("FAIL: %s" % label, file=sys.stderr)
        failures.append(label)


def git(repo, *args, when=None):
    env = dict(os.environ)
    if when is not None:
        # Reflog entries take their timestamp from the committer date, so
        # backdating the commit backdates the reflog entry too. Needed because
        # _file_unchanged_since looks for a reflog entry at-or-before the
        # binary's build time -- and in reality the build always follows the
        # commit. Without this the scratch repo inverts that ordering.
        stamp = "%d +0000" % int(when)
        env["GIT_AUTHOR_DATE"] = stamp
        env["GIT_COMMITTER_DATE"] = stamp
    return subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=test", *args],
        cwd=repo, capture_output=True, text=True, env=env,
    )


def make_repo(tmp):
    """A git repo with a source file committed 2h ago and a binary built 1h ago.

    Ordering matters: commit -> build -> (later) mtime bump, which is the real
    sequence on a deployed host.
    """
    now = time.time()
    repo = Path(tmp) / "repo"
    (repo / "src").mkdir(parents=True)
    src = repo / "src" / "thing.swift"
    src.write_text("let original = 1\n")
    git(repo, "init", "-q", "-b", "main")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "c1", when=now - 7200)

    binary = repo / "src" / "thing-bin"
    binary.write_text("compiled")
    os.utime(src, (now - 7200, now - 7200))
    os.utime(binary, (now - 3600, now - 3600))
    return repo, src, binary


def run_case(repo, src, binary):
    """Invoke the compiled-artifact branch with REPO_DIR pointed at the scratch repo."""
    chk = {"name": "thing", "status": "ok", "detail": "running"}
    saved = hc.REPO_DIR
    hc.REPO_DIR = repo
    try:
        hc.mark_stale_if_outdated(chk, src, "no-such-process-pattern", binary_path=binary)
    finally:
        hc.REPO_DIR = saved
    return chk


with tempfile.TemporaryDirectory() as tmp:
    # --- 1. mtime bumped, content IDENTICAL -> must NOT flag ------------------
    repo, src, binary = make_repo(tmp)
    content_before = src.read_bytes()
    # Simulate `git checkout` re-stamping the file: same bytes, new mtime,
    # far enough past the binary to clear the 30-minute threshold.
    src.write_bytes(content_before)
    os.utime(src, (time.time(), time.time()))
    assert src.read_bytes() == content_before, "test bug: content must be unchanged"
    assert src.stat().st_mtime - binary.stat().st_mtime > 1800, "test bug: threshold not cleared"

    chk = run_case(repo, src, binary)
    check(chk["status"] != "stale",
          "mtime-only bump does NOT flag rebuild (got status=%r detail=%r)"
          % (chk["status"], chk.get("detail")))

with tempfile.TemporaryDirectory() as tmp:
    # --- 2. content ACTUALLY changed -> must still flag -----------------------
    repo, src, binary = make_repo(tmp)
    src.write_text("let original = 1\nlet added = 2\n")
    os.utime(src, (time.time(), time.time()))
    assert src.stat().st_mtime - binary.stat().st_mtime > 1800

    chk = run_case(repo, src, binary)
    check(chk["status"] == "stale",
          "real content change STILL flags rebuild (got status=%r)" % chk["status"])
    check("rebuild needed" in (chk.get("detail") or ""),
          "real content change keeps the actionable 'rebuild needed' wording")

with tempfile.TemporaryDirectory() as tmp:
    # --- 3. source older than binary -> untouched (threshold not crossed) -----
    repo, src, binary = make_repo(tmp)
    chk = run_case(repo, src, binary)
    check(chk["status"] == "ok", "source older than binary stays ok")

with tempfile.TemporaryDirectory() as tmp:
    # --- 4. uncommitted content change -> still flags -------------------------
    # git show can't see it, so _file_unchanged_since returns False and the
    # flag stands. Fail-safe direction: never hide a real rebuild.
    repo, src, binary = make_repo(tmp)
    src.write_text("let original = 1\nlet uncommitted = 3\n")
    os.utime(src, (time.time(), time.time()))
    chk = run_case(repo, src, binary)
    check(chk["status"] == "stale", "uncommitted content change still flags (fail-safe)")

with tempfile.TemporaryDirectory() as tmp:
    # --- 5. no git history at all -> fails safe to flagging -------------------
    repo = Path(tmp) / "nogit"
    (repo / "src").mkdir(parents=True)
    src = repo / "src" / "thing.swift"
    src.write_text("x\n")
    binary = repo / "src" / "thing-bin"
    binary.write_text("b")
    os.utime(binary, (time.time() - 7200, time.time() - 7200))
    chk = run_case(repo, src, binary)
    check(chk["status"] == "stale", "no git history fails safe (flags rather than hides)")

print()
if failures:
    print("FAILED %d check(s)" % len(failures), file=sys.stderr)
    sys.exit(1)
print("OK — 6/6 compiled-artifact content-check tests passed")
