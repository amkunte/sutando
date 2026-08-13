#!/usr/bin/env python3
"""Lint: every test file must actually RUN its assertions under `python3 <file>`.

Why this exists (2026-08-13). `tests/check-pending-questions-parse.test.py`
was written in pytest style — four `test_*(tmp_path)` functions — and nothing
invoked them. CI runs every test as:

    output=$(python3 "$f"); rc=$?          # .github/workflows/ci.yml

so the file exited 0 having executed ZERO assertions, and reported green in CI
for as long as it existed. pytest is not installed in CI (it installs only
detect-secrets and discord.py), so the fixture form ran nowhere at all.

A prose rule would not have caught the next one. This lint does: it fails the
build if a test file DEFINES `test_*` functions but has no mechanism that calls
them. The check is deliberately narrow — it does not care about style, only
about whether the assertions can execute.

Accepted invocation mechanisms:
  * `unittest.main()`
  * an `if __name__ == "__main__":` block
  * any module-level call expression (the repo's dominant plain style: the
    assertions run at import, via `ok(...)` helpers or bare asserts)

Run: python3 tests/lint-plain-style-tests.test.py
Exit: 0 on pass, 1 on fail.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SEARCH_ROOTS = [REPO / "tests", REPO / "skills"]

_offenders: list[tuple[str, int]] = []
_scanned = 0


def _defines_but_never_runs(path: Path) -> int | None:
    """Return the count of uninvokable test_* fns, or None if the file is fine."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError, OSError):
        return None  # unparseable is a different problem; not this lint's job

    test_fns = [
        n.name for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")
    ]
    if not test_fns:
        return None  # plain style — assertions run at import

    src = path.read_text(encoding="utf-8")
    has_main_block = any(
        isinstance(n, ast.If)
        and isinstance(n.test, ast.Compare)
        and ast.unparse(n.test).startswith("__name__ ==")
        for n in tree.body
    )
    has_module_level_call = any(
        isinstance(n, ast.Expr) and isinstance(n.value, ast.Call) for n in tree.body
    )
    if has_main_block or has_module_level_call or "unittest.main" in src:
        return None
    return len(test_fns)


for root in SEARCH_ROOTS:
    if not root.is_dir():
        continue
    for f in sorted(root.rglob("*.test.py")):
        if "node_modules" in f.parts:
            continue
        _scanned += 1
        n = _defines_but_never_runs(f)
        if n is not None:
            _offenders.append((str(f.relative_to(REPO)), n))

if _offenders:
    print(f"  FAIL: {len(_offenders)} of {_scanned} test file(s) define test_* "
          f"functions that NOTHING invokes — they exit 0 without asserting:")
    for rel, n in _offenders:
        print(f"    {rel}  ({n} test fn(s) never called)")
    print("\n  Fix: convert to the repo's plain style — run the assertions at")
    print("  module level (see tests/check-pending-questions-parse.test.py), or")
    print("  add an `if __name__ == \"__main__\":` runner. Do NOT rely on pytest:")
    print("  CI invokes `python3 <file>` and does not install it.")

print(f"lint-plain-style-tests: {_scanned - len(_offenders)}/{_scanned} test files run their assertions")
sys.exit(1 if _offenders else 0)
