# Parcel Radar — scan-prompt (follow verbatim)

Update `skills/parcel-radar/state/parcels.json` with every inbound parcel currently in transit or recently delivered, scanned from the user's Gmail. **Exclude Amazon** — it's covered by the `amazon-orders` skill (skip ALL Amazon senders — `*@amazon.*` incl. amazon.co.uk/.ca + Amazon Logistics; the query uses `-from:amazon.` so regional TLDs don't slip through).

Workspace paths resolve under `${SUTANDO_WORKSPACE:-$HOME/.sutando/workspace}` — write state to the skill's `state/` dir; deliver via `results/`.

## Scan window

Parcels are short-lived; scan `newer_than:90d` and decide "in transit vs delivered" from the email content, not the email age. Search ALL mail (`in:anywhere`) — shipment notices are often auto-archived or filed into labels.

## Phase 1 — carrier emails (authoritative status + tracking #)

Run these Gmail queries (paginate; add ` in:anywhere`):

```
from:(ups.com OR mcinfo.ups.com OR fedex.com OR usps.com OR email.informeddelivery.usps.com OR dhl.com OR mydhl.dhl.com OR ontrac.com OR lasership.com OR veho.com) subject:(shipment OR tracking OR delivery OR "on its way" OR "out for delivery" OR delivered OR package OR arriving) in:anywhere newer_than:90d
```

From each, extract: **carrier**, **tracking number**, **status** (shipped / in_transit / out_for_delivery / delivered / exception), **est_delivery** date, **last_update** date. Read the body via `get_thread` when the subject is truncated. A "delivery exception / action needed / address issue" → `status: "exception"` + a one-line `notes`.

Build `tracking_url` from the carrier + tracking number:
- UPS → `https://www.ups.com/track?tracknum=<T>`
- FedEx → `https://www.fedex.com/fedextrack/?trknbr=<T>`
- USPS → `https://tools.usps.com/go/TrackConfirmAction?tLabels=<T>`
- DHL → `https://www.dhl.com/us-en/home/tracking.html?tracking-id=<T>`
- OnTrac → `https://www.ontrac.com/tracking?number=<T>`
- LaserShip/Veho/other → best-known tracking URL, else leave `tracking_url: null`.

Infer carrier from tracking-number shape when the sender is ambiguous: UPS `1Z…` (18 char); USPS 20–22 digits or `9400…`/`9205…`; FedEx 12/15/20 digits; DHL 10–11 digits. This shape heuristic is **best-effort** (formats overlap) — a wrong guess only yields a dead `tracking_url`, never bad status; prefer the sending carrier identity when known.

## Phase 2 — e-commerce store shipment confirmations (merchant origin + item)

These tell you WHAT shipped and from WHOM (the carrier email often doesn't). Run:

```
subject:("your order has shipped" OR "has shipped" OR "on its way" OR "order is on the way" OR "shipment confirmation" OR "tracking number" OR "out for delivery" OR shipped) -from:amazon. in:anywhere newer_than:90d
from:(shopifyemail.com OR bestbuy.com OR target.com OR walmart.com OR apple.com OR nike.com OR etsy.com OR ebay.com OR chewy.com OR rei.com OR newegg.com OR bhphotovideo.com OR uniqlo.com OR wayfair.com) subject:(shipped OR shipment OR "on its way" OR tracking OR "out for delivery" OR delivered) in:anywhere newer_than:90d
```

From each store email extract: **merchant** (brand from sender domain or email branding — e.g. `bestbuy.com` → "Best Buy"; Shopify shops use the shop's name in the From display), **item** (product name from subject/body — truncate to a readable phrase), and any **carrier + tracking number** present. Skip Amazon.

**Reject promo/false-positive mail.** Subject keywords like "shipped" appear in marketing too. Only create a parcel from a store email if it contains a concrete shipment signal — a **tracking number**, OR an explicit per-order "your order #… has shipped / is on its way / out for delivery" with an order id. Discard "sale ends", "free shipping", "ships free", newsletters, and pre-shipment order *confirmations* (no tracking, status still "ordered") — those are not in-transit parcels.

## Merge the two sources

- **Match by tracking number first** — NORMALIZE before comparing (strip all non-alphanumerics + uppercase on BOTH sides; store `1Z 999 AA1…` vs carrier `1Z999AA1…` would otherwise split into two parcels; store the canonical form in `tracking`). If a Phase-2 store email and a Phase-1 carrier email share the canonical tracking #, they're ONE parcel — combine (merchant + item from the store; status + ETA from the carrier).
- A store email with a tracking # but no carrier email yet → a parcel at `shipped`/`in_transit` with merchant + item known.
- A carrier email with no matching store email → a parcel with status known but `merchant`/`item` unknown (set `merchant: "(unknown)"`, best-effort `item`).
- **Dedup id**: stable hash of the tracking number; when there's no tracking #, hash `merchant|item|shipped_date`.
- **Status precedence** when multiple emails exist for one parcel: `delivered > out_for_delivery > in_transit > shipped > ordered`; `exception` overrides unless a later `delivered` exists.
- **Carrier emails are authoritative for status/ETA.** When a carrier and a store email disagree, take status, `est_delivery`, and `last_update` from the carrier (it reflects the live network state); take `merchant` + `item` from the store. Never downgrade a carrier "out_for_delivery"/"delivered" to a store's older "shipped".
- **Preserve manual marks.** Load the existing `parcels.json` first. If a parcel there has `delivery_source: "manual"`, keep its `delivered` status + `delivered_date` + `delivery_source` even though no carrier/store email confirms delivery — the user set it deliberately on the web UI. A scan must never revert a manually-delivered parcel back to "shipped"/"in_transit".

## Diff vs previous scan

Snapshot the prior `parcels.json` to `state/history/<previous-scan-date>.json` (skip if a snapshot from that date already exists). In the new `scan_history` entry compute:
- `added`: parcels in this scan not in the previous one
- `delivered_since_last`: parcels that moved to `delivered` since last run
- `exceptions`: parcels now in `exception`

## Update state

Set `last_scan` to current ISO8601-with-timezone; write `parcels.json` (sorted: in-transit first by `est_delivery` asc, then delivered by `delivered_date` desc). Append the `scan_history` entry, then **cap `scan_history` to the last 30 entries** (per-scan snapshots already live in `state/history/`).

## Notify

If `added`, `delivered_since_last`, or `exceptions` is non-empty, deliver this update:

```
📦 Parcels update:
+N inbound: <merchant — item (carrier, ETA)>
🚚 N out for delivery: <merchant — item>
✅ N delivered: <merchant — item>
⚠️ N exception: <merchant — item — reason>
```

**Delivery — post to the Discord `#parcels` channel** (deterministic; bypasses DM/Telegram proactive routing). Resolve the channel id from `discord-config.json` and post via the bot:
```bash
CH=$(python3 -c "import json,os;from pathlib import Path;ws=os.environ.get('SUTANDO_WORKSPACE',str(Path.home()/'.sutando/workspace'));print(json.load(open(Path(ws)/'state/discord-config.json')).get('channels',{}).get('parcels',''))" 2>/dev/null)
if [ -n "$CH" ]; then python3 src/discord_post.py "$CH" "$MSG"; else printf '%s' "$MSG" > "results/proactive-parcel-radar-$(date +%s).txt"; fi
```
Post to the channel **OR** write the `results/proactive-*.txt` fallback — never both (writing both double-notifies). The channel post is the sole delivery when `#parcels` is configured; the `results/` fallback only fires when the key is absent (non-Discord setups → DM/Telegram).

If nothing changed, stay silent.
