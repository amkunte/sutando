---
name: fleet-context
description: Share task-context and owner preferences across the Maverick/Goose fleet so either node stays aware of what the other is doing — instantly via #bot2bot, durably via the synced memory repo.
---

# Fleet Context

Keeps every node in the fleet on the same page about the owner's (Viper's) tasks,
choices, and preferences. Two layers:

1. **Durable** — each node appends to its OWN log `<memory>/fleet/context-<Node>.md`
   (Maverick→context-Maverick.md, Goose→context-Goose.md). These sync via the
   private memory repo (~5 min cadence). Each node writes ONLY its own file →
   zero shared-file git conflicts. Each node READS all of them.
2. **Live** — the relay also posts a `context:`/`pref:` line to **#bot2bot**, which
   the other node ingests instantly via the peer pipeline. So awareness is
   near-instant; the synced file is the durable backstop.

## When to RELAY (post your own context)

Run this when YOU (this node) take on something worth the other node knowing:

- **Accepted a non-trivial task from the owner:**
  ```bash
  python3 skills/fleet-context/scripts/relay.py --kind context \
    --summary "Viper asked me to <X>; status: <in-progress/done>"
  ```
- **Owner stated a durable preference/decision in passing** —
  **capture-then-relay**: save it to memory FIRST (so it's durable even before
  the next sync), THEN relay:
  ```bash
  # 1. write the pref to <memory>/<slug>.md + index it in MEMORY.md
  # 2. relay it:
  python3 skills/fleet-context/scripts/relay.py --kind pref \
    --summary "Viper prefers <X>"
  ```

Keep summaries to 1–3 lines. Don't relay trivia (greetings, quick lookups).

## When to INGEST (peer posted context)

When a `context:`/`pref:` message from the OTHER node arrives in #bot2bot, it
reaches you as a **peer-tier** task. Ingest it **silently**:
- Read it → you're now aware in-session.
- Write `[no-send]` as the result (or archive) — **never reply** (a reply would
  start a two-bot ack loop; this is the loop-guard we both exercised).
- The durable copy lands in the peer's `context-<Node>.md` at next sync; no need
  to write the peer's file yourself (you'd conflict with their writes).

## Read the full fleet picture

At session catchup, or when you need to know what the other node has been doing:
```bash
python3 skills/fleet-context/scripts/read-context.py --tail 25
```

## Design notes (agreed Maverick↔Goose, 2026-06-10)

- Per-node files, not one shared log → no git conflicts.
- Silent ingest → no bot-to-bot ack loop.
- Capture-then-relay for passing prefs → nothing lost between syncs.
- Memory-sync cadence tightened 30→~5 min (crons.json; sync-memory.sh untouched).
- Owner = **Viper** (NOT "Chi" — that's the external upstream maintainer).
