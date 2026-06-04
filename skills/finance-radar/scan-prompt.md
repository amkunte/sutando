# Finance Radar — scan-prompt (follow verbatim)

Produce a private finance digest to Discord **#finance** from the owner's Google
Sheet. Output goes ONLY to #finance — **no web page**, no DM/Telegram, no
`results/` file. Sensitive data: keep figures in the skill's own state, not the
shared workspace.

Config: `skills/finance-radar/config.json` (`sheet_id`, focus `tabs`, alert
thresholds). Coupled skill — it needs the in-session **Google Drive MCP**
connector to read the sheet, so it runs in the core agent session.

## Step 1 — read the sheet

Read the sheet via `mcp__claude_ai_Google_Drive__read_file_content` with the
`sheet_id` from config. It returns a text representation of ALL tabs. **Focus
ONLY on the `dashboard`, `master journal`, and `daily` tabs — ignore the rest.**
(The sheet is large; if the tool truncates, the dashboard block is near the top
of the relevant section — that's the one that matters.)

> Note: the `daily` tab is a dead time-series (stopped updating Nov 2021). Do
> NOT use it for the live number — the live net worth is the dashboard's
> "Current Net Worth" cell. The `master journal` refreshes only ~quarterly.

## Step 2 — extract the dashboard into latest-snapshot.json

From the **dashboard** tab, extract these figures and write them to
`skills/finance-radar/state/latest-snapshot.json` (omit any field you can't find
— the digest degrades gracefully; never guess a number):

```json
{
  "as_of": "YYYY-MM-DD",          // the dashboard "as of"/Live date
  "net_worth": 0,                  // Current Net Worth (REQUIRED)
  "ath": 0, "ath_date": "YYYY-MM-DD",
  "asset_classes": {"Cash": 0, "Liabilities": 0, "Real Estate": 0, "Side Inv": 0, "Stock": 0},
  "mag7": {"concentration_pct": 0.0, "holdings": {"MSFT": 0, "GOOG": 0, "TSLA": 0, "NVDA": 0}},
  "liquid_cash": 0,
  "fire_net_worth": 0,             // "FIRE Net Worth (excl. primary residence)"
  "next_milestone": 0              // next un-crossed $0.5M net-worth milestone
}
```
Liabilities are negative; real-estate and stock positive. Use whole dollars.

## Step 3 — diff, alert, post

Run the digest (it computes week-over-week Δ vs the stored prior point, evaluates
threshold alerts, renders, posts to #finance, then rotates state):
```bash
python3 skills/finance-radar/scripts/digest.py --post
```
Dry-run without `--post` to preview. The script posts to the `finance` channel
from `discord-config.json`; if that channel is unset it errors loudly and posts
nothing (no fallback — #finance or nothing, by design).

**Privacy:** do NOT echo any of the financial figures back in your
conversational/task reply — the numbers belong in #finance only. After a
successful `--post`, just confirm the digest was dispatched (e.g. "Posted this
week's finance digest to #finance"), nothing more.

## Cadence & alerts

Weekly by default (config `cadence`). Standing alerts the digest raises:
Mag-7 concentration ≥ threshold, liquid cash below floor, a net-worth milestone
crossed since last digest, and a drawdown ≥ threshold off the all-time high.

## Monthly add-on (optional)

When a NEW `master journal` snapshot date appears (it re-snapshots ~quarterly),
also diff the latest two snapshot dates and surface the biggest position moves
(e.g. "GOOG +$X, TSLA −$Y, primary mortgage paid down $Z") as an extra line.
