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

- `state/trips.json` — current + recent trips (schema in `scripts/trips.schema.json`).
- `state/travel-preferences.json` — the learned preference model.
- `state/history/<date>.json` — prior-scan snapshots (diffing).

## How to run

The procedure is fully prescribed in **`scan-prompt.md`** — the agent follows it verbatim (same "the scan IS the prompt" pattern as morning-briefing / subscription-scanner: Gmail access lives at the agent layer, light state on disk, no heavy Python). `scripts/build_trip_state.py` is a thin helper that validates + merges the structured trip data the agent extracts.

## Privacy

All email parsing is local (Gmail MCP on the user's own account); the preference model and itineraries live only in the workspace. No third-party trip aggregator is involved.

## Per-trip chat & document corpus (web)

The `/trips` page gives each trip a chat box + 📎 attach control:
- **Chat** — ask anything about that trip ("must-visit in Nubra?", "authentic parathas in Delhi?"). The page POSTs to `/trips/chat`; the core agent answers async using that trip's record + its corpus + web research, writing to `results/tripchat-<id>.txt` (the page polls `/trips/chat-result`). Not delivered via any bridge.
- **Attach** — upload a PDF/image/doc; it's saved to `state/corpus/<trip_id>/` (base64 JSON, no multipart) and an ingest task extracts its facts (Read handles PDF+images) into `state/corpus/<trip_id>/_corpus.md` and, when it carries booking/itinerary detail, into the trip's record. This is how PDF-only e-tickets (which the Gmail tools can't read) get into the trip.

When you (core) pick up a `from: trip-chat` or `from: trip-ingest` task, follow its self-describing body verbatim and write the result to the `results/tripchat-<id>.txt` path it names.

## Calendar sync (web) — timezone-correct

Each upcoming trip on `/trips` has a **🗓 Check & sync to Calendar** button. It POSTs to `/trips/calendar-sync`; the core agent then uses the Google Calendar MCP to (1) cross-check every flight/hotel segment against the calendar, (2) create any MISSING ones, and (3) correct any with wrong times — always with an explicit `timeZone` matching the segment's offset region (e.g. +05:30 → `Asia/Kolkata`), because Gmail's auto-add frequently mislabels timezones / drops legs. Summary is written to `results/tripchat-<id>.txt` (page polls). When you (core) pick up a `from: trip-calsync` task, follow its body: list_events to match, create_event/update_event to fix, never delete unrelated events, write the per-segment summary. Put each segment's OWN confirmation number (verbatim from trips.json) in the event description — never reuse another segment's PNR (an international booking's PNR is not the domestic legs' conf#).
