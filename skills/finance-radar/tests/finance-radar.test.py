#!/usr/bin/env python3
"""Tests for finance-radar digest logic. pytest-free; no Discord, no sheet —
drives digest.compute/render with synthetic snapshots. Run:
    python3 skills/finance-radar/tests/finance-radar.test.py
"""
from __future__ import annotations

import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / "scripts"))

import digest  # noqa: E402

_fails = []


def check(name, cond):
    print(("PASS" if cond else "FAIL") + f"  {name}")
    if not cond:
        _fails.append(name)


CFG = {"alerts": {"mag7_concentration_pct": 30.0, "liquid_cash_floor": 250000,
                  "milestone_step": 500000, "drawdown_from_ath_pct": 10.0}}


def main() -> int:
    snap = {
        "as_of": "2026-06-04", "net_worth": 9183708,
        "ath": 9256200, "ath_date": "2026-06-01",
        "asset_classes": {"Cash": 355588, "Liabilities": -3001918,
                          "Real Estate": 5810000, "Side Inv": 100000, "Stock": 5920038},
        "mag7": {"concentration_pct": 26.66, "holdings": {"GOOG": 1070354}},
        "liquid_cash": 334120, "fire_net_worth": 7669807, "next_milestone": 9500000,
    }
    prior = {"net_worth": 9100000}

    c = digest.compute(snap, prior, CFG)
    check("wk-over-wk delta computed", c["nw_delta"] == 9183708 - 9100000)
    check("off-ATH pct negative + ~-0.78%", round(c["off_ath_pct"], 2) == -0.78)
    check("no false alerts at normal levels", c["alerts"] == [])

    # render contains the headline + delta + milestone gap
    msg = digest.render(snap, c)
    check("render shows net worth", "$9,183,708" in msg)
    check("render shows delta", "+$83,708" in msg)
    check("render shows milestone gap", "$316,292 to go" in msg)

    # --- alerts fire correctly ---
    # concentration breach
    s2 = dict(snap); s2["mag7"] = {"concentration_pct": 31.0}
    c2 = digest.compute(s2, prior, CFG)
    check("concentration >=30 fires alert", any("concentration" in a for a in c2["alerts"]))

    # liquid cash floor breach
    s3 = dict(snap); s3["liquid_cash"] = 200000
    c3 = digest.compute(s3, prior, CFG)
    check("liquid cash below floor fires alert", any("Liquid cash" in a for a in c3["alerts"]))

    # milestone crossed up
    s4 = dict(snap); s4["net_worth"] = 9550000
    c4 = digest.compute(s4, {"net_worth": 9450000}, CFG)
    check("crossing $9.5M milestone fires", any("9,500,000" in a for a in c4["alerts"]))

    # drawdown off ATH
    s5 = dict(snap); s5["net_worth"] = 8000000  # ~-13.6% off 9.2566M ATH
    c5 = digest.compute(s5, prior, CFG)
    check("drawdown >10% off ATH fires", any("off all-time high" in a for a in c5["alerts"]))

    # --- graceful degradation: missing optional fields ---
    sparse = {"as_of": "2026-06-04", "net_worth": 1000000}
    cc = digest.compute(sparse, None, CFG)
    m = digest.render(sparse, cc)
    check("renders with only required fields (no crash)", "$1,000,000" in m)
    check("no delta line when no prior", "vs last digest" not in m)

    # milestone re-cross guard: drop-then-recover across $9.5M must NOT re-fire
    # (prior ath 9.6M already banked the 9.5M milestone)
    s_recover = dict(snap); s_recover["net_worth"] = 9550000
    c_recover = digest.compute(s_recover, {"net_worth": 9400000, "ath": 9600000}, CFG)
    check("milestone does NOT re-fire below prior ATH", not any("milestone" in a for a in c_recover["alerts"]))
    # genuine NEW high across 9.5M still fires
    c_new = digest.compute(s_recover, {"net_worth": 9450000, "ath": 9450000}, CFG)
    check("genuine new milestone above prior ATH fires", any("9,500,000" in a for a in c_new["alerts"]))

    # B1: config missing a threshold key + a breach must NOT crash the render
    sparse_cfg = {"alerts": {}}
    s_breach = dict(snap); s_breach["mag7"] = {"concentration_pct": 40.0}; s_breach["liquid_cash"] = -5
    try:
        cb = digest.compute(s_breach, prior, sparse_cfg)
        ok = any("concentration" in a for a in cb["alerts"])
    except Exception:
        ok = False
    check("B1: missing-threshold config + breach does not crash, still alerts", ok)

    # B2: next_milestone as a string must not crash render (clean_snapshot coerces)
    s_str = digest.clean_snapshot({**snap, "next_milestone": "9,500,000"})
    try:
        digest.render(s_str, digest.compute(s_str, prior, CFG)); ok2 = True
    except Exception:
        ok2 = False
    check("B2: string next_milestone coerced, no crash", ok2 and s_str["next_milestone"] == 9500000.0)

    # B3: comma-string net_worth is coerced (not silently dropped) → alerts/delta live
    s_comma = digest.clean_snapshot({**snap, "net_worth": "$9,183,708"})
    check("B3: comma/$ net_worth coerced to number", s_comma["net_worth"] == 9183708.0)
    cc3 = digest.compute(s_comma, {"net_worth": 9100000}, CFG)
    check("B3: delta still computed after coercion", cc3["nw_delta"] == 83708.0)
    m3 = digest.render(s_comma, cc3)
    check("B3: render shows $-formatted net worth (not raw string)", "$9,183,708" in m3)

    # to_num edge cases
    from common import to_num
    check("to_num parens = negative", to_num("(1,234)") == -1234.0)
    check("to_num percent", to_num("26.66%") == 26.66)
    check("to_num junk → None", to_num("n/a") is None)

    # money/signed formatting
    from common import money, signed
    check("money formats negative", money(-3001918) == "-$3,001,918")
    check("signed adds plus", signed(83708) == "+$83,708")

    print()
    if _fails:
        print(f"{len(_fails)} FAILED: {_fails}"); return 1
    print("all tests passed"); return 0


if __name__ == "__main__":
    raise SystemExit(main())
