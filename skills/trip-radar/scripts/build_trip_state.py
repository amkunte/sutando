#!/usr/bin/env python3
"""Validate + merge agent-extracted trips into state/trips.json, and emit the
diff (new trips / changes) vs the prior scan.

The agent (per scan-prompt.md) extracts travel confirmations from Gmail and
writes them as a JSON array of trips to a temp file; this thin helper does the
deterministic part: dedup by confirmation number, preserve stable trip_ids,
snapshot the prior state for history/diffing, and print what's NEW or CHANGED
so the agent knows whether to deliver a proactive message.

Usage:
    python3 build_trip_state.py <extracted_trips.json>
        merge the extracted file into state/trips.json; print diff summary.
    python3 build_trip_state.py --show
        print the current upcoming itinerary (no write).

Trip shape (see state/trips.example.json):
    {"destination","start","end","purpose","segments":[
        {"type","provider","confirmation","start","end","from","to","price","status"}]}

py39-safe: `from __future__ import annotations`, timezone.utc (not datetime.UTC).
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
STATE = SKILL_DIR / "state"
TRIPS = STATE / "trips.json"
HISTORY = STATE / "history"


def _load(path: Path) -> dict:
    if not path.exists():
        return {"trips": [], "last_scan": None}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {"trips": [], "last_scan": None}


def _leg_route(seg: dict) -> str:
    """A route/flight signature that distinguishes the legs of one booking.
    Prefers explicit origin/destination; falls back to a flight code parsed
    from the provider/name (e.g. 'Air India AI2479 (First)' → 'AI2479') so the
    two domestic legs of a round-trip that share a confirmation# and carry NO
    from/to fields don't collapse to the same key.
    """
    o = seg.get("from") or seg.get("origin") or ""
    d = seg.get("to") or seg.get("destination") or ""
    if o or d:
        return f"{o}>{d}"
    m = re.search(r"[A-Z]{2}\s?\d{2,4}", seg.get("provider") or seg.get("name") or "")
    return m.group(0).replace(" ", "") if m else ""


def _seg_key(seg: dict) -> str:
    """Stable identity of a *leg*. Includes the route/flight signature, not the
    date, so:
    - the two legs of a round-trip (same confirmation#, different route) stay
      DISTINCT (keying on conf# alone collided them into one),
    - a reschedule (same conf# + route, new date) keeps the same key → reads as
      a status/time change, not a drop + add.
    When conf# is present but no route/flight signature can be derived, the start
    date is the last-resort differentiator (rare; only when both are missing).
    """
    conf = (seg.get("confirmation") or "").strip().upper()
    route = _leg_route(seg)
    if conf and route:
        return f"conf:{conf}|{seg.get('type')}|{route}"
    if conf:
        return f"conf:{conf}|{seg.get('type')}|{(seg.get('start') or '')[:10]}"
    return f"{seg.get('type')}|{(seg.get('start') or '')[:10]}|{route}"


def _trip_anchor(trip: dict) -> str:
    """A RESCHEDULE-STABLE trip identity for the calendar uid prefix.

    NOT `trip_id`: `trip_id` embeds `trip.start`, so if the first segment is
    rescheduled to another day, `trip_id` changes → every segment uid changes →
    the `[trip-radar:<uid>]` markers stop matching existing calendar events →
    the sync creates DUPLICATES. Anchor on the lexically-smallest confirmation#
    across the trip's segments (stable when a date moves), falling back to the
    destination slug when no conf# exists.
    """
    confs = sorted(_conf_set(trip))
    return confs[0].lower() if confs else _slug(trip.get("destination"))


def _seg_uid(trip: dict, seg: dict) -> str:
    """Deterministic, reschedule-stable per-segment id used as the Google
    Calendar dedup marker. Same segment → same uid across runs (and across date
    reschedules), so the calendar sync matches an existing event structurally
    (by `[trip-radar:<uid>]` in its description) instead of fuzzy matching."""
    return "tr-" + _slug(_trip_anchor(trip)) + "-" + _slug(_seg_key(seg))


def _slug(s: str) -> str:
    out = "".join(c if c.isalnum() else "-" for c in (s or "?").lower())
    while "--" in out:
        out = out.replace("--", "-")
    return out.strip("-") or "trip"


def _trip_id(trip: dict) -> str:
    return f"{_slug(trip.get('destination'))}-{trip.get('start','?')[:10]}"


def _conf_set(trip: dict) -> set:
    return {(s.get("confirmation") or "").strip().upper()
            for s in trip.get("segments", []) if (s.get("confirmation") or "").strip()}


def _date_overlap(a: dict, b: dict) -> bool:
    """True if two trips share a metro AND their [start,end] date ranges overlap."""
    if _slug(a.get("destination")) != _slug(b.get("destination")):
        return False
    a0, a1 = (a.get("start") or "")[:10], (a.get("end") or a.get("start") or "")[:10]
    b0, b1 = (b.get("start") or "")[:10], (b.get("end") or b.get("start") or "")[:10]
    return a0 <= b1 and b0 <= a1


def _is_future(seg: dict) -> bool:
    return (seg.get("start") or "")[:10] >= datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _match_prior(trip: dict, priors: list, used: set):
    """Find the prior trip this extraction refers to. Match on a shared
    confirmation# first (stable across reschedules), then fall back to
    same-metro + overlapping dates. Returns (index, prior) or None."""
    ec = _conf_set(trip)
    if ec:
        for i, p in enumerate(priors):
            if i not in used and (_conf_set(p) & ec):
                return i, p
    for i, p in enumerate(priors):
        if i not in used and _date_overlap(trip, p):
            return i, p
    return None


def merge(extracted: list[dict], prior: dict) -> tuple[dict, list, list]:
    priors = prior.get("trips", [])
    used: set = set()
    merged: list[dict] = []
    new_trips: list[str] = []
    changes: list[str] = []
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    for trip in extracted:
        m = _match_prior(trip, priors, used)
        if m is None:
            trip["trip_id"] = _trip_id(trip)
            trip["first_seen"] = today
            new_trips.append(trip["trip_id"])
        else:
            i, old = m
            used.add(i)
            # reuse the prior trip_id so a reschedule (date shift) does NOT mint
            # a phantom "new trip"
            trip["trip_id"] = old.get("trip_id") or _trip_id(old)
            trip["first_seen"] = old.get("first_seen", today)
            old_segs = {_seg_key(s): s for s in old.get("segments", [])}
            new_segs = {_seg_key(s): s for s in trip.get("segments", [])}
            tid = trip["trip_id"]
            for k, s in new_segs.items():
                if k not in old_segs:
                    changes.append(f"{tid}: new segment {s.get('type')} {s.get('from','')}→{s.get('to','')}")
                elif s.get("status") and s.get("status") != old_segs[k].get("status"):
                    changes.append(f"{tid}: {s.get('type')} status {old_segs[k].get('status')}→{s.get('status')}")
            # cancellation: a FUTURE prior segment that vanished from the new extraction
            for k, s in old_segs.items():
                if k not in new_segs and _is_future(s):
                    changes.append(f"{tid}: {s.get('type')} {s.get('from','')}→{s.get('to','')} dropped — possible cancellation (verify)")
        trip["last_seen"] = today
        merged.append(trip)

    # Carry forward prior trips this scan didn't re-surface but that are still
    # upcoming — the Gmail query window may simply not have re-matched an older
    # confirmation. Dropping them would lose real trips; only the agent removing
    # a cancelled trip should delete one.
    for i, p in enumerate(priors):
        if i not in used and (p.get("end") or p.get("start") or "")[:10] >= today:
            merged.append(p)

    # Stamp deterministic per-segment uids (the calendar-sync dedup key) on
    # every trip — matched, new, and carried-forward alike.
    for t in merged:
        for s in t.get("segments", []):
            s["uid"] = _seg_uid(t, s)

    out = {"last_scan": datetime.now(timezone.utc).isoformat(),
           "trips": sorted(merged, key=lambda t: t.get("start", ""))}
    return out, new_trips, changes


def cmd_show() -> None:
    d = _load(TRIPS)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    upcoming = [t for t in d.get("trips", []) if (t.get("end") or "")[:10] >= today]
    if not upcoming:
        print("No upcoming trips on record.")
        return
    for t in upcoming:
        print(f"- {t.get('destination')} {t.get('start','?')[:10]}–{t.get('end','?')[:10]} "
              f"({t.get('purpose','?')}) — {len(t.get('segments', []))} segment(s)")


def main() -> None:
    args = sys.argv[1:]
    if not args or args[0] == "--show":
        cmd_show()
        return
    extracted = json.loads(Path(args[0]).read_text())
    if isinstance(extracted, dict):
        extracted = extracted.get("trips", [])
    prior = _load(TRIPS)
    STATE.mkdir(parents=True, exist_ok=True)
    HISTORY.mkdir(parents=True, exist_ok=True)
    if TRIPS.exists():
        snap = HISTORY / f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.json"
        if not snap.exists():
            snap.write_text(TRIPS.read_text())
    out, new_trips, changes = merge(extracted, prior)
    TRIPS.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    print(f"trips: {len(out['trips'])} | new: {len(new_trips)} | changes: {len(changes)}")
    for tid in new_trips:
        print(f"NEW_TRIP {tid}")
    for c in changes:
        print(f"CHANGE {c}")


if __name__ == "__main__":
    main()
