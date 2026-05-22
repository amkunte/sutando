#!/usr/bin/env python3
"""
Tests for src/sms-bridge.py workspace-path contract, added 2026-05-19.

Background: sms-bridge had `WORKSPACE = Path(__file__).resolve().parents[1]`
through its entire 5-day lifetime, resolving to the repo dir instead of the
workspace dir. Consequence: every inbound twilio_sms task the phone-server
correctly wrote to `<workspace>/tasks/` was silently dropped because the
bridge polled `<repo>/tasks/` instead. Heartbeat went to the wrong dir too,
producing a persistent "heartbeat stale" health-check warn that never cleared
even after restart.

PR #30 fixed it by switching to `resolve_workspace()`. This test exists to
prevent regression — any future edit that reintroduces the repo-rooted
WORKSPACE will fail loud at test time rather than silently 5 days later.

These tests parse the source file directly rather than import the module —
sms-bridge.main() blocks on `while True:` polling at import time, same
constraint as the other bridge tests.

Run: python3 tests/sms-bridge-workspace.test.py
Exit: 0 on pass, 1 on fail.
"""

from __future__ import annotations
import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SMS_BRIDGE_PATH = ROOT / "src" / "sms-bridge.py"


class SmsBridgeWorkspaceContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = SMS_BRIDGE_PATH.read_text()
        cls.tree = ast.parse(cls.source)

    def test_no_path_file_parents_anti_pattern_for_workspace(self):
        # The historic anti-pattern: `WORKSPACE = Path(__file__).resolve().parents[1]`
        # resolves to the repo dir, not the workspace. It's fine for REPO_DIR
        # (for .env loading) but never for WORKSPACE. Scan the AST for any
        # assignment to a name containing "WORKSPACE" that uses parents[N].
        offenders = []
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if not isinstance(target, ast.Name):
                    continue
                if "WORKSPACE" not in target.id.upper():
                    continue
                # Stringify the RHS and check for the anti-pattern.
                rhs_src = ast.unparse(node.value)
                if "parents[" in rhs_src or "parent.parent" in rhs_src:
                    offenders.append(f"{target.id} = {rhs_src}")
        self.assertEqual(
            offenders, [],
            "sms-bridge.py has reintroduced the repo-rooted WORKSPACE "
            "anti-pattern (see PR #30 + memory/reference_workspace_mismatch_pattern.md). "
            f"Offending assignments: {offenders}"
        )

    def test_uses_resolve_workspace(self):
        # Positive assertion: the bridge must import and call resolve_workspace().
        # If a future edit removes the call, this test fails before runtime.
        self.assertIn(
            "from workspace_default import resolve_workspace",
            self.source,
            "sms-bridge.py no longer imports resolve_workspace — see "
            "src/workspace_default.py for the canonical pattern."
        )
        self.assertIn(
            "resolve_workspace()",
            self.source,
            "sms-bridge.py imports resolve_workspace but never calls it. "
            "WORKSPACE must be set via the function, not a literal path."
        )

    def test_env_path_resolves_to_repo_not_workspace(self):
        # .env is code-adjacent config, NOT runtime state, so it MUST be loaded
        # from REPO_DIR, never from WORKSPACE. This is the corollary of the
        # main contract: workspace ≠ repo.
        self.assertIn(
            "REPO_DIR / \".env\"",
            self.source,
            "sms-bridge.py loads .env from a path that isn't REPO_DIR. "
            ".env stays repo-rooted; only runtime state (tasks/results/state) "
            "goes through resolve_workspace()."
        )

    def test_tasks_results_state_under_workspace(self):
        # The three runtime dirs must all derive from WORKSPACE (which is in
        # turn resolve_workspace()). If anyone copy-pastes a `REPO_DIR /
        # "tasks"` line in the future, this test catches it.
        for name in ("TASKS_DIR", "RESULTS_DIR", "STATE_DIR"):
            # Find the assignment line and assert it derives from WORKSPACE.
            found = False
            for node in ast.walk(self.tree):
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name) and target.id == name:
                            rhs = ast.unparse(node.value)
                            self.assertIn(
                                "WORKSPACE", rhs,
                                f"{name} doesn't derive from WORKSPACE — "
                                f"got `{name} = {rhs}`. Runtime dirs must "
                                f"flow from resolve_workspace()."
                            )
                            found = True
                            break
                if found:
                    break
            self.assertTrue(found, f"{name} assignment not found in sms-bridge.py")


if __name__ == "__main__":
    unittest.main()
