# Upstream Sync

Periodically fast-forwards your fork's `main` from upstream `sonichi/sutando` so it always mirrors upstream exactly. Does NOT touch your deployment branch (`local-main`).

## Branch model

```
sonichi/sutando         (upstream)
        │
        ▼
amkunte/main            mirrors upstream/main EXACTLY (clean, contributable)
        │
        ▼
amkunte/local-main      deployment branch — personal commits live here; rebased
                        on main manually by owner, one commit at a time, when
                        ready to bring upstream changes into the running system
```

Why this split:
- `main` stays a clean mirror — any PR opened against `sonichi/sutando` from `main` has no merge noise.
- `local-main` carries the things you don't want to push upstream (personal skills, host-specific config, in-flight WIP) and is the branch the running services check out.
- Conflicts during upstream merges are concentrated at the `local-main` rebase step, which the owner runs deliberately when they want to bring upstream changes in. The cron stays conflict-free.

## What the script does

1. `git fetch upstream main`
2. If `main` has no local commits beyond `upstream/main`, fast-forwards `main` to `upstream/main` and pushes.
3. If `main` somehow has unexpected commits (rebase model was violated), aborts and notifies — owner must move those commits to `local-main` and reset `main`.
4. Never touches `local-main`. Owner rebases `local-main` onto `main` manually when ready (see below).
5. Notifies via Telegram only on success-with-changes or failure. Stays silent on no-op and on dirty-tree-skip.

## Behavior on edge cases

- Working tree dirty (uncommitted changes) → skip silently (repo accumulates long-running uncommitted state owner is aware of).
- `main` has local commits not on `upstream/main` → abort, notify owner (rebase model violated).
- Fast-forward rejected → abort with error in Telegram notification.
- Push to `origin/main` fails → notify owner to retry manually.

The script remembers whatever branch you started on and restores it on exit. Safe to run while you're on `local-main` (the common case).

## How to run manually

```bash
bash skills/upstream-sync/scripts/sync.sh
```

Exit codes:
- `0` — nothing new (silent), or sync succeeded
- `1` — failed (rebase model violated, push error, etc.). A `proactive-upstream-sync-{ts}.txt` was written.

## Bringing upstream changes into `local-main` (manual step, owner-only)

After the cron has fast-forwarded `main`, your deployment is still running the old code on `local-main`. To pull upstream changes in:

```bash
git checkout local-main
git rebase main
# resolve any conflicts per-commit
git push --force-with-lease origin local-main
```

Why manual: rebasing 10+ upstream commits onto your deployment branch often surfaces conflicts (Chi rebases PRs before merging upstream, so add/add conflicts on the same file are common). Resolving these per-commit is surgical; doing it inside the cron forces all-or-nothing decisions on stale auto-merge attempts. Owner does this when they have a few minutes and want to deploy a fresh main.

## Cron schedule

Daily at 08:07 — see `skills/schedule-crons/crons.json` entry `upstream-sync`. The 8:07 minute is intentional (off the :00/:30 pile-up; lands a few minutes after morning-briefing's 6:57 fire so the morning brief reflects yesterday's main, and the sync happens before the workday starts).
