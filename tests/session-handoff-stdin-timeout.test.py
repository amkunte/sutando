#!/usr/bin/env python3
"""
Regression test — session-handoff.sh hung forever on a non-tty stdin that never delivers.

Pre-fix (main branch), src/session-handoff.sh had:

    if [ -z "$TRANSCRIPT" ] && [ ! -t 0 ]; then
      TRANSCRIPT="$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("transcript_path") or "")' ...)"
    fi

`[ ! -t 0 ]` is true for ANY non-tty stdin — not just a Claude Code hook's JSON pipe. It is
equally true for an inherited-but-idle pipe (cron, nohup, a wrapper whose stdin nobody ever
writes to). In that case `json.load(sys.stdin)` blocks forever waiting for an EOF that never
arrives, so the entire handoff hangs instead of falling through to `--latest`.

Measured on the real script with an idle pipe as stdin:
    BEFORE (origin/main)   HUNG — still blocked after 75s
    AFTER  (this branch)   completed rc=0 in 3.4s

Post-fix: a short `select.select()` poll guards the read. Data ready → parse exactly as before;
nothing within the window → emit "" and let the caller fall through. `select` is used rather
than `timeout(1)` because stock macOS has no `timeout` binary.

This test extracts the python program actually embedded in src/session-handoff.sh (so it
exercises shipped code, not a copy) and runs it against three stdin shapes.

Run: python3 tests/session-handoff-stdin-timeout.test.py
Exit code: 0 on pass, 1 on fail.
"""

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "src" / "session-handoff.sh"

# Upper bound for the guarded read. The shipped default window is 2s; allow generous
# headroom for slow CI while still being far below the "hangs forever" failure.
MAX_WAIT = 30.0

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        print(f"PASS: {name}")
        PASS += 1
    else:
        print(f"FAIL: {name}" + (f" — {detail}" if detail else ""))
        FAIL += 1


def extract_stdin_program() -> str:
    """Pull the python program out of the stdin guard in session-handoff.sh."""
    src = SCRIPT.read_text()
    m = re.search(r"TRANSCRIPT=\"\$\((?:\w+=\S+\s+)?python3 -c '(.*?)'\s*2>/dev/null", src, re.S)
    if not m:
        raise AssertionError("could not locate the stdin-parsing python block in session-handoff.sh")
    return m.group(1)


def run(program: str, mode: str, timeout: float = MAX_WAIT):
    """Return (stdout, elapsed) or (None, elapsed) if it timed out."""
    env = dict(os.environ, SH_STDIN_WAIT="2")
    t0 = time.time()
    if mode == "idle_pipe":
        r, w = os.pipe()  # writer held open, never written to, never closed
        try:
            p = subprocess.run([sys.executable, "-c", program], stdin=r,
                               capture_output=True, text=True, timeout=timeout, env=env)
            return p.stdout.strip(), time.time() - t0
        except subprocess.TimeoutExpired:
            return None, time.time() - t0
        finally:
            os.close(w)
            os.close(r)
    if mode == "hook_json":
        payload = json.dumps({"transcript_path": "/tmp/fake-transcript.jsonl"})
        try:
            p = subprocess.run([sys.executable, "-c", program], input=payload,
                               capture_output=True, text=True, timeout=timeout, env=env)
            return p.stdout.strip(), time.time() - t0
        except subprocess.TimeoutExpired:
            return None, time.time() - t0
    if mode == "devnull":
        with open(os.devnull) as f:
            p = subprocess.run([sys.executable, "-c", program], stdin=f,
                               capture_output=True, text=True, timeout=timeout, env=env)
            return p.stdout.strip(), time.time() - t0
    raise ValueError(mode)


def main() -> int:
    program = extract_stdin_program()

    out, elapsed = run(program, "idle_pipe")
    check(
        "idle non-tty stdin returns instead of blocking (FAILS on main — hangs forever)",
        out is not None,
        f"still blocked after {elapsed:.1f}s",
    )
    check(
        "idle stdin yields an empty transcript path so the caller falls through to --latest",
        out == "",
        f"got {out!r}",
    )

    out, elapsed = run(program, "hook_json")
    check(
        "hook JSON on stdin still parses transcript_path (regression guard)",
        out == "/tmp/fake-transcript.jsonl",
        f"got {out!r}",
    )
    check(
        "hook path is not delayed by the guard",
        elapsed < 2.0,
        f"took {elapsed:.1f}s — guard should return as soon as data is ready",
    )

    out, _ = run(program, "devnull")
    check("stdin from /dev/null yields empty, as before", out == "", f"got {out!r}")

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
