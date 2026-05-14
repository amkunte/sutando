---
name: karts-air
description: "Daily Cirrus SR22 deal-finder. Scrapes 4 aviation classifieds (Barnstormers, aircraftforsale.com, Controller, Trade-A-Plane), applies an executive buyer profile (G2/G3 SR22, $200–350k, mountain-mission payload math, CAPS-currency, 3-branch avionics evaluation), and surfaces matches via Telegram + a live web page at /KARTS-AIR."
user-invocable: true
---

# KARTS-AIR — Cirrus SR22 Deal Hunter

Daily search for used Cirrus SR22 airframes matching the owner's buyer profile. Modeled on the existing [deal-finder](../deal-finder/) skill, but adapted to the aviation classifieds market where (a) per-listing data is partially behind logins and (b) the buyer-profile evaluation needs LLM reasoning over each candidate (3-branch avionics analysis + payload math + 5 standing questions).

## Sources (v1)

| Site | Access | Notes |
|---|---|---|
| Barnstormers | Free | Static-ish HTML, full specs in body |
| aircraftforsale.com | Free | Free, decent HTML structure |
| Controller.com | Free preview | Per-listing detail mostly paywalled; can extract price, year, total time, location |
| Trade-A-Plane | Free preview | Similar to Controller — coarse fields free, detail paywalled |

Owner can grant cookie-jar credentials to unlock paywalled fields on the latter two — see `state/credentials.example.json` for the format. v1 runs without credentials and accepts reduced detail.

## Architecture

The skill follows the same pattern as `amazon-orders` and `subscription-scanner`: a cron-fired prompt asks the agent to do the work. The agent uses WebFetch (or Playwright MCP for JS-rendered pages) to retrieve listings at runtime, applies the criteria filter, evaluates matches with LLM reasoning, and writes results to `state/karts-air-data.json`. The web page reads that file live.

The reason this skill does NOT bake in a deterministic Python scraper (unlike `deal-finder/scan.py`) is that aviation sites are heterogeneous, JS-heavy, and anti-scrape; an agent with browser-grade tool access at runtime adapts to layout changes the way a static parser cannot.

## Files

- `scan-prompt.md` — the prompt body the 5AM cron passes to the agent. Verbatim instructions for: which URLs to fetch, how to filter, how to evaluate, where to write results, when to notify.
- `state/criteria.md` — **personalize before first use**. Human-readable narrative criteria (buyer profile, mission geometry, target metrics, 3 avionics branches, 5 standing questions, formatting expectations). Sourced from owner's 2026-05-13 spec.
- `state/criteria.json` — machine-readable filter shorthand (model, year-range, price-range, location-radius). The agent uses this for the coarse filter before per-listing LLM evaluation.
- `state/karts-air-data.json` — output. Latest scan's matching airframes. **Gitignored** (per-user financial / personal-buy data).
- `state/history/<YYYY-MM-DD>.json` — daily snapshots for diff highlighting.

## Web page

Live route `/KARTS-AIR` in `src/web-client.ts` reads `state/karts-air-data.json` on each GET and renders a sortable Markdown table with diff highlights (vs yesterday's snapshot). No build step needed.

## Cron

Daily at 05:00 PT (off-peak, before owner's morning briefing) — see `skills/schedule-crons/crons.json` entry `karts-air-scan`.

`crons.json` is gitignored (per-user). Add this entry manually after cloning:

```json
{
  "name": "karts-air-scan",
  "cron": "0 5 * * *",
  "prompt": "Run the Cirrus SR22 deal-hunter scan. Read the full instructions in skills/karts-air/scan-prompt.md and follow them verbatim. Update skills/karts-air/state/karts-air-data.json with the latest matching airframes, snapshot the previous version to state/history/, and write a proactive Telegram notification to results/proactive-karts-air-{ts}.txt only if NEW matching airframes appeared since the previous scan or existing tracked aircraft changed price/status. Stay silent if nothing changed."
}
```

Bump to `*/30 * * * *` during development to trigger every 30 minutes.

## Manual invocation

```
/karts-air
```

Runs the same flow as the cron — useful for testing changes to `criteria.md` or `scan-prompt.md`.

## Editing criteria

Edit `skills/karts-air/state/criteria.md` for narrative/qualitative changes (mission profile, avionics priorities, 5 standing questions). Edit `state/criteria.json` for machine-readable filters (model list, year range, price max). The scan-prompt cross-references both.

## Notification format

`results/proactive-karts-air-{ts}.txt` (Telegram, on changes only):

```
✈️ Cirrus SR22 hunt — 2 new candidates:

1. N1234AB — 2009 SR22 G3 GTSX Turbo — $289,900
   Sacramento Mather (KMHR) · 2,140 TT · 540 SMOH
   Branch 1 (turn-key). CAPS due 2027-08. Payload OK at full fuel.
   Top concerns: paint age. URL: …

2. N5678CD — 2008 SR22 G3 — $245,000
   Reno Stead (KRTS) · 1,890 TT · 1,120 SMOH
   Branch 2 (tweener). Engine near TBO — $40k overhead within 18 months.
   …

(0 dropped from yesterday, 1 price-change on N9999EF.)
View detail: <funnel-url>/KARTS-AIR
```
