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
    check("fallback wk delta computed when no daily change", c["nw_delta"] == 9183708 - 9100000)
    check("off-ATH pct negative + ~-0.78%", round(c["off_ath_pct"], 2) == -0.78)
    check("no false alerts at normal levels", c["alerts"] == [])

    # render contains the headline + delta + milestone gap
    msg = digest.render(snap, c)
    check("render shows net worth", "$9,183,708" in msg)
    check("render shows fallback delta", "+$83,708" in msg)
    check("render shows milestone gap", "$316,292 to go" in msg)

    # --- daily-tab fields: sheet's own day_change + 7-day trend take over ---
    snap_daily = dict(snap)
    snap_daily["day_change"] = 43899.73
    snap_daily["day_change_pct"] = 0.4803
    snap_daily["trend_7d"] = [9176159.0, 9176647.0, 9176246.0, 9256199.0, 9206842.0, 9139999.0, 9183899.0]
    cd = digest.compute(snap_daily, prior, CFG)
    check("daily change passed through", round(cd["day_change"], 2) == 43899.73)
    check("daily change SUPPRESSES fallback nw_delta", cd["nw_delta"] is None)
    check("7-day delta computed from trend", cd["week_delta"] == 9183899.0 - 9176159.0)
    md = digest.render(snap_daily, cd)
    check("render shows Today line", "Today: +$43,900" in md)
    check("render shows 7-day line", "7-day:" in md and "$9,176,159" in md)
    check("render omits 'vs last digest' when daily present", "vs last digest" not in md)

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

    # extract_sheet round-trip on a synthetic xlsx (deterministic parser)
    try:
        import openpyxl, tempfile, os, subprocess, json as _json, datetime
        wb = openpyxl.Workbook()
        # daily tab
        dly = wb.active; dly.title = "daily"
        dly.append(["Date", "Current", "Retirement", "SubTotal", "Other", "Total",
                    "Change", "Change %", "Abhi 401K", "Rady 401K", "Crypto", "Tanvi", "Saanvi"])
        base = datetime.datetime(2026, 5, 29)
        totals = [9170000, 9175000, 9176000, 9180000, 9150000, 9140000, 9183900]
        for i, t in enumerate(totals):
            chg = t - (totals[i-1] if i else t)
            dly.append([base + datetime.timedelta(days=i), None, None, None, None, t,
                        chg if i else None, (chg / totals[i-1]) if i else None,
                        None, None, None, None, None])
        # Dashboard tab (minimal label/value layout the parser scans for)
        dsh = wb.create_sheet("Dashboard")
        dsh["A1"] = "Max  Net Worth"; dsh["A2"] = 9256200
        dsh["C1"] = "Concentration"; dsh["E1"] = 0.2666
        dsh["G1"] = "Liquid Cash"; dsh["G2"] = 334120
        dsh["I1"] = "FIRE Net Worth (excl. Primary Residence)"; dsh["J1"] = 7670049
        # one asset-class block: header row then a 'Live' values row (matches the sheet)
        dsh["B8"] = "Cash"; dsh["C8"] = "Liabilities"; dsh["D8"] = "Real Estate"
        dsh["E8"] = "Side Inv"; dsh["F8"] = "Stock"; dsh["G8"] = "Grand Total"
        dsh["A9"] = "Live"; dsh["B9"] = 355588; dsh["C9"] = -3001918; dsh["D9"] = 5810000
        dsh["E9"] = 100000; dsh["F9"] = 5920280; dsh["G9"] = 9183950
        # milestone ladder
        dsh["M1"] = "9M"; dsh["N1"] = datetime.datetime(2026, 5, 6)
        dsh["M2"] = "9.5M"; dsh["M3"] = "10M"
        fx = os.path.join(tempfile.mkdtemp(), "fin.xlsx"); wb.save(fx)
        env = dict(os.environ)
        r = subprocess.run([sys.executable, str(SKILL / "scripts" / "extract_sheet.py"), fx],
                           capture_output=True, text=True, env=env)
        out = _json.loads((SKILL / "state" / "latest-snapshot.json").read_text())
        check("extract: net_worth = latest daily Total", out.get("net_worth") == 9183900)
        check("extract: as_of = latest daily date", out.get("as_of") == "2026-06-04")
        check("extract: day_change from daily tab", round(out.get("day_change"), 0) == 43900)
        check("extract: trend_7d has 7 points", len(out.get("trend_7d", [])) == 7)
        check("extract: concentration % from dashboard", round(out["mag7"]["concentration_pct"], 2) == 26.66)
        check("extract: liquid cash from dashboard", out.get("liquid_cash") == 334120)
        check("extract: asset_classes parsed", out.get("asset_classes", {}).get("Cash") == 355588)
        check("extract: next_milestone = first undated ladder rung", out.get("next_milestone") == 9500000)
    except ImportError:
        print("SKIP  extract_sheet round-trip (openpyxl not installed)")

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
