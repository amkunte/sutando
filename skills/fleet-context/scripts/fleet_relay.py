#!/usr/bin/env python3
"""Fleet-context RELAY (writer half).

Publishes a fleet-context entry so the OTHER node(s) become aware of what this
node just did / learned — near-instantly via a #bot2bot post, durably via a
per-node append-only log that rides the existing memory-sync repo.

Locked spec (Goose<>Maverick, 2026-06-10, owner Viper):
  - PER-NODE files: this node only ever writes its own FLAT top-level file
    `<memory>/fleet-context-<Node>.md`; each node reads ALL of them on ingest
    → zero shared-file writes → zero git-rebase conflicts (same lesson as the
    memory-sync race).
  - LOCATION = a FLAT `*.md` in the memory dir — NOT a subdir. There are two
    sync-memory.sh variants: the repo `scripts/sync-memory.sh` globs top-level
    `memory/*.md` ONLY; the local-install rsyncs `-a`. A subdir (`memory/fleet/`)
    silently dies under the glob script, and the workspace/repo `notes/` paths
    aren't safe (not synced / git-tracked). A flat `<memory>/fleet-context-*.md`
    rides the top-level `*.md` loop under BOTH scripts, and never pollutes the
    loaded context (only MEMORY.md is auto-loaded). The memory dir itself is
    node-portable — see memory_dir(), slug derived from the repo path.
  - Append-only, `[<Node> <ISO-UTC>]` line prefixes.
  - Two entry kinds:
      context: this node accepted a non-trivial task from the owner
      pref:    the owner stated a durable preference/decision in passing
               (CALLER must save it to durable memory FIRST, then relay —
               capture-then-relay, so nothing is lost between syncs).
  - The receiving node INGESTS silently (no #bot2bot reply) — see SKILL.md.

Usage:
    python3 skills/fleet-context/scripts/fleet_relay.py <context|pref> "<summary>"
    # optional: --no-post  (append to log only, skip #bot2bot)

Exit 0 on success (log always written; post failure is non-fatal + reported).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]  # skills/fleet-context/scripts/<file> → repo root


def resolve_workspace() -> Path:
    """Canonical workspace via the M0 helper `scripts/sutando-config.sh workspace`
    (config.local.json → config.json → <repo>/workspace). `$SUTANDO_WORKSPACE`
    is no longer honored post-v0.8/#1440 — the helper ignores it (warn-only)
    except under SUTANDO_TEST_MODE=1 for test isolation. Single source of truth
    (no divergent copy)."""
    import subprocess
    r = subprocess.run(
        ["bash", str(REPO / "scripts" / "sutando-config.sh"), "workspace"],
        capture_output=True, text=True,
    )
    return Path(r.stdout.strip())


def memory_dir() -> Path:
    """Claude-projects memory dir, NODE-PORTABLE.

    Derives the projects slug from the repo path so this resolves correctly on
    every node — Maverick (`/Users/abhi/sutando` → `-Users-abhi-sutando`) AND
    Goose (`/Users/abhi-mini/sutando` → `-Users-abhi-mini-sutando`). Hardcoding
    one node's slug would silently write to a dead dir on the other → no sync
    (Goose red-team). `$SUTANDO_MEMORY_DIR` still overrides everything.

    The BASE is resolved via claude_home_path(), not hardcoded to ~/.claude.
    The node-portability fix above got the slug right but left the base stale,
    and post-#1454 CLAUDE_CONFIG_DIR moves claude-home under the workspace — so
    on a migrated host this returned a REAL BUT ABANDONED directory and every
    relay write landed there. Measured on Goose 2026-07-23: two divergent
    memory dirs, ~/.claude/... with 64 files frozen at Jul-13 09:33 vs the
    canonical 65, and the legacy MEMORY.md was byte-identical (sha a7da42ec) to
    the state three memory files mysteriously REVERTED to earlier that day.
    Exactly the same failure this docstring already warns about — writing to a
    dead dir — one level further up the path."""
    env = os.environ.get("SUTANDO_MEMORY_DIR")
    if env:
        return Path(os.path.expanduser(env))
    slug = str(REPO).replace("/", "-")
    return _claude_home() / "projects" / slug / "memory"


def _claude_home() -> Path:
    """Claude-home base, preferring the repo's own resolver.

    Layered so this keeps working when the skill is run standalone (no repo
    src/ importable) and on a node that has not migrated: helper →
    $CLAUDE_CONFIG_DIR → the historic ~/.claude default.
    """
    try:
        sys.path.insert(0, str(REPO / "src"))
        from util_paths import claude_home_path  # type: ignore
        return Path(claude_home_path())
    except Exception:
        ccd = os.environ.get("CLAUDE_CONFIG_DIR")
        return Path(os.path.expanduser(ccd)) if ccd else Path.home() / ".claude"


def node_name(ws: Path) -> str:
    """Node callsign. env → discord-config node_name → state/fleet-node.txt → hostname.

    discord-config carries the real callsign (Maverick/Goose), so it's preferred
    over the raw hostname (which may be e.g. 'Maddy-MBP')."""
    env = os.environ.get("SUTANDO_NODE_NAME", "").strip()
    if env:
        return env
    try:
        cfg = json.loads((ws / "state" / "discord-config.json").read_text())
        n = str(cfg.get("node_name", "")).strip()
        if n:
            return n
    except (OSError, ValueError):
        pass
    try:
        v = (ws / "state" / "fleet-node.txt").read_text().strip()
        if v:
            return v
    except OSError:
        pass
    import socket
    return socket.gethostname().split(".")[0]


def bot2bot_channel(ws: Path) -> str:
    try:
        cfg = json.loads((ws / "state" / "discord-config.json").read_text())
        return str(cfg.get("channels", {}).get("bot2bot", "")).strip()
    except (OSError, ValueError):
        return ""


def main() -> int:
    args = [a for a in sys.argv[1:] if a != "--no-post"]
    no_post = "--no-post" in sys.argv[1:]
    if len(args) < 2 or args[0] not in ("context", "pref"):
        print('usage: fleet_relay.py <context|pref> "<summary>" [--no-post]', file=sys.stderr)
        return 2
    kind, summary = args[0], args[1].strip()
    if not summary:
        print("fleet_relay: empty summary", file=sys.stderr)
        return 2

    ws = resolve_workspace()
    node = node_name(ws)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")

    # 1) Durable: append to THIS node's own TOP-LEVEL memory .md (never a peer's).
    # `<memory>/fleet-context-<Node>.md` is FLAT (no subdir) on purpose: it's the
    # only location that syncs under BOTH sync-memory.sh variants in play — the
    # repo script globs top-level `*.md` only, the local-install script rsyncs -a.
    # A subdir (memory/fleet/) would silently fail under the glob script.
    mem = memory_dir()
    mem.mkdir(parents=True, exist_ok=True)
    log = mem / f"fleet-context-{node}.md"
    if not log.exists():
        log.write_text(f"# Fleet context — {node}\n\nAppend-only. One entry per line. Synced via the memory repo.\n\n")
    with log.open("a") as fh:
        fh.write(f"[{node} {stamp}] {kind}: {summary}\n")
    print(f"fleet_relay: appended to {log}")

    # 2) Instant: broadcast to #bot2bot (best-effort; log already durable).
    if not no_post:
        ch = bot2bot_channel(ws)
        if ch:
            msg = f"{kind}: {node} → fleet — {summary}"
            try:
                r = subprocess.run(
                    ["python3", str(REPO / "src" / "discord_post.py"), ch, msg],
                    cwd=str(REPO), capture_output=True, text=True, timeout=20,
                )
                if r.returncode == 0:
                    print(f"fleet_relay: posted to #bot2bot ({ch})")
                else:
                    print(f"fleet_relay: WARN #bot2bot post failed rc={r.returncode}: {r.stderr.strip()[:160]}", file=sys.stderr)
            except Exception as e:  # noqa: BLE001 — post is best-effort
                print(f"fleet_relay: WARN #bot2bot post raised: {e}", file=sys.stderr)
        else:
            print("fleet_relay: WARN no bot2bot channel configured — log-only", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
