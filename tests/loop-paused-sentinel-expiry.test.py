#!/usr/bin/env python3
"""_loop_deliberately_paused must read each sentinel's ISO expiry, not mtime.

Both sentinels carry their expiry as file CONTENT:
  - scripts/presenter-mode.sh writes an ISO timestamp; its header requires
    readers to "handle a stale sentinel (ignore if expired)".
  - Sutando.app writes loop-paused-until.sentinel the same way
    (src/Sutando/main.swift: "Sentinel format: ISO-8601 expiry timestamp").

Before the fix these failed in OPPOSITE directions: presenter used bare
.exists() (an expired sentinel suppressed the idle warning forever), and
loop-paused compared st_mtime > now — mtime is write time, so it is always
in the past and that branch could never return True at all.

Plain style per PERSONAL_CLAUDE.md: assertions run under `python3 <file>`.
"""
import importlib.util
import pathlib
import sys
import tempfile
import time

REPO = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("hc", REPO / "src" / "health-check.py")
hc = importlib.util.module_from_spec(spec)
sys.modules["hc"] = hc
spec.loader.exec_module(hc)

paused = hc._loop_deliberately_paused


def iso(offset_sec):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + offset_sec))


def ws_with(name, content):
    d = pathlib.Path(tempfile.mkdtemp())
    (d / "state").mkdir()
    if name:
        (d / "state" / name).write_text(content)
    return d


# --- nothing set: not paused -------------------------------------------------
assert paused(ws_with(None, "")) is False, "empty workspace must not read as paused"

# --- presenter mode ----------------------------------------------------------
assert paused(ws_with("presenter-mode.sentinel", iso(1800) + "\n")) is True, \
    "a live presenter sentinel must read as paused"
assert paused(ws_with("presenter-mode.sentinel", iso(-3600) + "\n")) is False, \
    "an EXPIRED presenter sentinel must be ignored (writer's documented contract)"

# --- explicit loop pause (Sutando.app) ---------------------------------------
assert paused(ws_with("loop-paused-until.sentinel", iso(1800) + "\n")) is True, \
    "a live 30-min pause must read as paused (mtime comparison could never do this)"
assert paused(ws_with("loop-paused-until.sentinel", iso(-60) + "\n")) is False, \
    "a lapsed pause must auto-expire back to unpaused"
assert paused(ws_with("loop-paused-until.sentinel", "2099-01-01T00:00:00Z\n")) is True, \
    "the app's 'Indefinite' option writes a year-2099 expiry and must hold"

# --- malformed content must fail CLOSED, never active-forever ----------------
for junk in ("garbage", "", "   ", "not-a-date"):
    assert paused(ws_with("presenter-mode.sentinel", junk)) is False, \
        f"malformed sentinel {junk!r} must not read as paused ('g' > '2' in ASCII)"

print("loop-paused-sentinel-expiry: 10 assertions passed")
