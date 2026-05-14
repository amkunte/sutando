# KARTS-AIR scan-prompt

You are running a daily Cirrus SR22 acquisition scan on behalf of the owner. The scan procedure is fully prescribed below — follow it verbatim unless the criteria files in `skills/karts-air/state/` say otherwise.

## Inputs (read both before fetching anything)

1. **`skills/karts-air/state/criteria.md`** — full narrative buyer profile, payload math, 3-branch avionics map, 5 standing questions, formatting expectations. THIS IS THE SOURCE OF TRUTH for evaluation.
2. **`skills/karts-air/state/criteria.json`** — machine-readable filter shorthand (model, year range, price range, source URL list). Use for coarse pre-filtering before LLM evaluation.

## Tooling: choose tier per source

**Tier 1 — WebFetch first.** Cheap, fast, no browser overhead. Try it first on every source. If a source returns content (200 + listing-shaped HTML), proceed with extraction and skip Tier 2 for that source.

**Tier 2 — Playwright MCP fallback.** When `mcp__playwright__*` tools are available (after `claude mcp add playwright -- npx -y @playwright/mcp@latest`), use them for any source that came back 403 / Cloudflare-blocked / Datadome-blocked / empty in Tier 1. Real Chromium with full JS/TLS fingerprint beats the bot-detection that blocks WebFetch + bare curl + headless-via-`browser.mjs`.

**First-run warning (Tier 2):** Playwright downloads ~150 MB of Chromium on first invocation per machine. If `~/Library/Caches/ms-playwright/chromium-*` does not exist, expect the first `browser_navigate` call to take 30-60s. Subsequent calls are fast. Cron-triggered scans should NOT race this download — if the first navigate hangs past 60s, bail with a "Playwright cold-start in progress, scan deferred" note and let the next manual run pick up.

### Tier-2 recipe (per source)

For each source URL needing the Playwright path:

1. `mcp__playwright__browser_navigate` — load the page.
2. `mcp__playwright__browser_wait_for` — wait for either a listing-shaped element OR a Cloudflare/Datadome challenge marker, whichever lands first (use `text:` parameter to anchor on a phrase you'd expect in real results, e.g. "Cirrus SR22"). Timeout 15s.
3. `mcp__playwright__browser_snapshot` — accessibility-tree snapshot. This is the cheapest way to get structured page text; prefer it over `browser_take_screenshot` (binary blob → no extraction signal).
4. If the snapshot shows a Cloudflare/captcha page rather than listings: try `mcp__playwright__browser_console_messages` to confirm, then mark this source `"blocked-even-with-playwright"` and continue to the next source.
5. If listings are present, run the same structured-extraction prompt as Tier 1 over the snapshot text. If snapshot text is truncated (Playwright snapshots cap at ~10k chars on complex pages), follow up with `mcp__playwright__browser_evaluate` to pull the listings array directly via DOM (`document.querySelectorAll('.listing-card')` or equivalent).
6. `mcp__playwright__browser_close` when done with a source.

### Structured extraction prompt (same for both tiers)

Pass this `prompt` argument when calling WebFetch, or use it as the extraction instruction when feeding Playwright-snapshot text back to yourself:

```
For each Cirrus SR22 (G2 or G3 normally-aspirated only) listed for sale on this
page, extract: tail number / N-number, year, generation (G2/G3), exact list
price (USD), total airframe time (TT), engine SMOH hours, propeller status,
CAPS expiration if visible, location (city + ICAO if shown), and a URL to the
full listing detail page. Return as a JSON array. Skip turbo variants (SR22T).
```

### Source URLs (in order)

1. `https://www.barnstormers.com/` — start at homepage. WebFetch: try first; if it returns the search form rather than listings, navigate to the SR22 results URL. Playwright: navigate directly to the SR22 search page.
2. `https://www.aircraftforsale.com/` — search for "Cirrus SR22"; follow result-list URL.
3. `https://www.controller.com/listings/aircraft/for-sale/list?Mdltxt=SR22&Manu=CIRRUS` — preview-only fields without login.
4. `https://www.trade-a-plane.com/search?make=CIRRUS&model_group=SR-22&category_level1=Single+Engine+Piston&type=aircraft` — preview-only fields without login.

### Per-source status taxonomy (`sources_status` values)

- `ok` — listings retrieved (specify tier in notes if relevant)
- `ok-preview-only` — Controller / Trade-A-Plane fields shown without login
- `blocked` — WebFetch blocked AND Playwright unavailable this session
- `blocked-even-with-playwright` — Both tiers failed; site has stronger detection than residential Chromium
- `no-results` — Page loaded but zero SR22 G2/G3 NA listings (legit empty state, not a block)

## Coarse filter (before LLM evaluation)

Combine the raw lists. Dedupe by (tail-number) if present, else by (year + asking-price + location-city) triple. Drop listings that obviously fail any of:

- Generation not in `criteria.json.generations_target`
- Variant matches `variants_exclude` (Turbo / SR22T)
- Year outside [`year_min`, `year_max`]
- Price outside [`price_min_usd`, `price_max_usd`]
- Engine SMOH > `engine_smoh_max_hours` (if SMOH is unknown, KEEP the listing and flag for manual review)
- Location more than `preferred_radius_miles + 1000` miles from `home_zip` (1000-mile buffer to capture interesting cross-country candidates)

## Per-candidate LLM evaluation (after coarse filter)

For each surviving listing, write a full evaluation per the criteria.md "Formatting expectations" section:

1. **Summary row** — tail, year/gen, list price, total time, engine SMOH, CAPS expiry, location.
2. **Weight & balance breakdown** — extract empty weight; calculate fuel-load at max cabin (680 lbs); state real-world range if fuel must be downloaded.
3. **Avionics & infrastructure analysis** — assign to Branch 1 / Branch 2 / Branch 3 per criteria.md. Estimate downstream capital outlay to reach Branch-1 / "Ideal Target State".
4. **Pros / Cons** — engine pedigree (factory rebuild vs field OH), paint+interior, geographic delivery cost.
5. **Standing-questions notes** — for each of Q1-Q5 in criteria.md, write 1 short sentence per candidate when applicable.

Resilience: if a source page only gave you title + price (Controller/Trade-A-Plane preview fields), mark the W&B / avionics sections "INSUFFICIENT DATA — requires owner to grant site login or click through" instead of fabricating numbers.

## Persist results

Write the evaluated candidates to `skills/karts-air/state/karts-air-data.json` with this schema:

```json
{
  "last_scan": "ISO8601 timestamp (Pacific time)",
  "sources_status": {
    "barnstormers": "ok" | "blocked" | "no-results",
    "aircraftforsale": "ok" | "blocked" | "no-results",
    "controller": "ok-preview-only" | "blocked" | "no-results",
    "trade-a-plane": "ok-preview-only" | "blocked" | "no-results"
  },
  "candidates": [
    {
      "tail": "N1234AB",
      "year": 2009,
      "gen": "G3",
      "price_usd": 289900,
      "list_url": "https://...",
      "source": "barnstormers",
      "location_city": "Sacramento Mather",
      "location_icao": "KMHR",
      "total_time_hr": 2140,
      "engine_smoh_hr": 540,
      "prop_status": "concurrent OH at SMOH",
      "caps_expiry": "2027-08",
      "empty_weight_lbs": 2330,
      "useful_load_lbs": 1070,
      "branch": 1,
      "branch_rationale": "Already has Avidyne IFD540 + DFC90 + plumbed 4-place O2. Zero work required.",
      "upgrade_overhead_usd": 0,
      "pros": ["Recent factory engine RB", "WAAS upgraded", "Hangared since new"],
      "cons": ["Paint shows West Coast UV age", "No FlightStream — see Q4"],
      "q1_within_500mi_of_PAO": true,
      "q2_factory_rebuild_evidence": "Yes — see logbook entry 2024-02 (Continental factory RB)",
      "q3_branch2_ventilation": null,
      "q4_flightstream": false,
      "q5_net_after_prop_and_o2": 289900,
      "first_seen": "2026-05-13",
      "last_seen": "2026-05-13",
      "price_history": [{"date": "2026-05-13", "price_usd": 289900}]
    }
  ],
  "scan_history": [
    {
      "scan_time": "ISO8601",
      "candidates_count": 7,
      "new_tails": ["N1234AB", "N9999EF"],
      "dropped_tails": [],
      "price_changes": [{"tail": "N5678CD", "from": 250000, "to": 245000}]
    }
  ]
}
```

Snapshot the prior `karts-air-data.json` to `state/history/<previous-scan-date>.json` (skip if a snapshot from that date already exists). Update `first_seen` / `last_seen` / `price_history` per candidate by joining against the prior snapshot on tail-number (fall back to URL if no tail).

## Notify

If `scan_history[-1].new_tails.length > 0` OR `price_changes.length > 0`, write a Telegram notification to `results/proactive-karts-air-{ts}.txt`. Format per the example in `SKILL.md` "Notification format" section. Stay silent if nothing changed since the previous scan.

Always include a `View detail: <funnel-url>/KARTS-AIR` line at the bottom of the notification so the owner can click through to the live web view.

## When to bail out early (do NOT silently produce empty output)

If BOTH tiers (WebFetch AND Playwright if available) return blocked / empty from all 4 sources, write a one-line notification: `🚧 Cirrus SR22 scan: all 4 sources blocked today even with Playwright. Web page unchanged.` and stop. Do NOT crash the cron — `agent-api` keeps running.

If Tier 2 (Playwright) is NOT available in this session — i.e. `mcp__playwright__*` tools are missing from the inventory — and all 4 sources blocked in Tier 1, write `🚧 Cirrus SR22 scan: all 4 sources blocked. Playwright MCP not available this session; restart Claude Code to load it.` and update `sources_status` with `blocked` (not `blocked-even-with-playwright`).

If `criteria.md` or `criteria.json` is missing or unparseable, write a one-line notification: `🚧 Cirrus SR22 scan: criteria file unreadable at <path>. Owner to fix.` and stop.

## Manual invocation

When invoked manually via `/karts-air`, follow the same flow but ALWAYS write a notification (even if no changes) so the owner has confirmation the run completed.
