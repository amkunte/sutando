# KARTS-AIR scan-prompt

You are running a daily Cirrus SR22 acquisition scan on behalf of the owner. The scan procedure is fully prescribed below — follow it verbatim unless the criteria files in `skills/karts-air/state/` say otherwise.

## Inputs (read both before fetching anything)

1. **`skills/karts-air/state/criteria.md`** — full narrative buyer profile, payload math, 3-branch avionics map, 5 standing questions, formatting expectations. THIS IS THE SOURCE OF TRUTH for evaluation.
2. **`skills/karts-air/state/criteria.json`** — machine-readable filter shorthand (model, year range, price range, source URL list). Use for coarse pre-filtering before LLM evaluation.

## Source list (v1)

Fetch each via WebFetch with a `prompt` argument that asks for **structured listing extraction**, not free-form summary:

```
For each Cirrus SR22 (G2 or G3 normally-aspirated only) listed for sale on this
page, extract: tail number / N-number, year, generation (G2/G3), exact list
price (USD), total airframe time (TT), engine SMOH hours, propeller status,
CAPS expiration if visible, location (city + ICAO if shown), and a URL to the
full listing detail page. Return as a JSON array. Skip turbo variants (SR22T).
```

Source URLs (in order):

1. `https://www.barnstormers.com/` — start at homepage, navigate via WebFetch redirect to the Cirrus SR22 search results.
2. `https://www.aircraftforsale.com/` — search for "Cirrus SR22"; follow result-list URL.
3. `https://www.controller.com/listings/aircraft/for-sale/list?Mdltxt=SR22&Manu=CIRRUS` — preview-only fields without login.
4. `https://www.trade-a-plane.com/search?make=CIRRUS&model_group=SR-22&category_level1=Single+Engine+Piston&type=aircraft` — preview-only fields without login.

If WebFetch returns 403 / Cloudflare-blocked for one of these, log the source as "blocked this scan" in the output and continue with the others. Do NOT crash the whole scan on one source failure.

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

If `WebFetch` returns 403 / Cloudflare-blocked from all 4 sources, write a one-line notification: `🚧 Cirrus SR22 scan: all 4 sources blocked today. Web page unchanged.` and stop. Do NOT crash the cron — `agent-api` keeps running. Owner will see the blocked-everywhere notification and can intervene.

If `criteria.md` or `criteria.json` is missing or unparseable, write a one-line notification: `🚧 Cirrus SR22 scan: criteria file unreadable at <path>. Owner to fix.` and stop.

## Manual invocation

When invoked manually via `/karts-air`, follow the same flow but ALWAYS write a notification (even if no changes) so the owner has confirmation the run completed.
