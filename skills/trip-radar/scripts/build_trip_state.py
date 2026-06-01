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


def _seg_key(seg: dict) -> str:
    """Identity of a segment: confirmation# if present, else type+start+route."""
    conf = (seg.get("confirmation") or "").strip().upper()
    if conf:
        return f"conf:{conf}"
    return f"{seg.get('type')}|{seg.get('start')}|{seg.get('from')}|{seg.get('to')}"


def _slug(s: str) -> str:
    out = "".join(c if c.isalnum() else "-" for c in (s or "?").lower())
    while "--" in out:
        out = out.replace("--", "-")
    return out.strip("-") or "trip"


def _trip_id(trip: dict) -> str:
    return f"{_slug(trip.get('destination'))}-{trip.get('start','?')[:10]}"


def merge(extracted: list[dict], prior: dict) -> tuple[dict, list, list]:
    prior_by_id = {t.get("trip_id") or _trip_id(t): t for t in prior.get("trips", [])}
    merged: dict[str, dict] = {}
    new_trips: list[str] = []
    changes: list[str] = []

    for trip in extracted:
        tid = _trip_id(trip)
        trip["trip_id"] = tid
        old = prior_by_id.get(tid)
        if old is None:
            new_trips.append(tid)
        else:
            old_segs = {_seg_key(s): s for s in old.get("segments", [])}
            for s in trip.get("segments", []):
                k = _seg_key(s)
                if k not in old_segs:
                    changes.append(f"{tid}: new segment {s.get('type')} {s.get('from','')}→{s.get('to','')}")
                elif s.get("status") and s.get("status") != old_segs[k].get("status"):
                    changes.append(f"{tid}: {s.get('type')} status {old_segs[k].get('status')}→{s.get('status')}")
            trip["first_seen"] = old.get("first_seen", trip.get("first_seen"))
        merged[tid] = trip

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for t in merged.values():
        t.setdefault("first_seen", today)
        t["last_seen"] = today
    out = {"last_scan": datetime.now(timezone.utc).isoformat(),
           "trips": sorted(merged.values(), key=lambda t: t.get("start", ""))}
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
