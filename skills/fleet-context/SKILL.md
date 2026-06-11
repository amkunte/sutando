---
name: fleet-context
description: Share task-context and owner preferences across the Maverick/Goose fleet so either node stays aware of what the other is doing — instantly via #bot2bot, durably via the synced memory repo.
---

# Fleet Context

Keeps every node in the fleet on the same page about the owner's (Viper's) tasks,
choices, and preferences. Two layers:

1. **Durable** — each node appends to its OWN log `<memory>/fleet/context-<Node>.md`
   (Maverick→context-Maverick.md, Goose→context-Goose.md). Each node writes ONLY
   its own file → zero shared-file git conflicts. Each node READS all of them.
   These sync via the private memory repo (~5 min cadence).
2. **Live** — the relay also posts a `context:`/`pref:` line to **#bot2bot**, which
   the other node ingests instantly via the peer pipeline. Awareness is
   near-instant; the synced file is the durable backstop.

> **Why `<memory>/fleet/` and not `notes/`?** Verified against sync-memory.sh:
> its `rsync -a "$MEM_SRC/"` is RECURSIVE, so `memory/fleet/` propagates
> cross-node. The workspace `notes/` dir is NOT synced (sync uses the *repo*
> notes path), and the repo `notes/` is git-tracked (would pollute `git status`).
> `memory/fleet/` syncs AND pollutes neither the repo nor the loaded context
> (only MEMORY.md is auto-loaded, not subdirs).

## When to RELAY (post your own context)

Run when YOU (this node) take on something worth the other node knowing:

- **Accepted a non-trivial task from the owner:**
  ```bash
  python3 skills/fleet-context/scripts/fleet_relay.py context "Viper asked me to <X>; status: <in-progress/done>"
  ```
- **Owner stated a durable preference/decision in passing** — **capture-then-relay**:
  save it to memory FIRST (durable even before the next sync), THEN relay:
  ```bash
  # 1. write the pref to <memory>/<slug>.md + index it in MEMORY.md
  # 2. python3 skills/fleet-context/scripts/fleet_relay.py pref "Viper prefers <X>"
  ```

Keep summaries 1–3 lines. Don't relay trivia (greetings, quick lookups).

## When to INGEST (peer posted context)

A `context:`/`pref:` message from the OTHER node arrives in #bot2bot as a
**peer-tier** task. Ingest it **silently**:
- Read it → you're now aware in-session.
- Write `[no-send]` as the result (or archive) — **never reply** (a reply starts a
  two-bot ack loop; this is the loop-guard we exercised live).
- The durable copy lands in the peer's `context-<Node>.md` at next sync; don't
  write the peer's file yourself (you'd conflict with their writes).

## Read the full fleet picture

At session catchup / on demand — merged, time-ordered across all nodes:
```bash
python3 skills/fleet-context/scripts/read_fleet.py --limit 25
python3 skills/fleet-context/scripts/read_fleet.py --since-hours 6
```

## Design notes (agreed Maverick↔Goose, 2026-06-10; converged after mutual red-team)

- Per-node files → no git conflicts.
- Silent ingest → no bot-to-bot ack loop.
- Capture-then-relay for passing prefs → nothing lost between syncs.
- Memory-sync cadence tightened 30→~5 min (crons.json; sync-memory.sh untouched).
- node_name resolves env → discord-config callsign → state/fleet-node.txt → hostname.
- Owner = **Viper** (NOT "Chi" — that's the external upstream maintainer).
