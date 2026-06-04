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

    # --- unknown → default FAIL-CLOSED (confirm), not allow (B1) ---
    v = classify(command="echo hello world", tier="owner")
    check("unrecognized command → confirm (fail-closed default)", v["decision"] == "confirm")

    # --- unknown/garbage tier coerces to least-privileged, not owner (B3) ---
    v = classify(kind="email_send", tier="attacker")
    check("garbage tier → least-privileged (deny), not owner", v["decision"] == "deny")
    v = classify(kind="email_send", tier="")
    check("empty tier → least-privileged (deny)", v["decision"] == "deny")

    # --- broadened patterns catch the red-team false-negatives (B2) ---
    for cmd, want_kind in [
        ("rm important.txt", "file_delete"),
        ("find /x -delete", "file_delete"),
        ("git clean -fdx", "file_delete"),
        ("env | grep TOKEN", "credential_exfil"),
        ("base64 ~/.claude/channels/discord/.env", "credential_exfil"),
        ("venmo pay $500", "financial"),
        ("mcp__claude_ai_Gmail__send_message", "email_send"),
    ]:
        v = classify(command=cmd, tier="owner")
        check(f"pattern catches: {cmd!r} → {want_kind}", v["kind"] == want_kind and v["decision"] == "confirm")

    # --- id has randomness: rapid calls do not collide (B6) ---
    ids = {new_id() for _ in range(50)}
    check("new_id unique across 50 rapid calls (has randomness)", len(ids) == 50)

    # --- G4: kind + command → MOST RESTRICTIVE wins (no short-circuit bypass) ---
    # email_send is confirm for owner; pair it with a deny-only-via-tier command.
    v = classify(kind="email_send", command="git push upstream main", tier="team")
    check("most-restrictive across kind+command (team) → deny", v["decision"] == "deny")
    v = classify(kind="email_send", command="rm -rf /important", tier="owner")
    check("kind+command both confirm (owner) → confirm", v["decision"] == "confirm")

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

    import subprocess

    # G1: non-owner approver-tier cannot approve (exit 5), and the record stays pending
    rdeny = subprocess.run([sys.executable, str(SKILL / "scripts" / "resolve_approval.py"),
                            "testid01", "approve", "--approver-tier", "team"],
                           capture_output=True, text=True, env=os.environ)
    check("non-owner approver-tier → approve rejected (exit 5)", rdeny.returncode == 5)
    still = json.loads((approvals_dir() / "testid01.json").read_text())
    check("rejected approve left record pending", still["status"] == "pending")

    # owner approver-tier approves
    r = subprocess.run([sys.executable, str(SKILL / "scripts" / "resolve_approval.py"),
                        "testid01", "approve", "--note", "ok", "--approver-tier", "owner"],
                       capture_output=True, text=True, env=os.environ)
    out = json.loads(r.stdout.strip().splitlines()[-1])
    check("owner approve → status approved", out["status"] == "approved")

    # idempotent: second approve does not flip / re-decide
    r2 = subprocess.run([sys.executable, str(SKILL / "scripts" / "resolve_approval.py"),
                         "testid01", "deny"], capture_output=True, text=True, env=os.environ)
    out2 = json.loads(r2.stdout.strip().splitlines()[-1])
    check("idempotent: already-decided not flipped", out2.get("status") == "approved")

    # unknown (but valid-shape) id → exit 3 (deny needs no owner tier, so it
    # reaches the existence check rather than the authority check)
    r3 = subprocess.run([sys.executable, str(SKILL / "scripts" / "resolve_approval.py"),
                         "nope123", "deny"], capture_output=True, text=True, env=os.environ)
    check("unknown id → exit 3", r3.returncode == 3)

    # path-traversal id → exit 4, and the traversal target is NOT written (B7)
    sentinel = Path(tmp) / "state" / "secret.json"
    r4 = subprocess.run([sys.executable, str(SKILL / "scripts" / "resolve_approval.py"),
                         "../secret", "approve"], capture_output=True, text=True, env=os.environ)
    check("traversal id '../secret' → exit 4 (rejected)", r4.returncode == 4)
    check("traversal did NOT write outside approvals dir", not sentinel.exists())

    # real request_approval.py creates a unique file with O_EXCL
    rr = subprocess.run([sys.executable, str(SKILL / "scripts" / "request_approval.py"),
                         "--kind", "email_send", "--summary", "t", "--command", "x"],
                        capture_output=True, text=True, env=os.environ)
    rid = rr.stdout.strip()
    check("request_approval prints clean id to stdout (no warn leak, N1)",
          rid.isalnum() and "WARN" not in rr.stdout)

    # check_policy.py exit codes
    rc = subprocess.run([sys.executable, str(SKILL / "scripts" / "check_policy.py"),
                         "--kind", "email_send"], capture_output=True, text=True, env=os.environ)
    check("check_policy email_send → exit 10 (confirm)", rc.returncode == 10)
    rc = subprocess.run([sys.executable, str(SKILL / "scripts" / "check_policy.py"),
                         "--command", "echo hi"], capture_output=True, text=True, env=os.environ)
    check("check_policy unrecognized → exit 10 (confirm, fail-closed)", rc.returncode == 10)

    print()
    if _fails:
        print(f"{len(_fails)} FAILED: {_fails}")
        return 1
    print("all tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
