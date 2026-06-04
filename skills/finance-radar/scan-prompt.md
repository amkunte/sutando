# Finance Radar — scan-prompt (follow verbatim)

Produce a private finance digest to Discord **#finance** from the owner's Google
Sheet. Output goes ONLY to #finance — **no web page**, no DM/Telegram, no
`results/` file. Sensitive data: keep figures in the skill's own state, not the
shared workspace.

Config: `skills/finance-radar/config.json` (`sheet_id`, focus `tabs`, alert
thresholds). Coupled skill — it needs the in-session **Google Drive MCP**
connector to read the sheet, so it runs in the core agent session.

## Step 1 — download the sheet as xlsx

Do **not** use `read_file_content` — its text rep truncates the ~1,800-row
`daily` tab to ~191 rows (which once made us wrongly think the tab was dead).
Download the real binary workbook instead:

```
mcp__claude_ai_Google_Drive__download_file_content(
    fileId=<sheet_id from config>,
    exportMimeType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
```
The result is JSON `{content: <base64 xlsx>}` (large → likely saved to a
tool-result file path; if so, read the `content` field from that path). Decode
the base64 to a temp file:
```bash
python3 - <<'PY'
import json, base64
src = "<inline JSON or the saved tool-result file path>"
d = json.load(open(src)) if src.endswith(".txt") else json.loads(src)
open("/tmp/finance.xlsx","wb").write(base64.b64decode(d["content"]))
print("wrote /tmp/finance.xlsx")
PY
```

## Step 2 — extract the sheet → latest-snapshot.json (deterministic)

The `daily` tab is the **live** net-worth time-series (updates ~1:15pm after
market close, day-over-day Change column, 5+ yrs history) — it is the headline
source. The `Dashboard` tab supplies composition (asset split, Mag-7
concentration, liquid cash, FIRE, ATH, milestone ladder). `extract_sheet.py`
parses BOTH from the xlsx with openpyxl — no eyeballing, no guessing:

```bash
python3 -c "import openpyxl" 2>/dev/null || pip install --user --quiet openpyxl
python3 skills/finance-radar/scripts/extract_sheet.py /tmp/finance.xlsx
```
This writes `skills/finance-radar/state/latest-snapshot.json` with: `net_worth`
(latest daily Total), `as_of`, **`day_change` / `day_change_pct`** (the sheet's
real day-over-day change), `trend_7d` (last 7 daily Totals), plus the dashboard
composition fields. Then delete `/tmp/finance.xlsx` (it holds raw financials).

## Step 3 — diff, alert, post

Run the digest (renders today's Δ + 7-day trend + composition + alerts, posts to
#finance, rotates state):
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
