# Disk Cleanup

Monthly easy-wins disk reclamation. Removes regenerable caches and stale installers; never touches user data, source repos, or app data.

## What it cleans

- `~/Downloads/*.{dmg,pkg,zip}` ≥50 MB and >30 days old
- `~/.npm` cache (`npm cache clean --force`)
- Homebrew cache + stale portable-rubies (`brew cleanup -s`)
- `~/Library/Caches/com.spotify.client`
- `~/Library/Caches/electron`
- `~/.gradle/caches`
- pip cache (`pip3 cache purge`)

All targets re-create themselves on next use — no user impact.

## What it doesn't touch

User data, photo libraries, X-Plane, Docker VMs (those require active confirmation per the 2026-05-19 reclamation session), Chrome cache (requires UI flow), `~/.gemini`, `~/.sutando`, source repos, anything under `~/Documents` except files explicitly named above.

## Scheduling

Configured in `skills/schedule-crons/crons.json` as `monthly-disk-cleanup` — fires once a month. Writes `results/proactive-disk-cleanup-<ts>.txt` ONLY when ≥1 GB was reclaimed; stays silent otherwise so it doesn't notify on no-op months.

## Manual run

```bash
bash skills/disk-cleanup/scripts/clean.sh
```

Override notification threshold (MB):

```bash
DISK_CLEANUP_NOTIFY_THRESHOLD_MB=512 bash skills/disk-cleanup/scripts/clean.sh
```

## History

Created 2026-05-19 after the one-shot reclamation that freed 141 GB (118 GB GoPro + 13 GB Docker + 11 GB easy-wins). The easy-wins portion became this skill — recurring, auto-pilot. The bigger one-shot items (GoPro / Docker / X-Plane) stay manual on purpose; they require human judgment about whether the data is still wanted.
