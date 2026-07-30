#!/usr/bin/env python3
"""Regression test — the roaming skip-gates were comment-blind.

`src/scan-catchup.py` and `src/scheduled-catchup.py` each decided "is this node
gated out?" with a raw substring test against `.env`:

    return "SKIP_SKILL_SCANS=1" in (REPO_DIR / ".env").read_text()

A substring test cannot see comment syntax, so **a commented-out flag still
gated the node**:

    GATES  'SKIP_SKILL_SCANS=1'
    GATES  '# SKIP_SKILL_SCANS=1'
    GATES  '#SKIP_SKILL_SCANS=1'
    GATES  '# disabled: SKIP_SKILL_SCANS=1 (was for travel)'
    no     'SKIP_SKILL_SCANS=0'      <- works only by accident: no "=1" substring

So the obvious way to resume scans when coming off the road — comment the line
out — silently did nothing, and the symptom (scans stay off) is indistinguishable
from "nothing to report". Same for `SKIP_SCHEDULED_DELIVERIES`, which gates the
morning briefing and the drips.

This is the **third** appearance of the class. `health-check.py`'s
`twilio_configured()` fixed it for `TWILIO_ACCOUNT_SID` on 2026-07-02 and its
docstring records the cost: the substring form matched the commented template
placeholder, so unconfigured hosts ran the phone checks and `startup.sh` "kept a
public ngrok tunnel open to a port with nothing behind it". `startup.sh:977` and
`:1094` carry the anchored-grep equivalent. The two `SKIP_*` reads were never
swept. (`health-check.py:2389` `SKIP_PHONE` and `:2448` `SKIP_SMS_BRIDGE` have the
same shape but live in an upstream file — separate PR, out of scope here.)

Every "must NOT gate" case below FAILS against the pre-fix substring form, which
is what makes this a regression test rather than a decoration.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"ok   {name}")
    else:
        FAILURES.append(name)
        print(f"FAIL {name}{(' — ' + detail) if detail else ''}")


def load(rel: str, mod_name: str):
    """Import a hyphenated module by path."""
    spec = importlib.util.spec_from_file_location(mod_name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# The pre-fix implementation, kept verbatim so the negative control is explicit
# rather than asserted in prose.
def legacy_substring(name: str, env_path: Path) -> bool:
    try:
        return f"{name}=1" in env_path.read_text()
    except OSError:
        return False


CASES = [
    # (.env contents, should_gate, label)
    ("SKIP_SKILL_SCANS=1\n", True, "bare active flag"),
    ("OTHER=x\nSKIP_SKILL_SCANS=1\nMORE=y\n", True, "active flag among others"),
    ("  SKIP_SKILL_SCANS=1  \n", True, "active flag with surrounding whitespace"),
    ("SKIP_SKILL_SCANS=1  # roaming\n", True, "active flag with trailing comment"),
    ("# SKIP_SKILL_SCANS=1\n", False, "COMMENTED flag must not gate"),
    ("#SKIP_SKILL_SCANS=1\n", False, "commented, no space, must not gate"),
    ("# disabled: SKIP_SKILL_SCANS=1 (was for travel)\n", False,
     "commented with prose must not gate"),
    ("SKIP_SKILL_SCANS=0\n", False, "explicit 0 must not gate"),
    ("SKIP_SKILL_SCANS=\n", False, "empty value must not gate"),
    ("NOT_SKIP_SKILL_SCANS=1\n", False, "different key must not gate"),
    ("SKIP_SKILL_SCANS_EXTRA=1\n", False, "key prefix must not gate"),
    ("", False, "empty .env must not gate"),
]


def main() -> int:
    scan = load("src/scan-catchup.py", "scan_catchup")
    sched = load("src/scheduled-catchup.py", "scheduled_catchup")

    for helper_name, mod in (("scan-catchup", scan), ("scheduled-catchup", sched)):
        fn = getattr(mod, "_dotenv_flag_enabled", None)
        check(f"{helper_name} exposes _dotenv_flag_enabled", callable(fn))
        if not callable(fn):
            continue
        for contents, expected, label in CASES:
            with tempfile.TemporaryDirectory() as d:
                p = Path(d) / ".env"
                p.write_text(contents)
                got = fn("SKIP_SKILL_SCANS", p)
                check(f"{helper_name}: {label}", got is expected,
                      f"expected {expected}, got {got}")

    # --- the negative control, run explicitly -------------------------------
    # Each "must not gate" case that the legacy form gets WRONG is a case this
    # fix actually repairs. If this count is 0 the test proves nothing.
    repaired = 0
    for contents, expected, label in CASES:
        if expected:
            continue
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ".env"
            p.write_text(contents)
            if legacy_substring("SKIP_SKILL_SCANS", p):
                repaired += 1
    check("legacy substring form fails at least 3 of the must-not-gate cases",
          repaired >= 3, f"legacy wrongly gated {repaired} case(s)")
    print(f"     (pre-fix form wrongly gated {repaired} of "
          f"{sum(1 for _, e, _ in CASES if not e)} must-not-gate cases)")

    # --- the env-var branch must still win ---------------------------------
    prev = os.environ.get("SKIP_SKILL_SCANS")
    os.environ["SKIP_SKILL_SCANS"] = "1"
    try:
        check("env var still gates regardless of .env",
              scan._node_skips_scans() is True)
    finally:
        if prev is None:
            os.environ.pop("SKIP_SKILL_SCANS", None)
        else:
            os.environ["SKIP_SKILL_SCANS"] = prev

    print()
    if FAILURES:
        print(f"{len(FAILURES)} failed: {FAILURES}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
