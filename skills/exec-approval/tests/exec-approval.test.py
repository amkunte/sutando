#!/usr/bin/env python3
"""Tests for the exec-approval skill. pytest-free runner — no external deps.

Run: python3 skills/exec-approval/tests/exec-approval.test.py
Uses a temp workspace via SUTANDO_WORKSPACE so it never touches real state,
and never posts to Discord (no 'approvals' channel in the temp config).
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

# Make the skill's scripts importable
SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / "scripts"))

_fails = []


def check(name, cond):
    print(("PASS" if cond else "FAIL") + f"  {name}")
    if not cond:
        _fails.append(name)


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="exec-approval-test-")
    os.environ["SUTANDO_WORKSPACE"] = tmp
    # empty discord-config → no channel → posts become no-ops (warn only)
    (Path(tmp) / "state").mkdir(parents=True, exist_ok=True)
    (Path(tmp) / "state" / "discord-config.json").write_text(json.dumps({"channels": {}}))

    import common, importlib
    importlib.reload(common)
    from common import classify, new_id, approvals_dir

    # --- classify by kind ---
    v = classify(kind="email_send", tier="owner")
    check("email_send/owner → confirm", v["decision"] == "confirm")
    v = classify(kind="email_send", tier="team")
    check("email_send/team → deny", v["decision"] == "deny")

    # --- classify by command pattern (no kind) ---
    v = classify(command="rm -rf /tmp/somedir", tier="owner")
    check("rm -rf → file_delete/confirm", v["decision"] == "confirm" and v["kind"] == "file_delete")
    v = classify(command="gh pr create --repo sonichi/sutando --title x", tier="owner")
    check("gh pr create sonichi → upstream_push/confirm",
          v["decision"] == "confirm" and v["kind"] == "upstream_push")

    # --- kind wins over command ---
    v = classify(kind="email_send", command="ls -la", tier="owner")
    check("explicit kind overrides command scan", v["kind"] == "email_send")

    # --- unknown → default allow ---
    v = classify(command="echo hello world", tier="owner")
    check("benign command → allow (default)", v["decision"] == "allow")

    # --- id is short + stable shape ---
    aid = new_id()
    check("new_id is <=8 chars, alnum", len(aid) <= 8 and aid.isalnum())

    # --- request → resolve round-trip (no Discord) ---
    import request_approval, resolve_approval
    # simulate request
    rec = {
        "id": "testid01", "status": "pending", "kind": "email_send",
        "summary": "test", "command": "x", "tier": "owner",
        "created_at": "2026-01-01T00:00:00Z", "decided_at": None,
    }
    p = approvals_dir() / "testid01.json"
    p.write_text(json.dumps(rec))

    # approve via the module's logic
    import subprocess
    r = subprocess.run([sys.executable, str(SKILL / "scripts" / "resolve_approval.py"),
                        "testid01", "approve", "--note", "ok"],
                       capture_output=True, text=True, env=os.environ)
    out = json.loads(r.stdout.strip().splitlines()[-1])
    check("resolve approve → status approved", out["status"] == "approved")

    # idempotent: second approve does not flip / re-decide
    r2 = subprocess.run([sys.executable, str(SKILL / "scripts" / "resolve_approval.py"),
                         "testid01", "deny"], capture_output=True, text=True, env=os.environ)
    out2 = json.loads(r2.stdout.strip().splitlines()[-1])
    check("idempotent: already-decided not flipped", out2.get("status") == "approved")

    # unknown id → exit 3
    r3 = subprocess.run([sys.executable, str(SKILL / "scripts" / "resolve_approval.py"),
                         "nope", "approve"], capture_output=True, text=True, env=os.environ)
    check("unknown id → exit 3", r3.returncode == 3)

    # check_policy.py exit codes
    rc = subprocess.run([sys.executable, str(SKILL / "scripts" / "check_policy.py"),
                         "--kind", "email_send"], capture_output=True, text=True, env=os.environ)
    check("check_policy email_send → exit 10 (confirm)", rc.returncode == 10)
    rc = subprocess.run([sys.executable, str(SKILL / "scripts" / "check_policy.py"),
                         "--command", "echo hi"], capture_output=True, text=True, env=os.environ)
    check("check_policy benign → exit 0 (allow)", rc.returncode == 0)

    print()
    if _fails:
        print(f"{len(_fails)} FAILED: {_fails}")
        return 1
    print("all tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
