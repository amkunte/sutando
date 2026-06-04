#!/usr/bin/env python3
"""Build (and optionally post) the #finance weekly digest from the dashboard
snapshot the agent extracted into state/latest-snapshot.json.

Deterministic: week-over-week delta vs the stored prior point, threshold
alerts, render, post to #finance, then rotate latest → prior + history.

Usage:
    digest.py            # dry-run: print the digest, change nothing
    digest.py --post     # post to #finance, then persist this week's point
"""
from __future__ import annotations

import argparse
import json
import sys
import time

from common import (load_config, load_json, latest_path, prior_path, state_dir,
                    post_to_finance, money, signed, to_num)


def clean_snapshot(snap: dict) -> dict:
    """Coerce every numeric field via to_num so comma/$-formatted sheet values
    ('9,183,708') become real numbers instead of being silently treated as
    absent (B3). Non-numeric → None (the digest degrades gracefully)."""
    if not isinstance(snap, dict):
        return snap
    s = dict(snap)
    for k in ("net_worth", "ath", "liquid_cash", "fire_net_worth", "next_milestone"):
        if k in s:
            s[k] = to_num(s[k])
    if isinstance(s.get("asset_classes"), dict):
        s["asset_classes"] = {k: to_num(v) for k, v in s["asset_classes"].items()}
    if isinstance(s.get("mag7"), dict):
        m = dict(s["mag7"])
        if "concentration_pct" in m:
            m["concentration_pct"] = to_num(m["concentration_pct"])
        if isinstance(m.get("holdings"), dict):
            m["holdings"] = {k: to_num(v) for k, v in m["holdings"].items()}
        s["mag7"] = m
    return s

# latest-snapshot.json schema the agent writes (see scan-prompt.md):
# {as_of, net_worth, ath, ath_date, asset_classes:{label:amt},
#  mag7:{concentration_pct, holdings:{ticker:amt}}, liquid_cash,
#  fire_net_worth, next_milestone}
REQUIRED = ("as_of", "net_worth")


def _pct(n):
    return "n/a" if n is None else f"{n:+.2f}%"


def compute(snap: dict, prior: dict, cfg: dict) -> dict:
    nw = snap.get("net_worth")
    out = {"alerts": [], "nw_delta": None, "nw_delta_pct": None, "off_ath_pct": None,
           "day_change": None, "day_change_pct": None, "week_delta": None, "week_start": None}

    # Day-over-day change comes straight from the live `daily` tab (authoritative
    # — it's why the owner keeps that tab). day_change_pct is already in %.
    if isinstance(snap.get("day_change"), (int, float)):
        out["day_change"] = snap["day_change"]
    if isinstance(snap.get("day_change_pct"), (int, float)):
        out["day_change_pct"] = snap["day_change_pct"]

    # 7-day trend from the daily tab's last 7 Totals.
    trend = [t for t in (snap.get("trend_7d") or []) if isinstance(t, (int, float))]
    if len(trend) >= 2:
        out["week_delta"] = trend[-1] - trend[0]
        out["week_start"] = trend[0]

    ath = snap.get("ath")
    if isinstance(nw, (int, float)) and isinstance(ath, (int, float)) and ath:
        out["off_ath_pct"] = (nw - ath) / ath * 100.0

    # Fallback "since last digest" delta only if the daily tab gave nothing.
    if (out["day_change"] is None and prior
            and isinstance(prior.get("net_worth"), (int, float)) and isinstance(nw, (int, float))):
        pnw = prior["net_worth"]
        out["nw_delta"] = nw - pnw
        out["nw_delta_pct"] = ((nw - pnw) / pnw * 100.0) if pnw else None

    al = cfg.get("alerts", {})
    # Mag-7 concentration. Bind the threshold to a local — re-indexing al[...]
    # in the message would KeyError if the config omits the key, killing the
    # whole digest in exactly the week the alert should fire (B1).
    conc_thr = al.get("mag7_concentration_pct", 30)
    conc = (snap.get("mag7") or {}).get("concentration_pct")
    if isinstance(conc, (int, float)) and conc >= conc_thr:
        out["alerts"].append(f"⚠️ Mag-7 concentration {conc:.1f}% ≥ {conc_thr:.0f}% threshold")
    # Liquid cash floor
    cash_floor = al.get("liquid_cash_floor", 0)
    lc = snap.get("liquid_cash")
    if isinstance(lc, (int, float)) and lc < cash_floor:
        out["alerts"].append(f"⚠️ Liquid cash {money(lc)} below floor {money(cash_floor)}")
    # Drawdown from ATH
    if out["off_ath_pct"] is not None and out["off_ath_pct"] <= -abs(al.get("drawdown_from_ath_pct", 100)):
        out["alerts"].append(f"⚠️ Net worth {out['off_ath_pct']:.1f}% off all-time high")
    # Milestone crossed (up) since prior
    step = al.get("milestone_step", 500000)
    if prior and isinstance(prior.get("net_worth"), (int, float)) and isinstance(nw, (int, float)) and step:
        # Only fire milestones above the prior all-time high — otherwise a
        # drop-then-recover across a $0.5M boundary re-fires an already-banked
        # milestone every recovery week. ath is monotonic, so it's the right
        # high-water mark (falls back to prior net worth if ath absent).
        hwm = prior.get("ath") if isinstance(prior.get("ath"), (int, float)) else prior["net_worth"]
        crossed = [m for m in range(int((prior["net_worth"] // step + 1) * step),
                                    int(nw) + 1, int(step)) if m > hwm]
        for m in crossed:
            out["alerts"].append(f"🎉 Crossed {money(m)} net-worth milestone")
    return out


def render(snap: dict, c: dict) -> str:
    L = ["💰 **Finance digest** — net worth " + money(snap.get("net_worth"))
         + f"  (as of {snap.get('as_of','?')})"]
    if c["day_change"] is not None:
        L.append(f"Today: {signed(c['day_change'])} ({_pct(c['day_change_pct'])})")
    if c["week_delta"] is not None:
        L.append(f"7-day: {signed(c['week_delta'])}  (from {money(c['week_start'])})")
    if c["nw_delta"] is not None:  # fallback path only
        L.append(f"Δ vs last digest: {signed(c['nw_delta'])} ({_pct(c['nw_delta_pct'])})")
    if c["off_ath_pct"] is not None:
        L.append(f"Off ATH: {c['off_ath_pct']:+.2f}%  (ATH {money(snap.get('ath'))})")
    ac = snap.get("asset_classes") or {}
    if ac:
        L.append("Split: " + " · ".join(f"{k} {money(v)}" for k, v in ac.items()))
    mag7 = snap.get("mag7") or {}
    if mag7.get("concentration_pct") is not None:
        L.append(f"Mag-7 concentration: {mag7['concentration_pct']:.1f}%")
    if snap.get("liquid_cash") is not None:
        L.append(f"Liquid cash: {money(snap['liquid_cash'])}")
    if snap.get("fire_net_worth") is not None:
        L.append(f"FIRE net worth (excl. primary): {money(snap['fire_net_worth'])}")
    if isinstance(snap.get("next_milestone"), (int, float)) and isinstance(snap.get("net_worth"), (int, float)):
        gap = snap["next_milestone"] - snap["net_worth"]
        L.append(f"Next milestone {money(snap['next_milestone'])}: {money(gap)} to go")
    if c["alerts"]:
        L.append("")
        L.extend(c["alerts"])
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--post", action="store_true")
    args = ap.parse_args()

    cfg = load_config()
    snap = load_json(latest_path())
    if not snap:
        print("finance-radar: no latest-snapshot.json — the agent must extract the "
              "dashboard into it first (see scan-prompt.md)", file=sys.stderr)
        return 2
    snap = clean_snapshot(snap)
    # net_worth must be a real number post-coercion (a comma-string that fails
    # to parse becomes None and is rejected here, not silently zeroed — B3).
    if not isinstance(snap.get("net_worth"), (int, float)):
        print("finance-radar: latest-snapshot.json has no usable numeric net_worth",
              file=sys.stderr)
        return 2

    prior = clean_snapshot(load_json(prior_path()) or {})
    c = compute(snap, prior, cfg)
    msg = render(snap, c)

    if not args.post:
        print(msg)
        print("\n(dry run — pass --post to deliver to #finance and persist this point)")
        return 0

    if not post_to_finance(msg):
        return 1
    # Persist this point as the new prior + append to history (for trend).
    snap = dict(snap)
    snap["_posted_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    prior_path().write_text(json.dumps(snap, indent=2, ensure_ascii=False))
    hist = state_dir() / "history"
    hist.mkdir(exist_ok=True)
    # Sanitize as_of for the filename — a sheet date like "06/04/2026" would
    # otherwise be read as nested dirs and crash with FileNotFoundError.
    safe_date = str(snap.get("as_of", "snapshot")).replace("/", "-").replace("\\", "-")
    (hist / f"{safe_date}.json").write_text(
        json.dumps(snap, indent=2, ensure_ascii=False))
    print("posted to #finance + persisted this point")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
