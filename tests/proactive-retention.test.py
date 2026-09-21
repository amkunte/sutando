#!/usr/bin/env python3
"""Contract for src/proactive_retention.py, plus each bridge's delegation to it.

An undeliverable proactive result used to be unlinked by all three bridges, which
destroyed morning briefings and insights outright. The contract half pins the
behaviour; the delegation half pins that no adapter has gone back to `unlink` in
its no-owner branch — the copies are what drifted last time.
"""
from __future__ import annotations

import ast
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Plain import, not spec_from_file_location: the module name has no hyphen, so it
# needs no exec-loading. That also keeps this file out of
# scripts/lint-hermetic-bridge-tests.py's scope — it flags any test that calls
# exec_module AND mentions a bridge path in a code string, and cannot tell that the
# bridge paths below are only read as TEXT for the AST delegation check.
sys.path.insert(0, str(REPO / "src"))
import proactive_retention as pr  # noqa: E402


def _claim(d: Path, name: str, body: str = "briefing body") -> Path:
    p = d / name
    p.write_text(body)
    return p


with tempfile.TemporaryDirectory() as tmp:
    results = Path(tmp)
    retain = pr.retain_dir_for(results)
    assert retain == results / "undelivered", retain

    # The claim is moved, not copied, and the .sending marker is dropped.
    c = _claim(results, "proactive-morning-123.sending", "the briefing")
    dest = pr.retain_undeliverable(c, retain)
    assert dest is not None and dest.exists(), dest
    assert dest.name == "proactive-morning-123.txt", dest.name
    assert dest.read_text() == "the briefing"
    assert not c.exists(), "claim left behind — it would be re-claimed every poll"

    # Retained files must be OUTSIDE the bridges' results/*.txt poll glob, or the
    # bridge re-claims them every 2s instead of leaving them alone.
    assert dest.parent != results
    assert not list(results.glob("*.txt")), list(results.glob("*.txt"))

    # A second claim with the same name must not clobber the first.
    c2 = _claim(results, "proactive-morning-123.sending", "second briefing")
    dest2 = pr.retain_undeliverable(c2, retain)
    assert dest2 is not None and dest2 != dest, (dest, dest2)
    assert dest.read_text() == "the briefing", "first retained file was overwritten"
    assert dest2.read_text() == "second briefing"

    # A vanished claim reports failure rather than raising — the caller must not
    # then fall back to deleting anything.
    assert pr.retain_undeliverable(results / "gone.sending", retain) is None

    # A name without the .sending marker is still retained, under its own name.
    c3 = _claim(results, "insight-2026-08-17.txt", "insight")
    dest3 = pr.retain_undeliverable(c3, retain)
    assert dest3 is not None and dest3.name == "insight-2026-08-17.txt", dest3

print("  ok: retention contract")

# --- delegation: no adapter may unlink in its no-owner branch -----------------
# AST, not a text window. A proximity scan cannot work here: slack-bridge's
# LEGITIMATE success-path `claim.unlink()` sits directly above its no-owner log
# line, so every window wide enough to catch a real regression also flagged that.
def _no_owner_branches(src: str):
    """The statement lists that run when owner_id could not be resolved."""
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.If) or not isinstance(node.test, ast.Compare):
            continue
        t = node.test
        if not (isinstance(t.left, ast.Name) and t.left.id == "owner_id"):
            continue
        if len(t.ops) != 1 or not isinstance(t.comparators[0], ast.Constant):
            continue
        if t.comparators[0].value is not None:
            continue
        if isinstance(t.ops[0], ast.Is):
            yield node.body
        elif isinstance(t.ops[0], ast.IsNot):
            yield node.orelse


def _unlinks(stmts) -> bool:
    return any(isinstance(n, ast.Attribute) and n.attr == "unlink"
               for s in stmts for n in ast.walk(s))


ADAPTERS = ["src/telegram-bridge.py", "src/discord-bridge.py", "src/slack-bridge.py"]
for rel in ADAPTERS:
    src = (REPO / rel).read_text()
    assert "retain_undeliverable" in src, f"{rel} does not delegate to proactive_retention"
    branches = list(_no_owner_branches(src))
    assert branches, f"{rel}: found no `owner_id is (not) None` branch — did the shape change?"
    for b in branches:
        assert not _unlinks(b), f"{rel}: unlink() is back in a no-owner branch"

# The guard must be able to FAIL. A first draft of this check used a forward-only
# text window and silently passed against a deliberately reverted adapter, which is
# indistinguishable from a clean tree.
_tg = (REPO / "src" / "telegram-bridge.py").read_text()
_reverted = _tg.replace(
    "kept = retain_undeliverable(f, retain_dir_for(RESULTS_DIR))",
    "f.unlink(missing_ok=True)\n                            kept = None", 1)
assert _reverted != _tg, "self-check anchor not found — update it with the adapter"
assert any(_unlinks(b) for b in _no_owner_branches(_reverted)), \
    "the delegation guard does not detect a reverted adapter — it is decorative"

print("  ok: telegram / discord / slack all delegate, none unlink on no-owner")
print("  ok: guard verified against a deliberately reverted adapter")
print("ok: proactive retention contract + adapter delegation")
