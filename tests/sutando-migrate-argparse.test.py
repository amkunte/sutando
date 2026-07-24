#!/usr/bin/env python3
"""Regression tests for sutando-migrate.sh argument aliases."""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
MIGRATE = ROOT / "scripts" / "sutando-migrate.sh"


class TestSutandoMigrateArgparse(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="sutando-migrate-args-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_dash_dash_commit_alias_enters_commit_mode(self) -> None:
        """startup.sh and operator docs use --commit; keep it accepted."""
        dest = self.tmp / "dest"
        source = self.tmp / "source-a"
        (source / "notes").mkdir(parents=True)
        (source / "notes" / "argparse.md").write_text("alias smoke\n", encoding="utf-8")
        env = os.environ.copy()
        env.update(
            {
                "SUTANDO_MIGRATE_DEST": str(dest),
                "SUTANDO_MIGRATE_SRC_A": str(source),
                "SUTANDO_MIGRATE_SRC_B": str(self.tmp / "missing-b"),
                "SUTANDO_MIGRATE_SRC_C": str(self.tmp / "missing-c"),
            }
        )
        env.pop("SUTANDO_WORKSPACE", None)

        # --no-claude-import is MANDATORY here, not optional tidiness. A commit
        # run auto-invokes sutando-shell-setup.sh --import, which rsyncs the
        # REAL ~/.claude/projects/<slug>/ over the REAL workspace claude-home.
        # SUTANDO_MIGRATE_DEST/SRC_* do not scope that step, so this test — which
        # looks fully isolated — silently reverted the owner's live memory dir to
        # a frozen pre-migration snapshot on EVERY suite run (rsync -a, so even
        # the mtimes came back as Jul-13). Observed 3x on 2026-07-23 and
        # reproduced by bisect. sutando-migrate.sh's own docs name this flag as
        # the opt-out "for tests"; this one just never passed it.
        proc = subprocess.run(
            ["bash", str(MIGRATE), "--commit", "--source", "A", "--no-claude-import"],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

        combined = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 0, combined)
        self.assertIn("sutando-migrate: COMMIT mode", combined)
        self.assertNotIn("unknown arg: --commit", combined)
        self.assertTrue((dest / "notes" / "argparse.md").is_file())


if __name__ == "__main__":
    unittest.main()
