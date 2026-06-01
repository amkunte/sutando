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

Gmail queries (paginate; default window: `newer_than:400d` so you catch trips booked far ahead):
```
from:(noreply@united.com OR delta.com OR aa.com OR alaskaair.com OR southwest.com OR jetblue.com OR flyfrontier.com OR ana.co.jp OR qatarairways.com OR emirates.com OR airindia.com) subject:(confirmation OR itinerary OR "e-ticket" OR "your trip" OR "boarding")
from:(marriott.com OR hilton.com OR hyatt.com OR ihg.com OR booking.com OR hotels.com OR airbnb.com) subject:(confirmation OR reservation OR "your stay" OR receipt)
from:(hertz.com OR avis.com OR enterprise.com OR turo.com OR amtrak.com OR trainline.com) subject:(confirmation OR reservation OR itinerary)
```
For each confirmation, extract: type (flight/hotel/car/train/lodging), confirmation #, provider, start/end datetimes + timezone, origin/destination (airport codes + city), and price if shown. Read the body via `get_thread` when the subject is truncated.

**Group segments into trips.** A trip = a contiguous away-from-home window. Cluster segments whose dates overlap/adjoin and whose location is the same metro. Derive `trip.destination` (primary city), `trip.start`/`trip.end`, and attach segments. Use `scripts/build_trip_state.py` to validate + merge the extracted JSON into `state/trips.json` (it preserves `trip_id`, dedups by confirmation #, and snapshots the prior file to `state/history/<date>.json`).

**Diff vs the prior snapshot** to compute:
- `new_trips` — trips not present last scan.
- `changes` — segment delays / gate / seat / cancellation / new segment added to an existing trip.

## Phase B — concierge suggestions (only for `new_trips`, or on-demand for a named destination)

For each new trip, using `travel-preferences.json` + the destination + dates + trip purpose (infer business vs leisure from segment mix / day-of-week / past patterns), research and assemble:

- **🏨 Hotels (3)** — match the user's lodging prefs (brand/area/tier/amenities). Prefer their loyalty chains. For each: name, neighborhood, ~nightly price, why-it-fits (1 line), booking link.
- **🍽 Restaurants (4–6)** — match dining prefs (cuisine/price/vibe/dietary). Favor **places they haven't been** (cross-check past reservation history) and current standouts. For each: name, cuisine, neighborhood, why, reservation link if available.
- **🎟 Activities/tours (1–3, optional)** — match the activity profile + trip length; skip if a tight business trip.

Use WebSearch/WebFetch for current, real options (never invent venues or prices — if unsure, say "verify"). Keep it tight and skimmable.

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
