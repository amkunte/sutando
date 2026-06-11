# Fleet Sync

Durable bidirectional sync of **private** skill code + **gitignored** skill-state
across the fleet, via the existing private memory repo. For things that must
stay out of the public fork (`amkunte/sutando` is public).

**Usage**: `python3 skills/fleet-sync/scripts/fleet_sync.py [--dry-run]`

## Why

The public repo can't hold personal-only skills (e.g. `karts-air`'s
aircraft-acquisition criteria) or any skill's gitignored runtime state
(`orders.json`, `parcels.json`, `trips.json`, `subscriptions.json`,
`travel-preferences.json`, …). `notes/` sync is unreliable. During the Goose
home-base migration these were hand-copied over Discord — a one-time fix. This
skill makes it durable and automatic.

## How it works

- **Channel**: the existing PRIVATE memory repo (`~/.sutando/memory-sync`, same
  one `sync-memory.sh` uses) under a distinct top-level `fleet/` namespace —
  NOT the public repo, NOT sync-memory's one-way `machine-<host>/` backup dir.
- **Manifest** (`fleet/manifest.json`, lives in the private repo so skill names
  stay private): a list of items, each with an `owner` node. The **owner node
  pushes** that item to `fleet/data/<id>/`; **every other node pulls** it. One
  writer per item → no clobber.
- **This engine is generic** (no private data, no skill names) and tracked in
  the public repo. The private specifics live only in the manifest + data,
  which live only in the private repo.

Path placeholders in the manifest (`$REPO_DIR`, `$HOME`, `$WORKSPACE`) expand to
each node's own absolute paths, so the same manifest is correct on every node.

## Node identity

Each node needs a stable name matching the manifest `owner` fields. Set in
`.env`:

```
SUTANDO_NODE_NAME=goose      # or maverick, etc.
```

The engine reads it from the environment, then from `.env` (cron/launchd don't
inherit the shell env), then falls back to the short hostname.

## Safety (mirrors sync-memory.sh)

- Atomic-mkdir lock (`/tmp/fleet-sync.lock.d`) — concurrent runs serialize.
- `git pull --rebase --autostash` before push; one retry on non-fast-forward
  (coexists with the 5-min sync-memory cron writing the same repo).
- Mass-deletion tripwire: refuses a push deleting more than
  `SUTANDO_SYNC_MAX_DELETE` (default 50) files; override with
  `SUTANDO_FORCE_SYNC=1`.
- `rsync --delete` only ever targets a manifest dest path, never a parent.

## Adding an item

Edit `fleet/manifest.json` in the private repo (not this skill):

```json
{"id": "my-skill-state", "owner": "goose", "local": "$REPO_DIR/skills/my-skill/state", "kind": "dir"}
```

The owner node pushes it on its next run; peers pull it on theirs. No code
change needed.

## Cron

Runs every ~15 min, offset from the 5-min sync-memory cron to reduce git
contention (the lock + retry handle any overlap regardless):

```
11,26,41,56 * * * *   bash scripts/cron-gate.sh fleet-sync python3 skills/fleet-sync/scripts/fleet_sync.py
```

## Current manifest (2026-06-11)

| item | owner | what |
|---|---|---|
| amazon-orders-state | goose | scan state + history |
| parcel-radar-state | goose | scan state + history |
| trip-radar-state | goose | trips.json, travel-preferences.json, history |
| subscription-scanner-state | goose | scan state + history |
| karts-air | maverick | personal-only skill code + criteria/state |
