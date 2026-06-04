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

1. The core agent downloads the sheet as **xlsx** via the in-session Google
   Drive MCP (coupled skill — runs in the core session). Binary export, NOT
   `read_file_content`, because the text rep truncates the ~1,800-row `daily`
   tab.
2. `scripts/extract_sheet.py` parses it with **openpyxl**:
   - the **`daily`** tab (the live net-worth time-series — updates ~1:15pm after
     market close, 5+ yrs of daily rows) → latest net worth + the sheet's own
     **day-over-day change** + a 7-day trend;
   - the **`Dashboard`** tab → asset-class split, Mag-7 concentration, liquid
     cash, FIRE net worth, ATH, milestone ladder.
3. `scripts/digest.py` renders today's Δ + 7-day trend + composition + threshold
   alerts, posts to #finance, rotates state.

The day-over-day delta comes straight from the `daily` tab (authoritative); the
skill persists its own prior point only as a fallback + for the milestone
re-cross guard. **Dependency:** `openpyxl` (`pip install --user openpyxl`).

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
