#!/usr/bin/env python3
"""Fleet-context RELAY (writer half).

Publishes a fleet-context entry so the OTHER node(s) become aware of what this
node just did / learned — near-instantly via a #bot2bot post, durably via a
per-node append-only log that rides the existing memory-sync repo.

Locked spec (Goose<>Maverick, 2026-06-10, owner Viper):
  - PER-NODE files: this node only ever writes its own
    `<memory>/fleet/context-<Node>.md`; each node reads ALL of them on ingest
    → zero shared-file writes → zero git-rebase conflicts (same lesson as the
    memory-sync race).
  - LOCATION = the memory dir (`$SUTANDO_MEMORY_DIR`, default
    `~/.claude/projects/-Users-abhi-sutando/memory`). VERIFIED this is what
    sync-memory.sh actually propagates: its `rsync -a "$MEM_SRC/" …` is
    RECURSIVE, so `memory/fleet/` syncs cross-node. (The workspace `notes/`
    dir is NOT synced — sync-memory uses the *repo* notes path — and the repo
    `notes/` is git-tracked, so neither is a safe home. memory/fleet/ syncs
    AND pollutes neither the repo nor the loaded context, since only MEMORY.md
    is auto-loaded, not subdirs.)
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
    env = os.environ.get("SUTANDO_WORKSPACE")
    return Path(os.path.expanduser(env)) if env else Path.home() / ".sutando" / "workspace"


def memory_dir() -> Path:
    env = os.environ.get("SUTANDO_MEMORY_DIR")
    if env:
        return Path(os.path.expanduser(env))
    return Path.home() / ".claude" / "projects" / "-Users-abhi-sutando" / "memory"


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
