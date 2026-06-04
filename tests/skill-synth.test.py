"""Tests for skill-synth scan + synthesize. Runnable without pytest:
  python3 tests/skill-synth.test.py
(also discoverable by pytest if available)."""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "skills" / "skill-synth" / "scripts"


def _load(modname):
    spec = importlib.util.spec_from_file_location(modname, SCRIPTS / (modname + ".py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _make_ws(tmp: Path):
    (tmp / "tasks").mkdir(parents=True, exist_ok=True)
    (tmp / "results").mkdir(parents=True, exist_ok=True)
    # a strong skill-worthy task: procedural verb + substantive success result, x2 (recurring)
    for i, tid in enumerate(("1000000000001", "1000000000002")):
        (tmp / "tasks" / f"task-{tid}.txt").write_text(
            f"id: {tid}\nsource: discord\naccess_tier: owner\n"
            "task: Scan the foobar feed and generate a digest of new widgets since last run\n")
        (tmp / "results" / f"task-{tid}.txt").write_text(
            "Foobar digest generated: 3 new widgets found, summarized and posted. "
            * 6)  # >200 chars, no error markers
    # a non-candidate: failed result
    (tmp / "tasks" / "task-1000000000003.txt").write_text(
        "id: 1000000000003\nsource: discord\ntask: deploy the thing to prod\n")
    (tmp / "results" / "task-1000000000003.txt").write_text("❌ Error: deploy failed, traceback ...")
    # a non-candidate: trivial greeting
    (tmp / "tasks" / "task-1000000000004.txt").write_text(
        "id: 1000000000004\nsource: discord\ntask: hi\n")
    (tmp / "results" / "task-1000000000004.txt").write_text("hello!")


def run():
    ok = True
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        _make_ws(tmp)
        os.environ["SUTANDO_WORKSPACE"] = str(tmp)

        scan = _load("scan")
        common = _load("common")

        # --- scan picks the procedural recurring task, drops fail + trivial ---
        ws = common.resolve_workspace()
        tasks = common.iter_tasks(ws)
        ids = {t["id"] for t in tasks}
        assert ids >= {"1000000000001", "1000000000002", "1000000000003", "1000000000004"}, ids

        # invoke scan via subprocess (JSON) to test the real entrypoint
        out = subprocess.run(
            [sys.executable, str(SCRIPTS / "scan.py"), "--min-score", "3"],
            capture_output=True, text=True, env={**os.environ})
        import json
        cands = json.loads(out.stdout or "[]")
        cand_ids = {c["task_id"] for c in cands}
        for name, cond in [
            ("foobar-digest is a candidate", "1000000000001" in cand_ids),
            ("failed-deploy is NOT a candidate", "1000000000003" not in cand_ids),
            ("trivial 'hi' is NOT a candidate", "1000000000004" not in cand_ids),
            ("recurrence reason present", any("recurring" in ";".join(c["reasons"]) for c in cands)),
        ]:
            print(("PASS" if cond else "FAIL") + "  " + name); ok = ok and cond

        # --- synthesize bundles a candidate dir (NOT installed) ---
        syn = subprocess.run(
            [sys.executable, str(SCRIPTS / "synthesize.py"),
             "--from-task", "1000000000001", "--name", "foobar-digest-test"],
            capture_output=True, text=True, env={**os.environ})
        cdir = SCRIPTS.parent / "candidates" / "foobar-digest-test"
        try:
            checks = [
                ("synthesize exit 0", syn.returncode == 0),
                ("candidate SKILL.md created", (cdir / "SKILL.md").exists()),
                ("candidate brief.md created", (cdir / "brief.md").exists()),
                ("brief carries source task text", "foobar feed" in (cdir / "brief.md").read_text()),
                ("SKILL.md has agent TODO", "TODO(agent)" in (cdir / "SKILL.md").read_text()),
                ("nothing installed to ~/.claude/skills", not (Path.home() / ".claude" / "skills" / "foobar-digest-test").exists()),
            ]
            for name, cond in checks:
                print(("PASS" if cond else "FAIL") + "  " + name); ok = ok and cond
        finally:
            # cleanup the test candidate
            import shutil
            shutil.rmtree(cdir, ignore_errors=True)

    print("ALL PASS" if ok else "SOME FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(run())
