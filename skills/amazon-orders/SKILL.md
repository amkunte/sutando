# Amazon Orders

Scans Gmail for Amazon order emails since 2026-01-01 and surfaces them on `/amazon` with current delivery status. Refreshes every 6 hours.

## Architecture

Agent-driven scan (Gmail MCP access). The cron fires the prompt in `scan-prompt.md` verbatim; the agent runs the Gmail searches, dedupes by item title, takes the latest status email per order, writes `state/orders.json`.

## Files

- `scan-prompt.md` — verbatim prompt the agent runs each scan
- `state/orders.json` — current order list (gitignored — personal)
- `state/history/<YYYY-MM-DD>.json` — snapshots, for diff (also gitignored)

## Schema (`state/orders.json`)

```json
{
  "last_scan": "ISO8601 timestamp",
  "since": "2026-01-01",
  "orders": [
    {
      "id": "stable hash of item-title + ordered-date",
      "item": "truncated item title from subject",
      "channel": "amazon|whole-foods|amazon-fresh",
      "ordered_date": "YYYY-MM-DD",
      "shipped_date": "YYYY-MM-DD or null",
      "delivered_date": "YYYY-MM-DD or null",
      "status": "ordered|shipped|out_for_delivery|delivered",
      "split_shipment": false,
      "total_spent": 88.18,
      "currency": "USD",
      "returns_started_date": "YYYY-MM-DD or null",
      "refund_issued_date": "YYYY-MM-DD or null",
      "notes": "optional"
    }
  ],
  "scan_history": [
    { "date": "ISO8601", "total": <int>, "delivered": <int>, "in_transit": <int>, "ordered_only": <int>, "added": [], "delivered_since_last": [] }
  ]
}
```

## Cron

Every 6 hours at minute :13 — see `skills/schedule-crons/crons.json` entry `amazon-orders-scan`.

## Page

`/amazon` reads `state/orders.json` and renders a sortable table grouped by status (in-progress at top, delivered below). Includes a "Scan now" button that POSTs to `/amazon/scan` and writes a task file the next loop pass picks up.

## Privacy

`state/` is gitignored. Order data (item names, dates, etc.) lives only on this machine.
