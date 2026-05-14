---
name: karts-air
description: "Daily Cirrus SR22 deal-finder. Scrapes 4 aviation classifieds (Barnstormers, aircraftforsale.com, Controller, Trade-A-Plane), applies an executive buyer profile (G2/G3 SR22, $200–350k, mountain-mission payload math, CAPS-currency, 3-branch avionics evaluation), and surfaces matches via Telegram + a live web page at /KARTS-AIR."
user-invocable: true
---

# KARTS-AIR — Cirrus SR22 Deal Hunter

Daily search for used Cirrus SR22 airframes matching the owner's buyer profile. Modeled on the existing [deal-finder](../deal-finder/) skill, but adapted to the aviation classifieds market where (a) per-listing data is partially behind logins and (b) the buyer-profile evaluation needs LLM reasoning over each candidate (3-branch avionics analysis + payload math + 5 standing questions).

## Sources (v1)

| Site | Public data | Bot detection (verified 2026-05-13) |
|---|---|---|
| Barnstormers | Full specs in body, free | WebFetch & curl get 403; real Chromium reaches search form but not result listings |
| aircraftforsale.com | Decent HTML, free | WebFetch & curl get 403; needs Playwright |
| Controller.com | Coarse fields free, detail paywalled | WebFetch & curl get 403; needs Playwright |
| Trade-A-Plane | Coarse fields free, detail paywalled | WebFetch & curl get 403; needs Playwright |

All 4 sites use Cloudflare or Datadome fingerprint-grade detection (TLS handshake, JS challenges, IP-type filtering — not just UA). `scan-prompt.md` uses a two-tier approach: WebFetch first (in case the detection ever relaxes), Playwright MCP fallback as the real path. See PR #20 for the recipe.

Owner can grant cookie-jar credentials to unlock paywalled fields on Controller / Trade-A-Plane — see `state/credentials.example.json` for the format. v1 runs without credentials and accepts reduced detail.

## Architecture

The skill follows the same pattern as `amazon-orders` and `subscription-scanner`: a cron-fired prompt asks the agent to do the work. The agent first tries WebFetch (cheap, fast), then falls back to Playwright MCP for any source the bot-detection blocked. It applies the criteria filter, evaluates matches with LLM reasoning, and writes results to `state/karts-air-data.json`. The web page reads that file live.

The reason this skill does NOT bake in a deterministic Python scraper (unlike `deal-finder/scan.py`) is twofold: (a) aviation sites have heterogeneous layouts an agent adapts to; (b) the heavy bot detection makes Python `requests` useless without a residential-Chromium runtime — which Playwright MCP provides for free to the agent at scan time.

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
