# Upstream Sync

Periodically pulls new commits from the upstream `sonichi/sutando` repo into your fork's `main`.

## What it does

1. `git fetch upstream`
2. Compares `upstream/main` to local `main`. If no new commits, stays silent and exits 0.
3. If there are new commits, attempts to merge `upstream/main` into `main`:
   - **Fast-forward possible** → applies cleanly, no merge commit
   - **Non-FF but no conflict** → merge commit with the upstream-pulled summary
   - **Conflict** → aborts merge, leaves `main` untouched, writes a `proactive-{ts}.txt` to `results/` so you get a Telegram nudge with what to do
4. On clean merge, pushes to `origin/main` (your fork) and writes a brief Telegram notification with the commit count and short subjects.

## Behavior on edge cases

- Working tree dirty (uncommitted changes) → abort, notify owner. Don't risk losing local work.
- Not on `main` branch → abort, notify owner. Sync is `main`-only by design.
- Any `git` command fails → abort with the error in the Telegram notification.

## How to run manually

```bash
bash skills/upstream-sync/scripts/sync.sh
```

Exit codes:
- `0` — nothing new (stayed silent), or sync succeeded
- `1` — failed (working tree dirty, conflicts, or `git` error). A `proactive-{ts}.txt` was written.

## Cron schedule

Daily at 08:07 — see `skills/schedule-crons/crons.json` entry `upstream-sync`. The 8:07 minute is intentional (off the :00/:30 pile-up; lands a few minutes after morning-briefing's 6:57 fire so the morning brief reflects yesterday's main, and the sync happens before the workday starts).
