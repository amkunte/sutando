---
name: mac-mini-deal-finder
description: Scan Craigslist (SF Bay) for used Mac mini listings matching the owner's criteria (M2+, 16GB+ RAM, 512GB+ SSD, ≤$500, local pickup near 94566). Notify owner via SMS + Telegram on a match.
user-invocable: true
---

# Mac Mini Deal Finder

Watches Craigslist SF Bay for Mac mini listings matching the owner's criteria. Sends an SMS + Telegram DM when a new matching listing appears.

**Usage**: `/mac-mini-deal-finder` (one-shot scan) — typically run from cron every 15 min.

## Criteria

Read from `state/criteria.json`. Defaults:

- Chip family: `M2`, `M3`, `M4` (M1 explicitly excluded — owner asked for M2+)
- Min RAM: `16 GB`
- Min storage: `512 GB`
- Max price: `$500` (owner stated "incl. shipping" — local pickup means no shipping, so price is total)
- ZIP: `94566` (Pleasanton, CA), search radius `50 mi`
- Listing must mention "local pickup" OR be inside the SF Bay region (Craigslist's `sfbay` already filters by region)

Edit the JSON to retune.

## Sources

V1: Craigslist SF Bay only (`sfbay.craigslist.org/search/sss?...`).

V2 (planned, not in this skill yet):
- OfferUp — login-required scrape via Playwright
- Facebook Marketplace — same
- eBay — has local-pickup filter; HTML scraping works

## Behavior

1. Fetch the Craigslist search results page with a browser User-Agent.
2. Parse out each listing: title, URL, price, neighborhood, post date.
3. For each listing not in `state/seen.json`:
   - Fetch the listing detail page (one extra HTTP req per listing — cheap, only on first sight).
   - Apply criteria filter (chip, RAM, storage, price). Listings missing fields fall through to "soft match" — flagged but still notified, since Craigslist sellers often omit specs.
   - On match: notify (SMS + Telegram), record URL in `seen.json`.
4. Trim `seen.json` to the last 1000 entries.

## Notification format

```
[Mac Mini Deal] $480 — M2 / 16GB / 512GB
Concord (35mi, local pickup)
https://sfbay.craigslist.org/...
Posted 2h ago
```

SMS goes via `TWILIO_*` env vars to `OWNER_NUMBER`. Telegram goes via `results/proactive-{ts}.txt` (the bridge picks it up).

## Run it

```bash
python3 scripts/scan.py            # one-shot scan with default criteria
python3 scripts/scan.py --dry-run  # don't notify, print what would notify
python3 scripts/scan.py --reset    # clear seen.json (force re-notify everything)
```

Cron: every 15 min. Added to `skills/schedule-crons/crons.json`.
