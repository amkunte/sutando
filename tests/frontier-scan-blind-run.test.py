#!/usr/bin/env python3
"""A frontier-scan run where every source failed must not advance last_scan.

Advancing it there tells the scan-catchup backstop the scan is done for its
whole cadence, so a blind run buys 24h of silence and the delta is only
recovered by luck.
"""
from __future__ import annotations

import importlib.util
import json
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "skills" / "frontier-scan" / "scripts" / "fetch_sources.py"


def _load():
    spec = importlib.util.spec_from_file_location("fetch_sources", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run(fetch_result, tmp):
    mod = _load()
    sources = tmp / "sources.json"
    sources.write_text(json.dumps({"sources": [
        {"name": "A", "kind": "github", "repo": "o/a"},
        {"name": "B", "kind": "github", "repo": "o/b"},
    ]}))
    state = tmp / "state" / "seen.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps({"seen": {}, "last_scan": "2000-01-01T00:00:00Z",
                                 "scan_history": []}))
    mod.SOURCES = sources
    mod.STATE = state
    mod._fetch_github = lambda repo: fetch_result
    mod.main()
    return json.loads(state.read_text())


with tempfile.TemporaryDirectory() as d:
    after = _run(([], "releases HTTP 504"), Path(d))
    assert after["last_scan"] == "2000-01-01T00:00:00Z", \
        "all-sources-failed run advanced last_scan: %r" % after["last_scan"]
    assert after["scan_history"][-1]["blind"] is True

with tempfile.TemporaryDirectory() as d:
    item = {"key": "o/a@v1", "title": "v1", "url": "u"}
    after = _run(([item], None), Path(d))
    assert after["last_scan"] != "2000-01-01T00:00:00Z", \
        "successful run failed to advance last_scan"
    assert after["scan_history"][-1]["blind"] is False

print("ok: frontier-scan blind runs hold last_scan back, successful runs advance it")
