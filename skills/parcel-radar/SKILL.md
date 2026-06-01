---
name: parcel-radar
description: "Tracks inbound parcels by scanning Gmail for shipment/tracking emails from delivery carriers (UPS/FedEx/USPS/DHL/OnTrac/…) AND from e-commerce stores (Shopify shops, Best Buy, Target, Apple, etc.), so you see every package in transit — its merchant origin, carrier, tracking link, status, and ETA — on one page. Excludes Amazon (covered by the amazon-orders skill). Fires out-for-delivery / delivered / exception alerts."
user-invocable: true
---

# Parcel Radar

Turns shipping-notification clutter into one live board of every package headed to you: **what** it is, **who** it's from, **which carrier**, a **1-click tracking link**, current **status**, and **ETA** — surfaced on `/parcels`.

**Usage**: `/parcel-radar` (on-demand: scan + show parcels in transit and recently delivered)

## What it does

Agent-driven Gmail scan (Gmail MCP at the agent layer — same "the scan IS the prompt" pattern as amazon-orders / trip-radar). Two complementary email sources are merged:

1. **Carrier emails** (UPS/FedEx/USPS/DHL/OnTrac/…) — authoritative *status* + tracking number, but often opaque about *what* the parcel is.
2. **E-commerce store shipment confirmations** ("your order has shipped", "on its way") — give the *merchant origin* + *item* + usually the carrier + tracking number.

Matching the two by **tracking number** yields a full picture: merchant + item (from the store) and live status + ETA (from the carrier). **Amazon is intentionally excluded** — it has its own `amazon-orders` skill.

## Files

- `scan-prompt.md` — verbatim prompt the agent runs each scan
- `state/parcels.json` — current parcel list (gitignored — personal)
- `state/history/<YYYY-MM-DD>.json` — prior-scan snapshots, for diffing (gitignored)

## Schema (`state/parcels.json`)

```json
{
  "last_scan": "ISO8601 timestamp",
  "since": "newer_than:90d window",
  "parcels": [
    {
      "id": "stable hash of tracking# (or merchant+item+shipped_date when no tracking#)",
      "merchant": "Best Buy",
      "item": "Sony WH-1000XM5 headphones",
      "carrier": "ups|fedex|usps|dhl|ontrac|lasership|other",
      "tracking": "1Z999AA10123456784",
      "tracking_url": "https://www.ups.com/track?tracknum=1Z999AA10123456784",
      "status": "ordered|shipped|in_transit|out_for_delivery|delivered|exception",
      "shipped_date": "YYYY-MM-DD or null",
      "est_delivery": "YYYY-MM-DD or null",
      "delivered_date": "YYYY-MM-DD or null",
      "last_update": "YYYY-MM-DD",
      "notes": "optional (e.g. 'delivery exception — address issue')"
    }
  ],
  "scan_history": [
    { "date": "ISO8601", "total": 0, "in_transit": 0, "delivered": 0, "added": [], "delivered_since_last": [], "exceptions": [] }
  ]
}
```

## Triggers

- **Cron**: every 6h at minute :19 — see `skills/schedule-crons/crons.json` entry `parcel-radar-scan`. Deliver only on new parcel / out-for-delivery / delivered / exception.
- **On-demand**: `/parcel-radar` → scan + show the board.
- **Voice/chat**: "what's arriving today?", "where's my package from Best Buy?", "anything out for delivery?" → read `state/parcels.json`.

## Agent actions

The skill is agent-driven and works with no web UI:
- **Scan** — run `scan-prompt.md` (on demand, via `/parcel-radar`, or the cron) to refresh `state/parcels.json`.
- **Query** — answer "what's arriving today / out for delivery / from <merchant>?" by reading `state/parcels.json`.
- **Mark delivered (manual)** — when the user knows a parcel arrived but the carrier never sent a final update, set that parcel's `status: "delivered"`, `delivered_date` to today's local date, and `delivery_source: "manual"` (so manual marks are distinguishable from carrier-confirmed ones).

## Optional web dashboard

If the host ships a web UI, it can render `state/parcels.json` as a sortable board (in transit on top, delivered below) with merchant, carrier badge, clickable tracking link, status, and ETA. In this repo, `src/web-client.ts` serves `/parcels`, a localhost-gated **"Scan now"** button (`/parcels/scan` → queues the scan task), and a localhost-gated **"delivered?"** checkbox (`/parcels/mark-delivered` → the manual-mark action above). None of this is required — the dashboard is a convenience layer over the same state file + agent actions.

## Privacy

All parsing is local (Gmail MCP on the user's own account). `state/` is gitignored — parcel/merchant/tracking data lives only on this machine. No third-party package-tracking aggregator is involved.
