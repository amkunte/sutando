---
name: finance-radar
description: "Private weekly net-worth digest to Discord #finance, sourced from the owner's Google Sheet (dashboard / master-journal / daily tabs). Posts net worth + week-over-week delta, asset-class split, Mag-7 concentration, liquid cash, FIRE progress, next milestone, and threshold alerts. Output goes ONLY to #finance — no web page, no DM. Use on demand or on a weekly cron."
user-invocable: true
---

# Finance Radar

A private finance digest for Discord **#finance**. Reads the owner's financial
Google Sheet, surfaces the headline net-worth picture + standing alerts, and
posts it to #finance **only** — deliberately no web page (unlike parcel/trip
radar) and no DM/Telegram, because the data is sensitive.

## How it works

1. The core agent reads the configured sheet via the in-session **Google Drive
   MCP** (this makes it a *coupled* skill — it runs in the core session, not
   standalone) and extracts the **dashboard** tab into
   `state/latest-snapshot.json`.
2. `scripts/digest.py` diffs that against the stored prior week's point,
   evaluates alert thresholds, renders the digest, posts it to #finance, and
   rotates state (so next week has a baseline for the Δ).

The `daily` tab of the sheet is a dead time-series (stopped Nov 2021), so the
skill keeps its OWN weekly history in `state/` — resurrecting the
week-over-week delta the dead tab used to provide.

## Usage

```bash
# Full run is driven by the scan-prompt (agent reads sheet → writes snapshot → posts):
#   follow skills/finance-radar/scan-prompt.md
# Or, once state/latest-snapshot.json exists:
python3 skills/finance-radar/scripts/digest.py          # dry-run preview
python3 skills/finance-radar/scripts/digest.py --post   # post to #finance + persist
```

## What it surfaces

Net worth + Δ vs last digest · off all-time-high · asset-class split (Cash /
Stock / Real Estate / Liabilities / Side Inv) · Mag-7 concentration · liquid
cash · FIRE net worth (excl. primary residence) · distance to next $0.5M
milestone. **Alerts:** Mag-7 concentration ≥ threshold, liquid cash below floor,
milestone crossed, drawdown ≥ threshold off ATH. Thresholds in `config.json`.

## Privacy

- Output channel is #finance only — `post_to_finance()` errors loudly and posts
  nothing if that channel is unset (no DM/Telegram/web fallback by design).
- Financial figures live in the skill's own gitignored `state/` (never the
  shared workspace, never committed). Discord posts use `allowed_mentions:
  parse:[]` so nothing in the data can ping.

## Files

- `config.json` — sheet id, focus tabs, alert thresholds.
- `scan-prompt.md` — the read→extract→post procedure (follow verbatim).
- `scripts/digest.py` — diff + alerts + render + post + state rotation.
- `scripts/common.py` — config, state, #finance poster, formatting.
