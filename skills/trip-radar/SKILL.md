---
name: trip-radar
description: "Auto-aggregates flight/hotel/car/train confirmations from email into a live itinerary, and — when it detects a NEW trip — proactively suggests hotels, restaurants, and activities tuned to your destination and your own past-travel preferences. Also fires check-in and flight-status reminders."
user-invocable: true
---

# Trip Radar

Turns the travel-confirmation clutter in your inbox into (1) a clean live itinerary and (2) a proactive concierge: the moment a new trip shows up, Sutando tells you *"NYC Jun 12–15 — based on your history, here are 3 hotels, 5 restaurants, and 2 activities worth booking."*

**Usage**: `/trip-radar` (on-demand: show upcoming trips + refresh suggestions)

## What it does

Two cooperating loops:

1. **Itinerary sync** — parse flight / hotel / car / train / Airbnb confirmation emails into `state/trips.json` (trips, each with dated segments). Runs daily + on demand. Diffing against the prior scan surfaces **new trips** and **changes** (delays, gate/seat changes, cancellations).
2. **Concierge suggestions** — when a *new* trip is detected, derive the traveler's preferences from their own past-travel history and research destination-specific, taste-matched **hotels / restaurants / activities**, delivered as one proactive message. This is the part that makes it feel magic, not just a parser.

## Why it fits Sutando

- Leans on the **Gmail MCP** (read the user's own mail) + **proactive-loop** (detect + push) + **voice/Telegram** delivery — all existing infrastructure.
- The preference model is **learned from the user's own data**, and refined over time (and via "learn from demonstration" corrections) — a durable, sticky capability rather than a generic search.

## The preference model (`state/travel-preferences.json`)

The clever, sticky part. Built once by mining past travel emails, then incrementally refined every scan (and editable by the user — corrections are honored):

- **Lodging** — preferred chains/brands, neighborhood/area patterns, price tier, must-have amenities (gym, lounge, kitchen), loyalty programs.
- **Dining** — cuisines, price level, vibe (fine-dining vs casual vs hole-in-the-wall), dietary constraints, reservation habits.
- **Activities** — museums / outdoors / nightlife / food-tours mix; pace (packed vs relaxed).
- **Logistics** — preferred airlines, seat, rental-car brand, loyalty numbers.

Stored locally in the workspace; never leaves the machine.

## Triggers

- **Daily cron** (recommended `*/41 6 * * *` or similar off-peak; add to `schedule-crons`): run the scan-prompt; deliver only on new-trip / material-change / imminent-checkin.
- **On-demand**: `/trip-radar` → show upcoming itinerary + (re)generate suggestions for the next trip.
- **Voice/chat**: "what's my next trip?", "suggest restaurants for Miami", "where should I stay in NYC?" → read `state/trips.json` + run the suggestion step for the named destination.

## Reminders it fires

- **Check-in** — ~24h before each flight (airline + confirmation #).
- **Day-of status** — flight delay / gate / cancellation (free aviation status source).
- **Pre-trip prep** — a packing/logistics nudge 2 days out (weather at destination + any visa/TSA notes).

## State

- `state/trips.json` — current + recent trips (example shape in `state/trips.example.json`).
- `state/travel-preferences.json` — the learned preference model.
- `state/history/<date>.json` — prior-scan snapshots (diffing).

## How to run

The procedure is fully prescribed in **`scan-prompt.md`** — the agent follows it verbatim (same "the scan IS the prompt" pattern as morning-briefing / subscription-scanner: Gmail access lives at the agent layer, light state on disk, no heavy Python). `scripts/build_trip_state.py` is a thin helper that validates + merges the structured trip data the agent extracts.

## Privacy

All email parsing is local (Gmail MCP on the user's own account); the preference model and itineraries live only in the workspace. No third-party trip aggregator is involved.

## Per-trip chat & document corpus

Each trip keeps a document corpus under `state/corpus/<trip_id>/`:
- **Chat about a trip** — on request ("must-visit in Nubra?", "authentic parathas in Delhi?"), answer using that trip's record + its corpus + web research.
- **Ingest a document** — when the user supplies a PDF/image/doc (e.g. a PDF-only e-ticket the Gmail tools can't read), save it under `state/corpus/<trip_id>/` and extract its facts with the **Read tool** (Read natively handles PDFs + images — do NOT text/utf-8-parse) into `state/corpus/<trip_id>/_corpus.md`, and when it carries booking/itinerary detail, into the trip's record in `state/trips.json`.

*Optional web dashboard:* a host web UI can surface these as a per-trip chat box + 📎 attach control (in this repo, `src/web-client.ts` wires `/trips/chat`, `/trips/upload`, and a `/trips/chat-result` poll, dispatching `from: trip-chat` / `from: trip-ingest` tasks). The skill is fully usable without any web UI — these are agent capabilities, not web-only.

## Calendar sync — timezone-correct, idempotent

On request, cross-check each flight/hotel segment against the user's Google Calendar (Calendar MCP) and (1) create any MISSING events, (2) correct any with wrong times — always with an explicit `timeZone` matching the segment's offset region (e.g. +05:30 → `Asia/Kolkata`), because Gmail's auto-add frequently mislabels timezones or drops legs.

Idempotency is **structural**, not prompt-dependent: `build_trip_state.py` stamps a deterministic `uid` on every segment, and each synced event carries a hidden `[trip-radar:<uid>]` marker in its description. On every sync, match a segment to its event by that exact marker FIRST (then fall back to flight# + departure date to adopt pre-existing Gmail auto-events), create only truly-missing segments (embedding the marker + the segment's OWN confirmation# verbatim — never another segment's PNR), correct mismatches, and leave already-correct events untouched. Never delete unrelated events. Re-running is a no-op.

*Optional web dashboard:* a host web UI can expose this as a per-trip "🗓 Check & sync to Calendar" button (in this repo, `/trips/calendar-sync` dispatches a `from: trip-calsync` task whose body prescribes the match→create/fix steps above). Not required — the sync is an agent capability.
