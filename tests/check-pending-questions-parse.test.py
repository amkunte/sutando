#!/usr/bin/env python3
"""Regression: get_waiting_questions() must not phantom-count the canonical
empty template (`## Active` / `_(none)_` + footer) and must skip RESOLVED
headers, while still detecting genuine waiting questions. (PR: parser hardening)

Converted from pytest style to the repo's plain style 2026-08-13. The pytest
form defined four `test_*(tmp_path)` functions and nothing invoked them, so
`python3 <file>` — which is how CI runs every `tests/*.test.py`
(`.github/workflows/ci.yml`) — exited 0 having executed ZERO assertions. The
file had been reporting green while testing nothing. pytest is not installed
in CI, so the fixture form ran nowhere.

Run: python3 tests/check-pending-questions-parse.test.py
Exit: 0 on pass, 1 on fail.
"""
from __future__ import annotations

import importlib.util
import pathlib
import tempfile
import sys

_spec = importlib.util.spec_from_file_location(
    "cpq", str(pathlib.Path(__file__).resolve().parent.parent / "src" / "check-pending-questions.py")
)
cpq = importlib.util.module_from_spec(_spec)
try:
    _spec.loader.exec_module(cpq)
except SystemExit:
    pass

_passed = 0
_failed = 0


def ok(name: str, cond: bool, detail: str = "") -> None:
    global _passed, _failed
    if cond:
        _passed += 1
    else:
        _failed += 1
        print(f"  FAIL: {name}" + (f" — {detail}" if detail else ""))


def _count(content: str) -> list:
    """Titles of waiting questions parsed from `content`.

    Redirects RESULTS_DIR at the temp dir too — the pytest original set only
    PQ_FILE, so it read the developer's real results/ directory.
    """
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        f = tmp / "pending-questions.md"
        f.write_text(content)
        cpq.PQ_FILE = f
        cpq.RESULTS_DIR = tmp
        return [q["title"] for q in cpq.get_waiting_questions()]


# 1. The canonical empty template must not phantom-count.
got = _count(
    "# Pending Questions\n\n## Active\n\n_(none)_\n\n---\n\n"
    "Resolved questions are not archived here — see git history.\n"
)
ok("canonical empty template is zero", got == [], f"got: {got}")

# 2. A struck-through RESOLVED header is skipped.
got = _count("# PQ\n## ~~Verify PNR 8OE3AL~~ — RESOLVED 2026-06-03\nwas a past trip\n")
ok("resolved struck header skipped", got == [], f"got: {got}")

# 3. A genuine free-form question is detected.
got = _count("# PQ\n## Should I buy the PC12?\nNeed your call on financing.\n")
ok("real freeform question detected", got == ["Should I buy the PC12?"], f"got: {got}")

# 4. **Status:** unanswered → waiting; resolved → skipped.
got = _count("# PQ\n## Q1 — Deploy?\n- **Status:** unanswered\nbody\n")
ok("status waiting detected", got == ["Q1 — Deploy?"], f"got: {got}")
got = _count("# PQ\n## Q2 — Old\n- **Status:** resolved\nbody\n")
ok("status resolved skipped", got == [], f"got: {got}")

print(f"check-pending-questions-parse: {_passed}/{_passed + _failed} passed")
sys.exit(1 if _failed else 0)
