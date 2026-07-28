#!/usr/bin/env python3
"""Guard: exec-approval must resolve the Discord bot token via claude_home_path().

The exec-approval gate is the mechanism that routes untrusted / peer-requested actions to the owner
for sign-off. Its notification path hardcoded the PRE-migration location:

    DISCORD_ENV = Path.home() / ".claude" / "channels" / "discord" / ".env"

Post-M2 the channels dir lives under $CLAUDE_CONFIG_DIR (<workspace>/.claude-sutando/). On a migrated
host that file simply does not exist, so `_discord_token()` raised, `post_to_approvals()` printed a
WARN and returned False, and the approval was recorded to disk while **the owner was never
notified** — with the caller still receiving an id that looked like success.

Observed 2026-07-25: a real approval request emitted
`WARN: [Errno 2] No such file or directory: '/Users/abhi/.claude/channels/discord/.env';
approval not posted to Discord` while the token was present at the migrated path all along.

It fails closed (the action is not taken), so this is not a security hole — but it fails INVISIBLY,
which for a gate whose entire purpose is surfacing to a human is its own kind of broken.

These tests pin the resolution rule, not the notification itself: no Discord calls are made.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
COMMON = REPO / "skills" / "exec-approval" / "scripts" / "common.py"

failures = []


def check(cond: bool, label: str) -> None:
    if cond:
        print(f"  ok  {label}")
    else:
        print(f"FAIL: {label}")
        failures.append(label)


def resolved_under(config_dir: str) -> str:
    """Import common.py in a fresh interpreter with CLAUDE_CONFIG_DIR set, print DISCORD_ENV."""
    env = dict(os.environ)
    env.pop("CLAUDE_HOME", None)
    if config_dir is None:
        env.pop("CLAUDE_CONFIG_DIR", None)
    else:
        env["CLAUDE_CONFIG_DIR"] = config_dir
    code = (
        "import sys; sys.path.insert(0, %r);"
        "import common; print(common.DISCORD_ENV)" % str(COMMON.parent)
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
    if r.returncode != 0:
        return f"<import failed: {r.stderr.strip().splitlines()[-1] if r.stderr.strip() else '?'}>"
    return r.stdout.strip()


def main() -> int:
    if not COMMON.is_file():
        print(f"SKIP: {COMMON} not present")
        return 0

    # 1. The source must not hardcode the legacy path.
    src = COMMON.read_text()
    code_lines = [l for l in src.splitlines() if not l.lstrip().startswith("#")]
    hardcoded = any(
        '".claude"' in l and "channels" in l and "claude_home_path" not in l
        for l in code_lines
    )
    check(not hardcoded, "common.py does not hardcode ~/.claude/channels for the token")

    # 2. It must honor $CLAUDE_CONFIG_DIR — the migrated location.
    with tempfile.TemporaryDirectory() as td:
        got = resolved_under(td)
        want = str(Path(td) / "channels" / "discord" / ".env")
        check(got == want, f"honors $CLAUDE_CONFIG_DIR (got {got})")

    # 3. A different CLAUDE_CONFIG_DIR must move the path — i.e. it is genuinely resolved,
    #    not coincidentally equal.
    with tempfile.TemporaryDirectory() as td2:
        got2 = resolved_under(td2)
        check(
            got2 == str(Path(td2) / "channels" / "discord" / ".env"),
            "path tracks the env var rather than being fixed",
        )

    # 4. With no CLAUDE_CONFIG_DIR it must still resolve to the documented ~/.claude default,
    #    so un-migrated / vanilla hosts keep working.
    got3 = resolved_under(None)
    check(
        got3 == str(Path.home() / ".claude" / "channels" / "discord" / ".env"),
        "falls back to ~/.claude when CLAUDE_CONFIG_DIR is unset",
    )

    print()
    if failures:
        print(f"FAILED — {len(failures)} of 4 exec-approval token-path checks")
        return 1
    print("OK — 4/4 exec-approval token-path tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
