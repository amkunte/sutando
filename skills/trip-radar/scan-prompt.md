# Trip Radar — scan-prompt (follow verbatim)

You are running Trip Radar for the owner. Two phases: **(A) sync the itinerary**, then **(B) for any newly-detected trip, generate concierge suggestions**. Read `state/travel-preferences.json` first (build it via Phase 0 if missing).

Workspace paths resolve under `${SUTANDO_WORKSPACE:-$HOME/.sutando/workspace}` — write state to the skill's `state/` dir; deliver via `results/`.

## Phase 0 — preference model (one-time build; refine each run)

If `state/travel-preferences.json` is missing OR `--rebuild-prefs` is passed, mine the user's own past travel to learn preferences. Gmail queries (last ~3 years):
```
from:(no-reply@confirmation.marriott.com OR hilton.com OR hyatt.com OR ihg.com OR booking.com OR hotels.com OR airbnb.com OR expedia.com) subject:(confirmation OR reservation OR booking OR receipt)
subject:(reservation OR "table for" OR "booking confirmed") from:(opentable.com OR resy.com OR exploretock.com OR sevenrooms.com)
```
From the hits, infer and write `travel-preferences.json` (schema below). Be conservative — only record patterns with ≥2 supporting data points; mark single-instance guesses `"confidence":"low"`. NEVER fabricate a loyalty number; only record ones that literally appear.

If prefs exist, do a light refresh: fold in any new lodging/dining signal since `last_pref_refresh`.

## Phase A — sync itinerary

**Scan scope — search ALL mail, not just the inbox.** Travel confirmations are routinely archived or filed into labels (e.g. `2026_Travel`, `Travel_Imp`, `NYC`), and a far-ahead booking's confirmation is *old* — so an inbox-only or tight-recency scan misses real upcoming trips (observed 2026-06-01: a SFO→Delhi booking filed in `2026_Travel` from 4 months prior was missed). Two rules:
1. **Cover labels + archived mail.** Append ` in:anywhere` to the queries below (includes archived + all labels; excludes only Spam/Trash). ALSO run `list_labels` and, for any label whose name matches `travel|trip|vacay|vacation|<year>` (e.g. `2026_Travel`, `Travel_Imp`), run a `label:"<name>"` sweep over the same senders/subjects.
2. **Don't gate on email recency.** Use a wide window (`newer_than:1y`) or none, and decide "upcoming" by the **travel dates inside the email**, not by when the email arrived. A confirmation booked months ago for a future trip MUST surface.

Gmail queries (paginate; add ` in:anywhere`; also sweep travel labels per rule 1):
```
from:(noreply@united.com OR delta.com OR aa.com OR alaskaair.com OR southwest.com OR jetblue.com OR ana.co.jp OR qatarairways.com OR emirates.com OR airindia.com OR aircanada.ca OR amadeus.com OR amexgbt.com OR mytrips.amexgbt.com) subject:(confirmation OR itinerary OR "e-ticket" OR "your trip" OR booking OR ticket OR boarding) in:anywhere newer_than:1y
from:(marriott.com OR hilton.com OR hyatt.com OR ihg.com OR booking.com OR hotels.com OR airbnb.com OR fourseasons.com OR tajhotels.com OR oberoihotels.com OR xanterra.com) subject:(confirmation OR reservation OR "your stay" OR receipt) in:anywhere newer_than:1y
from:(hertz.com OR avis.com OR enterprise.com OR turo.com OR amtrak.com OR trainline.com) subject:(confirmation OR reservation OR itinerary) in:anywhere newer_than:1y
label:"2026_Travel" OR label:"Travel_Imp"   (+ any travel/trip/vacay label from list_labels)
```
Also catch travel-agent/aggregator bookings (Amex GBT/TripIt/Aeroplan) and forwarded confirmations (subject `Fwd:`), which often live only in a label, never the inbox.

For each confirmation, extract: type (flight/hotel/car/train/lodging), confirmation #, provider, start/end datetimes + timezone, origin/destination (airport codes + city), and price if shown. Read the body via `get_thread` when the subject is truncated.

**Group segments into trips.** A trip = a contiguous away-from-home window. Cluster segments whose dates overlap/adjoin and whose location is the same metro. Derive `trip.destination` (primary city), `trip.start`/`trip.end`, and attach segments. Use `scripts/build_trip_state.py` to validate + merge the extracted JSON into `state/trips.json` (it preserves `trip_id`, dedups by confirmation #, and snapshots the prior file to `state/history/<date>.json`).

**Diff vs the prior snapshot** to compute:
- `new_trips` — trips not present last scan.
- `changes` — segment delays / gate / seat / cancellation / new segment added to an existing trip.

## Phase A.5 — rich itinerary ingestion (docs, sheets, operator emails)

Airline/hotel confirmations only give the skeleton. The DETAILED itinerary for a tour/road-trip (day-by-day route, overnight stops, named hotels, the operator's plan) usually lives in **a Google Doc/Sheet or a tour-operator email**, not a standard confirmation — and is exactly what users care about. For each upcoming trip, also look there:

1. **Google Drive** — `mcp__claude_ai_Google_Drive__search_files` for docs/sheets matching the destination/operator/dates (e.g. `fullText contains 'Ladakh'`, `title contains '<destination>'`, mimeType doc/spreadsheet). Read the match with `read_file_content` (it renders Docs, Sheets, AND PDFs). Extract the day-by-day plan.
2. **Operator / agent / aggregator emails** — search beyond airline/hotel senders: the tour operator (often a plain gmail/business address), Amex GBT, TripIt, "itinerary"/"confirmed itinerary" subjects. These carry the schedule + named lodging.
3. **PDF e-tickets** — Air India and many carriers put the actual flight times ONLY in a PDF attachment; the email body is just "ticket attached." The Gmail tools here CANNOT read attachment bytes — so do NOT guess those dates. Either (a) read the same e-ticket if it's also saved in Drive (`read_file_content` handles PDF), or (b) mark the segment dates "verify" and ask the owner. Never fabricate flight times.

Persist the rich plan onto the trip:
- `trip.tour` = `{operator, name, dates, route, max_altitude, distance, bike, lodging, doc(view-url)}`
- `trip.road_itinerary` = `[{date, stop, detail}, …]` (day-by-day). (An optional web dashboard renders both.)

## Phase B — concierge suggestions (only for `new_trips`, or on-demand for a named destination)

For each new trip, using `travel-preferences.json` + the destination + dates + trip purpose (infer business vs leisure from segment mix / day-of-week / past patterns), research and assemble:

- **🏨 Hotels (3)** — match the user's lodging prefs (brand/area/tier/amenities). Prefer their loyalty chains. For each: name, neighborhood, ~nightly price, why-it-fits (1 line), booking link.
- **🍽 Restaurants (4–6)** — match dining prefs (cuisine/price/vibe/dietary). Favor **places they haven't been** (cross-check past reservation history) and current standouts. For each: name, cuisine, neighborhood, why, reservation link if available.
- **🎟 Activities/tours (1–3, optional)** — match the activity profile + trip length; skip if a tight business trip.

Use WebSearch/WebFetch for current, real options (never invent venues or prices — if unsure, say "verify"). Keep it tight and skimmable.

**Persist the suggestions onto the trip** (so they survive beyond the ephemeral delivery message and can be re-surfaced later or rendered by an optional web dashboard): write them into that trip's `suggestions` field in `state/trips.json` as:
```json
"suggestions": {
  "generated_at": "<ISO date you researched these>",
  "hotels":      [{"name": "...", "url": "...", "area": "...", "price": "$$$", "why": "..."}],
  "restaurants": [{"name": "...", "url": "...", "area": "...", "cuisine": "...", "why": "..."}],
  "activities":  [{"name": "...", "url": "...", "why": "..."}]
}
```
(When present, `url` makes `name` a link; `area`/`cuisine`/`price` are display meta alongside the `why` line. `generated_at` lets the UI/agent flag stale prices/availability — re-research if it's weeks old before re-surfacing.)

## Deliver

- **New trip detected** → write `results/proactive-tripradar-{epoch}.txt`:
  ```
  ✈️ Trip detected: <Destination> <start>–<end> (<purpose>)
  🏨 Hotels: <3 with 1-line why + link>
  🍽 Restaurants: <4–6 with link>
  🎟 Do: <0–3>
  (Reply "more hotels" / "cheaper" / "I prefer X" to refine — I'll learn it.)
  ```
- **Change detected** (delay/gate/cancel) → concise proactive alert.
- **Imminent check-in** (flight <24h, no check-in seen) → reminder with airline + conf #.
- **Nothing new / no change** → stay silent (write nothing). On-demand `/trip-radar` always replies (show upcoming itinerary even if unchanged).

## Refinement = learning

When the owner reacts ("too pricey", "I love sushi", "always book Hyatt", "hated that neighborhood"), update `travel-preferences.json` accordingly and confirm briefly — that's the flywheel that makes suggestions sharper each trip.
