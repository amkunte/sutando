# Amazon orders scan — prompt body

Update `skills/amazon-orders/state/orders.json` with the latest Amazon orders since 2026-01-01.

## Searches

Use these Gmail queries (paginate as needed):

```
from:(auto-confirm@amazon.com OR shipment-tracking@amazon.com OR order-update@amazon.com) after:2026/01/01
```

**Important:** do NOT search before 2026/01/01 even if pagination would let you. Owner has set this as the explicit history boundary.

## Per-message extraction

For each message returned, extract from the subject line:
- The item name (the part inside quotes after "Ordered:" / "Shipped:" / "Delivered:" — truncated by Amazon)
- Whether it's an Amazon order, Whole Foods Market, or Amazon Fresh (look at sender + subject prefix)
- The status (Ordered / Shipped / Out for delivery / Delivered)
- The date

For multi-item orders (subjects ending in "and N more items"), use the first item name as the canonical title and add `"split_shipment": true` if multiple "Shipped" emails exist for the same first-item title.

## Dedupe and merge

Group by `(item-title, channel, ordered_date)`. For each group:
- `ordered_date`: from the "Ordered:" email (or first email seen)
- `shipped_date`: from the latest "Shipped:" email; null if not yet shipped
- `delivered_date`: from the "Delivered:" email; null if not yet delivered
- `status`: highest reached state — `delivered > out_for_delivery > shipped > ordered`

Whole Foods / Amazon Fresh orders don't have item names in the subject — use the date as the disambiguator (one order per day per channel).

## Diff vs previous scan

Snapshot the prior `orders.json` to `state/history/<previous-scan-date>.json` (skip if a snapshot from that date already exists). In the new `scan_history` entry, compute:
- `added`: orders in this scan not in previous (newly ordered since last run)
- `delivered_since_last`: orders that moved from `shipped` / `out_for_delivery` to `delivered` since last run

## Update state

Set `last_scan` to current ISO8601-with-timezone, write the updated `orders.json`. Append a `scan_history` entry.

## Notify

If `added.length > 0` OR `delivered_since_last.length > 0`, write a brief Telegram notification to `results/proactive-amazon-orders-{ts}.txt`:

```
📦 Amazon orders update:
+N newly ordered: <item titles>
✅ N just delivered: <item titles>
```

If nothing changed, stay silent.
