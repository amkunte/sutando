#!/usr/bin/env python3
"""CI guard for `src/scan-catchup.py` — the self-healing skill-scan backstop.

This module is the thing that makes a silently-stopped scan impossible to miss:
it re-derives "is a scan overdue?" from `last_scan` on disk rather than from a
live cron, so cron expiry or a session restart cannot quietly turn a channel off
(see build_log 2026-06-20, #orders going silent mid-trip).

It had **no test coverage at all** until this file. That is the wrong gap to
leave open: an untested backstop can fail silently, and silent failure is
precisely the failure class it exists to catch. A regression here does not
announce itself — the scans just stop being flagged, exactly as if everything
were fine.

Covers current behaviour only:
  * the roaming-node gate (`SKIP_SKILL_SCANS`) never SCANDUEs, but still
    reports SCANSTALE when the owner node's state has gone stale
  * a fresh scan is silent
  * an overdue scan emits SCANDUE
  * the GRACE boundary (1.5x cadence) absorbs one missed tick
  * a missing state file is treated as due (first run)
  * an unparseable `last_scan` is treated as due

Run: python3 tests/scan-catchup-backstop.test.py
Also run under /usr/bin/python3 (3.9.6) — this module ships to a launchd host.
"""
import contextlib
import importlib.util
import io
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MODULE = REPO / "src" / "scan-catchup.py"


def _load():
    """Import the hyphenated module by path (not a valid identifier)."""
    spec = importlib.util.spec_from_file_location("scan_catchup", MODULE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run(mod, state_path, cadence_hours=24):
    """Drive main() over a single synthetic scan, capturing stdout."""
    mod.SCANS = [{
        "name": "fixture-scan",
        "state": state_path,
        "cadence_hours": cadence_hours,
        "hint": "HINT",
    }]
    mod._node_skips_scans = lambda: False
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        mod.main()
    return buf.getvalue().strip()


def _write(path, hours_ago=None, raw=None):
    if raw is not None:
        path.write_text(raw)
        return
    ts = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
    path.write_text(json.dumps({"last_scan": ts}))


def test_roaming_node_gate_never_schedules_a_scan():
    """SKIP_SKILL_SCANS=1 must never produce work for THIS node.

    Guards the Maverick/Goose split: the roaming node must not double-post.

    This test previously asserted the gated node emits *nothing at all*, which
    encoded the short-circuit as the contract. That was too strong, and it was
    load-bearing in the wrong direction: emitting nothing is also what a node
    does when the owner node has died, so the two states were indistinguishable
    and #orders/#parcels/#travel went dark for three days (2026-08-17+) with
    every backstop reading healthy. The invariant that actually matters is
    narrower -- no SCANDUE, i.e. no scan is ever scheduled here.
    """
    mod = _load()
    prev = os.environ.get("SKIP_SKILL_SCANS")
    os.environ["SKIP_SKILL_SCANS"] = "1"
    try:
        assert mod._node_skips_scans() is True
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.json"
            _write(p, hours_ago=1000)  # wildly overdue
            mod.SCANS = [{"name": "x", "state": p, "cadence_hours": 24, "hint": "H"}]
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                mod.main()
            out = buf.getvalue().strip()
            assert "SCANDUE" not in out, f"gated node must not schedule a scan: {out!r}"
    finally:
        if prev is None:
            os.environ.pop("SKIP_SKILL_SCANS", None)
        else:
            os.environ["SKIP_SKILL_SCANS"] = prev


def test_fresh_scan_is_silent():
    mod = _load()
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "s.json"
        _write(p, hours_ago=1)
        assert _run(mod, p) == ""


def test_overdue_scan_emits_scandue():
    mod = _load()
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "s.json"
        _write(p, hours_ago=100)
        out = _run(mod, p)
        assert out.startswith("SCANDUE fixture-scan"), out
        assert "overdue" in out and "HINT" in out, out


def test_grace_absorbs_one_missed_tick():
    """1.4x cadence stays silent; 1.6x fires. Pins GRACE=1.5.

    Without this, someone "tightening" GRACE to 1.0 would make the backstop
    re-fire on every ordinary late tick — noisy enough that the real signal
    gets ignored.
    """
    mod = _load()
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "s.json"
        _write(p, hours_ago=24 * 1.4)
        assert _run(mod, p) == "", "inside grace must stay silent"
        _write(p, hours_ago=24 * 1.6)
        assert _run(mod, p).startswith("SCANDUE"), "past grace must fire"


def test_missing_state_treated_as_due():
    mod = _load()
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "does-not-exist.json"
        out = _run(mod, p)
        assert out.startswith("SCANDUE fixture-scan"), out


def test_unparseable_last_scan_treated_as_due():
    """Both a corrupt file and a valid-JSON-but-garbage timestamp must fire.

    Fail-safe direction: unknown freshness surfaces rather than silently
    suppressing, which is what makes the backstop trustworthy.
    """
    mod = _load()
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "s.json"
        _write(p, raw="{not json")
        assert _run(mod, p).startswith("SCANDUE"), "corrupt JSON must fire"
        _write(p, raw=json.dumps({"last_scan": "not-a-timestamp"}))
        assert _run(mod, p).startswith("SCANDUE"), "bad timestamp must fire"


def test_all_sources_blocked_emits_scanblocked():
    """A scan that RUNS but produces nothing must surface.

    `last_scan` age alone cannot see this: the scan runs, every source is
    blocked, it stamps a fresh last_scan, and the age check reads it as
    perfectly healthy. That was the real state of #orders/#parcels/#travel
    from 2026-07-24. Emitted as SCANBLOCKED, not SCANDUE, because re-running
    a scan whose sources are blocked just reproduces the nothing.
    """
    mod = _load()
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "s.json"
        fresh = datetime.now(timezone.utc).isoformat()
        _write(p, raw=json.dumps({"last_scan": fresh,
                                  "sources_status": {"a": "blocked", "b": "blocked"}}))
        out = _run(mod, p)
        assert out.startswith("SCANBLOCKED"), f"all-blocked must fire: {out!r}"


def test_partial_block_and_healthy_stay_silent():
    """Degraded is not dead — firing on partial blockage would cry wolf."""
    mod = _load()
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "s.json"
        fresh = datetime.now(timezone.utc).isoformat()
        _write(p, raw=json.dumps({"last_scan": fresh,
                                  "sources_status": {"a": "blocked", "b": "ok"}}))
        assert _run(mod, p) == "", "partial blockage must stay silent"
        _write(p, raw=json.dumps({"last_scan": fresh, "sources_status": "ok"}))
        assert _run(mod, p) == "", "healthy must stay silent"


def test_suspended_beats_blocked():
    """The #142 suspend flag must win, or this change silently undoes it.

    karts-air is BOTH suspended and all-sources-blocked; if blocked-detection
    ran first, the SCANDUE noise #142 removed would come straight back.
    """
    mod = _load()
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "s.json"
        fresh = datetime.now(timezone.utc).isoformat()
        _write(p, raw=json.dumps({"last_scan": fresh, "suspended": True,
                                  "sources_status": {"a": "blocked", "b": "blocked"}}))
        assert _run(mod, p) == "", "suspended must suppress the blocked signal"


def test_sources_status_shape_tolerance():
    """Two shapes exist on disk: bare string, and per-source dict."""
    mod = _load()
    assert mod._blocked_sources({"sources_status": "blocked"}) == ["all"]
    assert mod._blocked_sources({"sources_status": "BLOCKED"}) == ["all"]
    assert mod._blocked_sources({"sources_status": "ok"}) == []
    assert mod._blocked_sources({"sources_status": {"x": "blocked"}}) == ["x"]
    assert mod._blocked_sources({"sources_status": {}}) == []
    assert mod._blocked_sources({}) == []


def _run_roaming(mod, state_path, cadence_hours=24, roaming_observable=True):
    """Drive main() on a node gated OUT of scanning, capturing stdout."""
    mod.SCANS = [{
        "name": "fixture-scan",
        "state": state_path,
        "cadence_hours": cadence_hours,
        "hint": "HINT",
        "roaming_observable": roaming_observable,
    }]
    mod._node_skips_scans = lambda: True
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        mod.main()
    return buf.getvalue().strip()


def test_roaming_node_never_emits_scandue():
    """The roaming gate must never trigger a scan on this node.

    SKIP_SKILL_SCANS exists so exactly one node scans; a second runner
    double-posts to the channel. So however stale the state is, the roaming
    node's output must not contain SCANDUE.
    """
    mod = _load()
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "s.json"
        ancient = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        _write(p, raw=json.dumps({"last_scan": ancient}))
        out = _run_roaming(mod, p)
        assert "SCANDUE" not in out, f"roaming node must not self-trigger: {out!r}"


def test_roaming_node_reports_stalled_owner_node():
    """The gate must not blind this node to the owner node having stopped.

    The roaming node pulls the owner node's scan state via fleet-sync, so it
    holds the evidence. Returning silently is what let #orders/#parcels/#travel
    go dark for three days after the home node stopped on 2026-08-17.
    """
    mod = _load()
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "s.json"
        ancient = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        _write(p, raw=json.dumps({"last_scan": ancient}))
        out = _run_roaming(mod, p, cadence_hours=6)   # 72h vs 6h*4=24h
        assert out.startswith("SCANSTALE"), f"stalled owner node must surface: {out!r}"


def test_roaming_node_quiet_while_owner_merely_late():
    """Wider than GRACE on purpose — the owner node is allowed to be off a bit.

    At 1.5x cadence the age check would fire on a scanning node. Here that is
    only "late", not "down", and firing would cry wolf every pass.
    """
    mod = _load()
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "s.json"
        late = (datetime.now(timezone.utc) - timedelta(hours=12)).isoformat()
        _write(p, raw=json.dumps({"last_scan": late}))
        out = _run_roaming(mod, p, cadence_hours=6)   # 12h < 6h*4=24h
        assert out == "", f"merely-late owner node must stay quiet: {out!r}"


def test_roaming_node_respects_suspended():
    """A scan carrying `suspended: true` stays silent on the roaming node (#142).

    This docstring previously claimed "karts-air is suspended and its last_scan
    is months old". The second half is true (2026-06-11); the first is FALSE on
    disk — the real state file's keys are ['candidates', 'last_scan',
    'scan_history', 'sources_status'] and `.get("suspended")` is None. The claim
    came from reading PR #142's TITLE rather than the object, and the fixture
    below made the test green while the real file walks straight past the guard.
    See test_real_world_shape_without_suspended_is_reported for that case.
    """
    mod = _load()
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "s.json"
        ancient = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
        _write(p, raw=json.dumps({"last_scan": ancient, "suspended": True}))
        out = _run_roaming(mod, p, cadence_hours=24)
        assert out == "", f"suspended scan must stay silent when roaming: {out!r}"


def test_roaming_node_ignores_state_it_does_not_carry():
    """Absence is not evidence — fleet-sync only carries manifest-listed items.

    A state file this node never pulled says nothing about the owner node, so
    it must not be reported as a stall.
    """
    mod = _load()
    with tempfile.TemporaryDirectory() as d:
        out = _run_roaming(mod, Path(d) / "missing.json")
        assert out == "", f"uncarried state must not be flagged: {out!r}"


def test_roaming_node_silent_for_scans_whose_state_never_syncs():
    """A PRESENT-but-never-refreshed file is a different case from a missing one.

    test_roaming_node_ignores_state_it_does_not_carry covers the file this node
    never pulled: read fails, nothing is emitted. But karts-air's state file is
    gitignored in the fleet repo and frontier-scan has no fleet entry, so on a
    roaming node those files EXIST -- as that node's own local copy, frozen at
    whenever it last scanned there. Ageing them emits a SCANSTALE that cannot
    clear however healthy the owner node is, which trains the reader to ignore
    the class.

    Measured 2026-08-30: four reachable copies of karts-air-data.json all read
    last_scan=2026-06-11 because they are all the same never-synced local file,
    and that is what produced a "dead 80 days" claim about a scan the owner node
    had run daily through 07-25.
    """
    mod = _load()
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "s.json"
        ancient = (datetime.now(timezone.utc) - timedelta(days=80)).isoformat()
        _write(p, raw=json.dumps({"last_scan": ancient}))
        out = _run_roaming(mod, p, cadence_hours=24, roaming_observable=False)
        assert out == "", f"unsyncable state must not be aged: {out!r}"


def test_roaming_observable_defaults_true():
    """Omitting the flag must preserve the reporting behaviour, not silence it."""
    mod = _load()
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "s.json"
        ancient = (datetime.now(timezone.utc) - timedelta(days=80)).isoformat()
        _write(p, raw=json.dumps({"last_scan": ancient}))
        mod.SCANS = [{"name": "fixture-scan", "state": p,
                      "cadence_hours": 24, "hint": "HINT"}]   # no flag at all
        mod._node_skips_scans = lambda: True
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            mod.main()
        assert buf.getvalue().strip().startswith("SCANSTALE")


def test_real_world_shape_without_suspended_is_reported():
    """The shape that actually exists on disk must reach the owner, not be silently dropped.

    karts-air's live state carries no `suspended` key and a last_scan from
    2026-06-11. The guard above does not apply to it, so it IS reported — which
    is correct: a scan nobody has run in months is either abandoned or was
    suspended without anyone recording that, and both deserve the owner's
    attention rather than a silence invented by the tooling.

    The cost of reporting it (a line that never goes away) is paid by the
    consumer, which surfaces once per changed set — see SKILL.md step 2.6.
    """
    mod = _load()
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "s.json"
        _write(p, raw=json.dumps({
            "last_scan": "2026-06-11T05:33:42-07:00",
            "sources_status": {"barnstormers": "blocked", "aircraftforsale": "ok"},
            "candidates": [],
            "scan_history": [],
        }))
        out = _run_roaming(mod, p, cadence_hours=24)
        assert out.startswith("SCANSTALE"), f"live shape must be reported: {out!r}"


def test_roaming_message_does_not_assert_a_single_cause():
    """Staleness here has two candidate causes; naming one is a false diagnosis.

    `last_scan` freshness on a roaming node depends on the owner node scanning
    AND fleet-sync delivering. An earlier draft said "the owner node has probably
    stopped -- wake the owner node", which sends the owner to fix a healthy
    machine whenever the sync clone is what broke.
    """
    mod = _load()
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "s.json"
        ancient = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
        _write(p, raw=json.dumps({"last_scan": ancient}))
        out = _run_roaming(mod, p, cadence_hours=6)
        assert "fleet-sync" in out, f"must name the sync as a candidate cause: {out!r}"
        assert "probably stopped" not in out, f"must not assert one cause: {out!r}"
        assert "Do NOT scan here" in out, f"must keep the no-double-post constraint: {out!r}"


def test_every_defined_test_is_registered():
    """This file collects from an explicit TESTS list, so a new test that is
    defined but not listed is silently skipped -- the suite still prints a
    green count, just a smaller one than the author thinks.

    Caught live on 2026-08-30: two tests were added, the count stayed at 17,
    and "17 passed" was read as confirmation that a new guard worked. It had
    never executed. The tell was that the count did not move when tests were
    added.

    This file is the only one of ~253 under tests/ that collects this way, so
    the guard is local rather than a repo-wide convention change.
    """
    import re
    src = Path(__file__).read_text()
    defined = set(re.findall(r"^def (test_\w+)", src, re.M))
    block = re.search(r"^TESTS = \[(.*?)^\]", src, re.M | re.S)
    assert block, "TESTS list not found"
    listed = set(re.findall(r"(test_\w+)", block.group(1)))
    missing = sorted(defined - listed)
    assert not missing, f"defined but never run: {missing}"


TESTS = [
    test_roaming_node_gate_never_schedules_a_scan,
    test_roaming_node_never_emits_scandue,
    test_roaming_node_reports_stalled_owner_node,
    test_roaming_node_quiet_while_owner_merely_late,
    test_roaming_node_respects_suspended,
    test_roaming_node_ignores_state_it_does_not_carry,
    test_roaming_node_silent_for_scans_whose_state_never_syncs,
    test_roaming_observable_defaults_true,
    test_every_defined_test_is_registered,
    test_real_world_shape_without_suspended_is_reported,
    test_roaming_message_does_not_assert_a_single_cause,
    test_fresh_scan_is_silent,
    test_overdue_scan_emits_scandue,
    test_grace_absorbs_one_missed_tick,
    test_missing_state_treated_as_due,
    test_unparseable_last_scan_treated_as_due,
    test_all_sources_blocked_emits_scanblocked,
    test_partial_block_and_healthy_stay_silent,
    test_suspended_beats_blocked,
    test_sources_status_shape_tolerance,
]



if __name__ == "__main__":
    failed = 0
    for t in TESTS:
        try:
            t()
            print(f"PASS: {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL: {t.__name__}: {e}")
    print(f"{len(TESTS) - failed} passed, {failed} failed")
    raise SystemExit(1 if failed else 0)
