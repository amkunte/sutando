#!/usr/bin/env python3
"""Fleet-sync engine — bidirectional sync of private skill code + gitignored
skill-state across the fleet via the PRIVATE memory repo.

Why this exists (owner directive 2026-06-11): some things must NOT live in the
public fork (amkunte/sutando is public) — personal-only skills (karts-air's
aircraft-acquisition criteria) and every skill's gitignored runtime state
(orders.json, parcels.json, trips.json, subscriptions.json,
travel-preferences.json …). The public repo can't hold them and `notes/` sync
is unreliable, so the fleet hand-copied state over Discord during the Goose
migration — a one-time fix, not a sustaining channel. This engine makes it
durable.

Design:
  * Channel = the existing PRIVATE memory repo (resolved per-node — see
    _resolve_sync_dir; legacy ~/.sutando-memory-sync or post-#762 ~/.sutando/memory-sync, the
    same repo sync-memory.sh uses) under a distinct top-level `fleet/`
    namespace. NOT the public repo; NOT sync-memory's `machine-<host>/`
    backup namespace (that's one-way disaster-recovery only).
  * Manifest-driven: `fleet/manifest.json` (lives in the private repo, so skill
    names stay private) lists items, each with an OWNER node. The owner node
    PUSHES that item; every other node PULLS it. One writer per item → no
    clobber.
  * This engine script is GENERIC (no private data, no skill names) → safe to
    track in the public repo. The private specifics live only in the manifest.

Safety (mirrors sync-memory.sh hard-won patterns):
  * Atomic-mkdir lock so concurrent runs serialize.
  * git pull --rebase --autostash before push; one retry on non-fast-forward.
  * Mass-deletion tripwire: refuse a push that deletes more than N files.
  * rsync --delete only ever targets a manifest dest path, never a parent.

Usage:
  python3 skills/fleet-sync/scripts/fleet_sync.py [--dry-run] [--once]
  --dry-run : show what would push/pull, touch nothing.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

HOME = Path.home()
REPO_DIR = Path(__file__).resolve().parents[3]  # skills/fleet-sync/scripts/<f> → repo root
WORKSPACE = Path(os.environ.get("SUTANDO_WORKSPACE", str(HOME / ".sutando" / "workspace"))).expanduser()
def _resolve_sync_dir() -> Path:
    """Locate the private memory-sync repo, path-agnostic across the fleet.

    Nodes are on DIFFERENT clone paths depending on when they were set up:
      * post-#762 migration: ~/.sutando/memory-sync   (e.g. Goose)
      * legacy (pre-#762):   ~/.sutando-memory-sync    (e.g. Maverick)
    Both point at the same remote (amkunte/sutando-memory); only the local
    clone dir differs. A single hardcoded default can't serve both — the
    original ~/.sutando/memory-sync default broke Maverick, and naively
    hardcoding the hyphenated path would break Goose. So probe both and use
    whichever exists (preferring the post-#762 path), mirroring how
    sync-memory.sh resolves its own clone. SUTANDO_MEMORY_SYNC_DIR overrides.
    """
    env = os.environ.get("SUTANDO_MEMORY_SYNC_DIR")
    if env:
        return Path(env).expanduser()
    new = HOME / ".sutando" / "memory-sync"   # post-#762
    legacy = HOME / ".sutando-memory-sync"    # legacy
    return new if new.exists() else (legacy if legacy.exists() else new)


SYNC_DIR = _resolve_sync_dir()
FLEET_DIR = SYNC_DIR / "fleet"
MANIFEST = FLEET_DIR / "manifest.json"
DATA_DIR = FLEET_DIR / "data"
# Share sync-memory.sh's lock so the two repo-writers SERIALIZE per node:
# at most one writer per node at a time → ≤2 concurrent writers fleet-wide
# (same contention as today), not 4. Addresses the collision-storm red-team
# (Maverick 2026-06-11) without modifying the hold-listed sync-memory.sh.
LOCK_DIR = Path("/tmp/sync-memory.lock.d")
MAX_DELETE = int(os.environ.get("SUTANDO_SYNC_MAX_DELETE", "50"))


def node_name() -> str:
    """Stable per-node id. Prefer SUTANDO_NODE_NAME (env, then .env file —
    cron/launchd invocations don't inherit the shell env); fall back to short
    hostname. Reading .env mirrors sync-memory.sh, since this is most often
    invoked by a cron that hasn't sourced the profile."""
    n = os.environ.get("SUTANDO_NODE_NAME")
    if not n:
        env_file = REPO_DIR / ".env"
        try:
            for line in env_file.read_text().splitlines():
                line = line.strip()
                if line.startswith("SUTANDO_NODE_NAME="):
                    n = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
        except OSError:
            pass
    if n:
        return n.strip().lower()
    return run(["hostname", "-s"], check=False).strip().lower()


def run(cmd, cwd=None, check=True) -> str:
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"cmd failed ({r.returncode}): {' '.join(cmd)}\n{r.stderr}")
    return r.stdout


def expand(p: str) -> Path:
    """Expand manifest path placeholders to this node's absolute paths."""
    return Path(
        p.replace("$REPO_DIR", str(REPO_DIR))
         .replace("$WORKSPACE", str(WORKSPACE))
         .replace("$HOME", str(HOME))
    ).expanduser()


def acquire_lock() -> bool:
    if LOCK_DIR.exists():
        # stale lock cleanup (>10 min)
        if time.time() - LOCK_DIR.stat().st_mtime > 600:
            try:
                LOCK_DIR.rmdir()
            except OSError:
                pass
    try:
        LOCK_DIR.mkdir()
        return True
    except FileExistsError:
        return False


def git(args, check=True) -> str:
    return run(["git", "-C", str(SYNC_DIR)] + args, check=check)


def _rsync_args(src: Path, dst: Path, dry: bool) -> list[str]:
    # Exclude history/ snapshot dirs (per-skill rollback backups) — syncing 36+
    # snapshots/skill bloats the private repo's git history permanently. We sync
    # CURRENT state cross-fleet; history stays local on the owning node
    # (red-team, Maverick 2026-06-11).
    # --checksum: compare by content hash, not size+mtime. The data is small
    # (state JSONs + a personal skill dir) so the cost is trivial, and it makes
    # change-detection content-correct — no transfer on a pure mtime bump (the
    # churn risk), and a real content change always syncs even if size+mtime
    # happen to match. Same pattern sync-memory.sh uses for its small rsyncs.
    args = ["rsync", "-a", "--checksum", "--delete", "--itemize-changes",
            "--exclude=history", "--exclude=node_modules", "--exclude=__pycache__",
            "--exclude=*.pyc", "--exclude=.DS_Store", "--exclude=.venv",
            f"{src}/", f"{dst}/"]
    if dry:
        args.insert(1, "-n")
    return args


def _real_changes(out: str) -> bool:
    # Count only REAL changes: itemize lines whose first char is a transfer
    # (`<`/`>`), local create/change (`c`), hardlink (`h`), or a message such
    # as `*deleting`. Lines starting with `.` are attribute-only (e.g. a
    # directory mtime bump) and must NOT trigger a commit+push — otherwise the
    # repo churns every run (the contention the red-team warned about).
    return any(ln[:1] in "<>ch*" for ln in out.splitlines() if ln.strip())


def rsync(src: Path, dst: Path, dry: bool) -> bool:
    """Mirror src/ → dst/ with --delete. Returns True if anything changed.
    --delete is bounded to dst (a manifest-listed path), never a parent.
    Used PUSH-side (dst = repo, recoverable via reflog)."""
    dst.mkdir(parents=True, exist_ok=True)
    return _real_changes(run(_rsync_args(src, dst, dry), check=False))


def rsync_pull(src: Path, dst: Path, dry: bool):
    """Guarded PULL: dst is a LOCAL gitignored skill dir (NOT recoverable), so
    the --delete blast radius is asymmetric vs push. Two guards before we let
    --delete touch local state (red-team, Maverick 2026-06-11):

      1. Empty-src guard: a legitimate owner state is never empty — it always
         has at least the current-state file. An empty/partial `src` means a
         failed/racing push or corruption; mirroring it would WIPE good local
         state (catastrophic if a node-identity misconfig flipped the OWNER
         into a puller). Refuse.
      2. Mass-delete abort: dry-run, count `*deleting` lines; if it would delete
         more than MAX_DELETE local files, refuse.

    Both overridable with SUTANDO_FORCE_SYNC=1. Returns (changed, note)."""
    if not any(p.is_file() for p in src.rglob("*")):
        return False, "skip: source empty (refusing to --delete local state)"
    dst.mkdir(parents=True, exist_ok=True)
    dry_out = run(_rsync_args(src, dst, True), check=False)
    deletes = sum(1 for ln in dry_out.splitlines() if ln.startswith("*deleting"))
    if deletes > MAX_DELETE and os.environ.get("SUTANDO_FORCE_SYNC") != "1":
        return False, f"skip: would delete {deletes} local files (>{MAX_DELETE}); set SUTANDO_FORCE_SYNC=1 to override"
    if dry:
        return _real_changes(dry_out), "dry-run"
    return _real_changes(run(_rsync_args(src, dst, False), check=False)), ""


def main() -> int:
    dry = "--dry-run" in sys.argv
    node = node_name()

    if not SYNC_DIR.exists():
        print(f"fleet-sync: private repo not found at {SYNC_DIR} — run sync-memory.sh first to clone it.")
        return 0

    if not acquire_lock():
        print("fleet-sync: another run in progress, skipping.")
        return 0
    try:
        # Always pull the latest private repo first so the manifest + peers' data are current.
        git(["pull", "--rebase", "--autostash"], check=False)

        if not MANIFEST.exists():
            print(f"fleet-sync: no manifest at {MANIFEST} — nothing to sync yet (seed it in the private repo).")
            return 0
        manifest = json.loads(MANIFEST.read_text())
        items = manifest.get("items", [])

        pushed, pulled, skipped = [], [], []

        # PUSH: items this node owns → repo/fleet/data/<id>/
        changed = False
        for it in items:
            if it.get("owner") != node:
                continue
            local = expand(it["local"])
            if not local.exists():
                skipped.append(f"{it['id']} (local missing: {local})")
                continue
            dest = DATA_DIR / it["id"]
            if rsync(local, dest, dry):
                pushed.append(it["id"])
                changed = True

        # Commit + push any pending fleet/ change (race-safe retry vs sync-memory.sh).
        # Stage the WHOLE fleet/ tree — owned-item data AND a hand-edited manifest.
        # The manifest must propagate too (e.g. an ownership flip): keying the
        # commit only on rsync data-changes left a manifest edit sitting
        # uncommitted until some data also changed (gap found 2026-06-11 during
        # the karts-air ownership move). `git add fleet` only touches fleet/, so
        # this never interferes with sync-memory's memory/ commits in the same repo.
        if not dry:
            git(["add", "fleet"])
            staged = [l for l in git(["status", "--porcelain", "fleet"]).splitlines() if l.strip()]
            if staged:
                deleted = len([l for l in git(["diff", "--cached", "--name-only", "--diff-filter=D"]).splitlines() if l])
                if deleted > MAX_DELETE and os.environ.get("SUTANDO_FORCE_SYNC") != "1":
                    git(["reset", "-q"], check=False)
                    print(f"fleet-sync: ABORT — push would delete {deleted} files (>{MAX_DELETE}). Set SUTANDO_FORCE_SYNC=1 to override.")
                    return 1
                what = f"push [{', '.join(pushed)}]" if pushed else "manifest/config update"
                git(["commit", "-m", f"fleet-sync: {node} {what}"], check=False)
                # Bounded rebase-retry: the private repo has up to 4 writers
                # (each node's sync-memory.sh + this engine). Non-fast-forward
                # rejections are normal under contention — pull-rebase and retry.
                pushed_ok = False
                for attempt in range(6):
                    git(["pull", "--rebase", "--autostash"], check=False)
                    r = subprocess.run(["git", "-C", str(SYNC_DIR), "push"],
                                       capture_output=True, text=True)
                    if r.returncode == 0:
                        pushed_ok = True
                        break
                    time.sleep(1 + attempt)  # small backoff
                if not pushed_ok:
                    print("fleet-sync: WARN — push still failing after retries; next run will retry.")

        # PULL: items this node does NOT own → expand(local). Guarded, because
        # dst is unrecoverable local state (see rsync_pull).
        for it in items:
            if it.get("owner") == node:
                continue
            src = DATA_DIR / it["id"]
            if not src.exists():
                continue  # owner hasn't pushed yet
            local = expand(it["local"])
            changed, note = rsync_pull(src, local, dry)
            if changed:
                pulled.append(it["id"])
            if note and note.startswith("skip"):
                skipped.append(f"{it['id']} pull {note}")

        tag = "[dry-run] " if dry else ""
        print(f"fleet-sync ({node}): {tag}pushed={pushed or '-'} pulled={pulled or '-'}"
              + (f" skipped={skipped}" if skipped else ""))
        return 0
    finally:
        try:
            LOCK_DIR.rmdir()
        except OSError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
