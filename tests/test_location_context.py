"""
tests/test_location_context.py

Premarket location-context collector (operator-approved 2026-07-16,
OBSERVATION ONLY). Locks: zone detection/freshness/broken semantics, trading-
day level computation (PDH/PDL/prev open+close, overnight, premarket),
impulse-phase classification, per-candidate alignment / opposing-zone /
target-blocked fields, the causal cross-instrument regime reader, the offline
persistence helper — and that the runner journals all of it beside candidates
without ever letting a collector failure affect the decision.
"""
from __future__ import annotations

import dataclasses
import json
from datetime import datetime, timedelta, timezone

from context.location_context import (
    aggregate,
    build_location_context,
    candidate_location,
    detect_zones,
    nearest_zones,
    read_other_instrument_regime,
    regime_persistence,
)


from tests.conftest import load_permissive_config

def make_bars(ohlc_list, start="2026-05-22T00:00:00+00:00", step_min=15):
    t0 = datetime.fromisoformat(start)
    return [
        {"ts": (t0 + timedelta(minutes=step_min * i)).isoformat(),
         "open": o, "high": h, "low": l, "close": c}
        for i, (o, h, l, c) in enumerate(ohlc_list)
    ]


FLAT = [(100.0, 101.0, 99.0, 100.0)] * 10


# ── aggregation ───────────────────────────────────────────────────────────────

def test_aggregate_15m_to_1h():
    bars = make_bars([(100, 102, 99, 101), (101, 105, 100, 104),
                      (104, 106, 103, 103), (103, 104, 98, 99)],
                     start="2026-05-22T10:00:00+00:00")
    agg = aggregate([{**b, "ts": datetime.fromisoformat(b["ts"])} for b in bars], 60)
    assert len(agg) == 1
    assert (agg[0]["open"], agg[0]["high"], agg[0]["low"], agg[0]["close"]) == (100, 106, 98, 99)


# ── zone detection ────────────────────────────────────────────────────────────

def _clean(bars):
    return [{**b, "ts": datetime.fromisoformat(b["ts"])} for b in bars]


def test_up_impulse_leaves_demand_zone_at_base():
    bars = _clean(make_bars(FLAT + [(100.0, 110.5, 100.0, 110.0)]))
    zones = detect_zones(bars, 15)
    assert len(zones) == 1
    z = zones[0]
    assert z["kind"] == "demand" and z["top"] == 101.0 and z["bottom"] == 99.0
    assert z["fresh"] is True and z["tests"] == 0 and z["broken"] is False


def test_down_impulse_leaves_supply_zone_and_test_counting():
    bars = _clean(make_bars(
        FLAT
        + [(100.0, 100.0, 89.0, 90.0)]      # impulse DOWN → supply 99..101
        + [(90.0, 91.0, 89.0, 90.0)]        # away from zone — not a test
        + [(90.0, 100.0, 90.0, 95.0)]))     # wicks back into zone — one test
    zones = [z for z in detect_zones(bars, 15) if z["kind"] == "supply"]
    assert len(zones) == 1
    z = zones[0]
    assert z["tests"] == 1 and z["fresh"] is False and z["broken"] is False


def test_zone_breaks_on_close_beyond_far_edge():
    bars = _clean(make_bars(
        FLAT
        + [(100.0, 100.0, 89.0, 90.0)]      # supply zone 99..101
        + [(90.0, 90.5, 89.0, 90.0)]
        + [(90.0, 103.0, 90.0, 102.5)]))    # closes above top → broken
    z = [z for z in detect_zones(bars, 15) if z["kind"] == "supply"][0]
    assert z["broken"] is True
    near = nearest_zones([z], price=95.0)
    assert near["supply"] is None  # broken zones never count as nearest


def test_nearest_zones_distance_and_containment():
    supply = {"kind": "supply", "top": 110.0, "bottom": 108.0, "tests": 0,
              "fresh": True, "broken": False, "timeframe_minutes": 60,
              "formed_ts": datetime(2026, 5, 22, tzinfo=timezone.utc)}
    demand = {"kind": "demand", "top": 96.0, "bottom": 94.0, "tests": 2,
              "fresh": False, "broken": False, "timeframe_minutes": 60,
              "formed_ts": datetime(2026, 5, 22, tzinfo=timezone.utc)}
    near = nearest_zones([supply, demand], price=100.0)
    assert near["supply"]["distance_points"] == 8.0
    assert near["demand"]["distance_points"] == 4.0
    inside = nearest_zones([supply], price=109.0)
    assert inside["supply"]["distance_points"] == 0.0


# ── bar-level context: levels, ranges, impulse ────────────────────────────────

def _two_day_bars():
    """Prev trading day (100-range) then overnight of the next trading day.
    18:00 ET = 22:00 UTC (May, EDT) is the roll."""
    rows = []
    # previous trading day: 2026-05-21 22:00 UTC → 2026-05-22 21:45 UTC
    t0 = datetime.fromisoformat("2026-05-21T22:00:00+00:00")
    n_prev = 24 * 4  # full day of 15m bars
    for i in range(n_prev):
        rows.append({"ts": (t0 + timedelta(minutes=15 * i)).isoformat(),
                     "open": 100.0, "high": 105.0 if i == 10 else 101.0,
                     "low": 95.0 if i == 20 else 99.0, "close": 100.0})
    # current trading day overnight: from 2026-05-22 22:00 UTC onward
    t1 = datetime.fromisoformat("2026-05-22T22:00:00+00:00")
    for i in range(45):  # 11h15m → through 09:00 UTC, past 04:00 ET premarket start
        rows.append({"ts": (t1 + timedelta(minutes=15 * i)).isoformat(),
                     "open": 100.0, "high": 103.0 if i == 5 else 100.5,
                     "low": 97.0 if i == 8 else 99.5, "close": 100.0})
    return rows


def test_levels_prev_day_overnight_premarket():
    bars = _two_day_bars()
    now = datetime.fromisoformat("2026-05-23T09:00:00+00:00")  # 05:00 ET
    loc = build_location_context(bars15=bars, price=100.0, now=now,
                                 market_condition="TRENDING", other_regime=None)
    assert loc["levels"]["pdh"] == 105.0 and loc["levels"]["pdl"] == 95.0
    assert loc["levels"]["prev_open"] == 100.0 and loc["levels"]["prev_close"] == 100.0
    assert loc["levels"]["onh"] == 103.0 and loc["levels"]["onl"] == 97.0
    # premarket started 04:00 ET (08:00 UTC): only the 08:00-09:00 UTC bars
    assert "pmh" in loc["levels"] and loc["levels"]["pmh"] <= 100.5
    assert loc["nearest_key_level"]["name"] in loc["levels"]


def test_impulse_phase_classification():
    dead = _clean(make_bars([(100.0, 100.6, 99.4, 100.0)] * 20))
    now = dead[-1]["ts"]
    from context.location_context import _impulse_phase, _median_true_range
    mtr = _median_true_range(dead)
    assert _impulse_phase(dead, now, mtr)["phase"] == "pre_impulse"

    ramp = [(100.0 + i, 101.2 + i, 99.8 + i, 101.0 + i) for i in range(8)]
    late = _clean(make_bars([(100.0, 100.6, 99.4, 100.0)] * 8 + ramp))
    got = _impulse_phase(late, late[-1]["ts"], _median_true_range(late))
    assert got["phase"] == "late_entry"


# ── candidate-level fields ────────────────────────────────────────────────────

def _loc_with(sup=None, dem=None, rel1h="middle", rel4h="middle", middle=True):
    return {
        "zones": {"1h": {"supply": sup, "demand": dem, "relation": rel1h},
                  "4h": {"supply": None, "demand": None, "relation": rel4h}},
        "middle_of_range": middle,
    }


SUP = {"kind": "supply", "top": 110.0, "bottom": 108.0, "tests": 0,
       "fresh": True, "broken": False, "timeframe_minutes": 60,
       "formed_ts": "2026-05-22T00:00:00+00:00", "distance_points": 8.0}


def test_long_under_fresh_supply_is_against_and_target_blocked():
    loc = _loc_with(sup=SUP, rel1h="approaching_supply", middle=False)
    got = candidate_location(loc, direction="LONG", entry=100.0, target=112.0)
    assert got["direction_zone_alignment"] == "against"
    assert got["opposing_zone"]["kind"] == "supply"
    assert got["room_to_opposing_points"] == 8.0
    assert got["target_blocked_by_opposing_zone"] is True


def test_long_with_room_below_supply_not_blocked():
    loc = _loc_with(sup=SUP, rel1h="inside_demand", middle=False)
    got = candidate_location(loc, direction="LONG", entry=100.0, target=105.0)
    assert got["direction_zone_alignment"] == "aligned"
    assert got["target_blocked_by_opposing_zone"] is False


def test_short_mirror_and_neutral_middle():
    dem = {**SUP, "kind": "demand", "top": 96.0, "bottom": 94.0}
    loc = _loc_with(dem=dem, rel1h="middle")
    got = candidate_location(loc, direction="SHORT", entry=100.0, target=93.0)
    assert got["direction_zone_alignment"] == "neutral"
    assert got["opposing_zone"]["kind"] == "demand"
    assert got["room_to_opposing_points"] == 4.0
    assert got["target_blocked_by_opposing_zone"] is True
    assert got["middle_of_range"] is True


def test_candidate_location_invalid_inputs_return_none():
    assert candidate_location(None, direction="LONG", entry=1, target=2) is None
    assert candidate_location(_loc_with(), direction="?", entry=1, target=2) is None
    assert candidate_location(_loc_with(), direction="LONG", entry="x", target=2) is None


# ── cross-instrument regime reader (causal) ───────────────────────────────────

def test_read_other_instrument_regime(tmp_path):
    now = datetime.fromisoformat("2026-05-23T14:30:00+00:00")
    rows = [
        {"ts": "2026-05-23T14:15:02+00:00", "instrument": "MES",
         "market_condition": "RANGE_BOUND", "decision": "NO_TRADE"},
        {"ts": "2026-05-23T14:45:02+00:00", "instrument": "MES",  # FUTURE row
         "market_condition": "TRENDING", "decision": "NO_TRADE"},
    ]
    p = tmp_path / "journal_2026-05-23.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    got = read_other_instrument_regime(tmp_path, "MNQ", now,
                                       for_date=now.date())
    assert got == {"instrument": "MES", "market_condition": "RANGE_BOUND",
                   "age_seconds": got["age_seconds"]}
    assert got["age_seconds"] < 1800  # and the FUTURE row was never used


def test_read_other_instrument_regime_stale_or_missing(tmp_path):
    now = datetime.fromisoformat("2026-05-23T14:30:00+00:00")
    p = tmp_path / "journal_2026-05-23.jsonl"
    p.write_text(json.dumps({"ts": "2026-05-23T10:00:00+00:00",
                             "instrument": "MES",
                             "market_condition": "TRENDING"}) + "\n")
    assert read_other_instrument_regime(tmp_path, "MNQ", now,
                                        for_date=now.date()) is None
    assert read_other_instrument_regime(tmp_path / "nope", "MNQ", now,
                                        for_date=now.date()) is None


# ── offline persistence helper ────────────────────────────────────────────────

def test_regime_persistence_offline(tmp_path):
    t0 = datetime.fromisoformat("2026-05-23T14:30:00+00:00")
    rows = [{"ts": (t0 + timedelta(minutes=m)).isoformat(), "instrument": "MNQ",
             "market_condition": cond}
            for m, cond in ((15, "TRENDING"), (30, "RANGE_BOUND"),
                            (60, "RANGE_BOUND"))]
    p = tmp_path / "journal_2026-05-23.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    got = regime_persistence(p, "MNQ", t0)
    assert got == {"+15m": "TRENDING", "+30m": "RANGE_BOUND",
                   "+60m": "RANGE_BOUND"}


# ── wired into the runner: journaled beside every candidate, fail-soft ────────

def test_runner_journals_location_context_beside_candidates(tmp_path):
    import sys
    from datetime import date
    from config.settings import load_config
    from webhook.runner import process_alert
    sys.path.insert(0, "tests")
    from test_e2e_scenarios import _base_payload

    # Explicit permissive universe: general runtime behavior proof, not an
    # assertion about the shipped isolated-lane config.
    cfg = load_permissive_config(max_staleness_seconds=10 ** 9)
    payload = _base_payload(timestamp="2026-05-23T14:30:00+00:00")
    fd = date(2026, 5, 23)
    result = process_alert(payload, config=cfg, log_dir=str(tmp_path), for_date=fd)
    assert result["decision"] in {"TRADE", "NO_TRADE", "RISK_REJECTED"}

    rows = [json.loads(line) for line in
            (tmp_path / f"journal_{fd.isoformat()}.jsonl").read_text().splitlines()]
    decision_rows = [r for r in rows if r.get("decision") and r.get("context")]
    assert decision_rows, rows
    ctx = decision_rows[-1]["context"]
    # bar history in a fresh tmp dir holds only this bar — the collector must
    # still produce a context (levels sparse) rather than crash or gate.
    assert "location_context" in ctx and ctx["location_context"] is not None
    loc = ctx["location_context"]
    assert loc["regime_at_signal"] is not None
    assert loc["zones"]["1h"]["relation"] == "middle"
    # every journaled candidate carries the per-candidate location block
    for c in (decision_rows[-1].get("candidate_audit") or []):
        assert "location" in c
    for c in (decision_rows[-1].get("shadow_candidates") or []):
        assert "location" in c


def test_collector_failure_never_affects_decision(tmp_path, monkeypatch):
    import sys
    from datetime import date
    from config.settings import load_config
    import webhook.runner as runner_mod
    sys.path.insert(0, "tests")
    from test_e2e_scenarios import _base_payload

    import context.location_context as lc

    def _boom(**_kw):
        raise RuntimeError("synthetic collector failure")

    monkeypatch.setattr(lc, "build_location_context", _boom)
    # Explicit permissive universe: general runtime behavior proof, not an
    # assertion about the shipped isolated-lane config.
    cfg = load_permissive_config(max_staleness_seconds=10 ** 9)
    payload = _base_payload(timestamp="2026-05-23T14:30:00+00:00")
    result = runner_mod.process_alert(
        payload, config=cfg, log_dir=str(tmp_path), for_date=date(2026, 5, 23))
    # the decision pipeline is untouched by a collector explosion
    assert result["decision"] == "TRADE" and result.get("fill"), result
