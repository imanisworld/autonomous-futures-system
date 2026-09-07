"""Unit tests for the pure helpers of scripts/edge_decomposition_audit.py.

Synthetic bars only; no corpus, no engine run.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from scripts import edge_decomposition_audit as audit

ET = ZoneInfo("America/New_York")


def _bars(prices: list[tuple[float, float, float, float]], *, start: datetime, minutes: int = 5,
          instrument: str = "MNQ") -> audit.Bars:
    rows = []
    for i, (o, h, l, c) in enumerate(prices):
        dt = (start + timedelta(minutes=minutes * i)).astimezone(ZoneInfo("UTC"))
        rows.append({"timestamp": dt.isoformat(), "_dt": dt, "open": o, "high": h, "low": l,
                     "close": c, "session": "new_york"})
    return audit.Bars(
        corpus_dir=Path("."), instrument=instrument, rows=rows, files=[Path("synthetic")],
        by_ts={r["timestamp"]: i for i, r in enumerate(rows)},
        by_dt={r["_dt"]: i for i, r in enumerate(rows)},
        file_of_idx=[0] * len(rows), bars_per_day=len(rows),
    )


def _cand(lane: audit.Lane, bars: audit.Bars, idx: int, direction: str, entry: float,
          stop: float, target: float) -> audit.Candidate:
    return audit._candidate(lane, bars, idx, direction, entry, stop, target)


DAY_ONLY_LANE = audit.LANES["322_mnq"]
CARRY_LANE = audit.LANES["orb_reclaim_mnq"]
NY_OPEN = datetime(2026, 3, 2, 9, 30, tzinfo=ET)


def test_eod_bar_detection_by_timeframe():
    assert audit._is_eod_bar(datetime(2026, 3, 2, 15, 55, tzinfo=ET).isoformat(), 5)
    assert not audit._is_eod_bar(datetime(2026, 3, 2, 15, 45, tzinfo=ET).isoformat(), 5)
    assert audit._is_eod_bar(datetime(2026, 3, 2, 15, 45, tzinfo=ET).isoformat(), 15)


def test_time_exit_control_uses_next_bar_open_and_horizon_close():
    prices = [(100 + i, 100.5 + i, 99.5 + i, 100.25 + i) for i in range(20)]
    bars = _bars(prices, start=NY_OPEN)
    cand = _cand(DAY_ONLY_LANE, bars, 0, "LONG", 100.0, 95.0, 110.0)
    out = audit.time_exit_control(DAY_ONLY_LANE, bars, cand, slippage_ticks=1.0)
    assert out["status"] == "OK"
    assert out["entry"] == pytest.approx(101.0 + 0.25)
    h30 = out["horizons"]["30m"]
    # 30-minute horizon ends with the bar opened 25 minutes after the entry bar (index 6).
    assert h30["exit_bar_ts"] == bars.rows[6]["timestamp"]
    assert h30["pts"] == pytest.approx((106.25 - 0.25) - 101.25)
    assert h30["mae_pts"] == pytest.approx(101.25 - 100.5)
    assert out["horizons"]["EOD"] is None


def test_resolve_bracket_market_fill_hits_target_with_commission():
    prices = [(100, 100.5, 99.5, 100.25), (100.5, 111.0, 100.0, 110.5), (110, 111, 109, 110)]
    bars = _bars(prices, start=NY_OPEN)
    cand = _cand(CARRY_LANE, bars, 0, "LONG", 100.0, 95.0, 110.0)
    res = audit.resolve_bracket(CARRY_LANE, bars, cand, fill_model="market", slippage_ticks=1.0,
                                tolerance_ticks=32.0)
    assert res["status"] == "RESOLVED" and res["result"] == "WIN" and res["exit_reason"] == "TARGET_HIT"
    assert res["fill_entry"] == pytest.approx(100.25)
    assert res["gross"] == pytest.approx((110.0 - 100.25) / 0.25 * 0.5)
    assert res["net"] == pytest.approx(res["gross"] - audit.COMMISSION_ROUND_TRIP)


def test_resolve_bracket_resting_order_requires_the_next_bar_to_trade_through():
    untouched = [(100, 100.5, 99.5, 100.25), (99.0, 99.5, 98.0, 98.5), (98, 99, 97, 98)]
    bars = _bars(untouched, start=NY_OPEN)
    cand = _cand(CARRY_LANE, bars, 0, "LONG", 101.0, 95.0, 110.0)
    res = audit.resolve_bracket(CARRY_LANE, bars, cand, fill_model="stop_market", slippage_ticks=1.0,
                                tolerance_ticks=32.0)
    assert res["status"] == "NO_FILL" and res["reason"] == "ENTRY_NOT_TRIGGERED"

    touched = [(100, 100.5, 99.5, 100.25), (100.0, 101.5, 99.5, 101.0), (101, 111, 100.5, 110.5)]
    bars = _bars(touched, start=NY_OPEN)
    cand = _cand(CARRY_LANE, bars, 0, "LONG", 101.0, 95.0, 110.0)
    res = audit.resolve_bracket(CARRY_LANE, bars, cand, fill_model="stop_market", slippage_ticks=1.0,
                                tolerance_ticks=32.0)
    assert res["status"] == "RESOLVED" and res["result"] == "WIN"
    assert res["fill_entry"] == pytest.approx(101.25)


def test_resolve_bracket_rejects_a_fill_beyond_its_own_bracket():
    # SHORT plan at 100 with stop 105; the IOC leg is marketable at 106 and
    # would fill above the stop -- that is a rejected order, not a trade.
    prices = [(105, 106.5, 104, 106.0), (106, 107, 105, 106), (106, 107, 105, 106)]
    bars = _bars(prices, start=NY_OPEN)
    cand = _cand(CARRY_LANE, bars, 0, "SHORT", 100.0, 105.0, 90.0)
    res = audit.resolve_bracket(CARRY_LANE, bars, cand, fill_model="ioc_limit", slippage_ticks=1.0,
                                tolerance_ticks=32.0)
    assert res["status"] == "NO_FILL" and res["reason"] == "ENTRY_BRACKET_INVALID_AT_FILL"


def test_resolve_bracket_day_only_flattens_on_exact_eod_bar_and_fails_closed_without_it():
    start = datetime(2026, 3, 2, 15, 40, tzinfo=ET)
    flat = [(100, 100.5, 99.5, 100.25)] * 4  # 15:40, 15:45, 15:50, 15:55
    bars = _bars(flat, start=start)
    cand = _cand(DAY_ONLY_LANE, bars, 0, "LONG", 100.0, 95.0, 110.0)
    res = audit.resolve_bracket(DAY_ONLY_LANE, bars, cand, fill_model="market", slippage_ticks=1.0,
                                tolerance_ticks=32.0)
    assert res["status"] == "RESOLVED" and res["exit_reason"] == audit.DAY_ONLY_EXIT_REASON
    assert res["exit_price"] == pytest.approx(100.25)

    bars = _bars(flat[:3], start=start)  # no 15:55 bar
    cand = _cand(DAY_ONLY_LANE, bars, 0, "LONG", 100.0, 95.0, 110.0)
    res = audit.resolve_bracket(DAY_ONLY_LANE, bars, cand, fill_model="market", slippage_ticks=1.0,
                                tolerance_ticks=32.0)
    assert res["status"] == "UNRESOLVED" and res["reason"] == audit.EOD_BAR_MISSING


def test_run_bracket_stage_enforces_one_position_at_a_time():
    prices = [(100, 100.5, 99.5, 100.25)] * 3 + [(100, 111, 99.5, 110.5)] + [(110, 111, 109, 110)] * 2
    bars = _bars(prices, start=NY_OPEN)
    first = _cand(CARRY_LANE, bars, 0, "LONG", 100.0, 95.0, 110.0)
    overlapping = _cand(CARRY_LANE, bars, 1, "LONG", 100.0, 95.0, 110.0)
    later = _cand(CARRY_LANE, bars, 4, "LONG", 110.0, 105.0, 120.0)
    rows = audit.run_bracket_stage(CARRY_LANE, bars, [first, overlapping, later], fill_model="market",
                                   slippage_ticks=1.0, tolerance_ticks=32.0)
    statuses = [r["status"] for r in rows]
    assert statuses[0] == "RESOLVED" and statuses[1] == "SKIPPED_POSITION_OPEN" and statuses[2] == "OPEN"
    summary = audit.bracket_summary(rows, boundary=None)
    assert summary["skipped_position_open"] == 1 and summary["resolved"] == 1 and summary["open"] == 1


def test_classify_engine_anchors_each_candidate_on_its_journal_bar(tmp_path):
    lane = audit.LANES["4hr_mnq"]
    bars = _bars([(100, 101, 99, 100.5)] * 4, start=NY_OPEN)
    cands = [_cand(lane, bars, i, "LONG", 100.0, 95.0, 110.0) for i in range(4)]
    ts = [bars.rows[i]["timestamp"] for i in range(4)]
    journal = [
        {"bar_ts": ts[0], "decision": "NO_TRADE", "failed_gates": ["MARKET_CONDITION_NOT_TRENDING"]},
        {"bar_ts": ts[1], "decision": "RISK_REJECTED", "setup": {"strategy": lane.strategy},
         "risk_check": {"failed_rule": "stop_too_wide", "reason": "x"}},
        {"bar_ts": ts[2], "decision": "TRADE", "setup": {"strategy": lane.strategy},
         "risk_check": {"result": "APPROVED"}, "paper_order_id": "PAPER-1"},
        {"type": "OUTCOME", "outcome": {"paper_order_id": "PAPER-1", "result": "WIN", "pnl_dollars": 20.0}},
    ]
    (tmp_path / "journal_2026-03-02.jsonl").write_text("\n".join(json.dumps(r) for r in journal) + "\n")
    rows, totals = audit.classify_engine(lane, cands, tmp_path)
    assert [r["disposition"] for r in rows] == [
        "SIGNAL:MARKET_CONDITION_NOT_TRENDING", "RISK:stop_too_wide", "FILLED:WIN", "NO_ENGINE_ROW_AT_BAR",
    ]
    assert rows[2]["net"] == pytest.approx(20.0 - audit.COMMISSION_ROUND_TRIP)
    assert totals["order_attempts"] == 1 and totals["ioc_filled"] == 1


def _fake_lane_result(*, control_mean: float, bracket_net: float, survivor_share: float,
                      survivor_net: float, ioc_net: float, fill_rate: float) -> dict:
    horizon = {"pts": {"n": 10, "t_stat": 1.0}, "net_usd": {"n": 10, "mean": control_mean}}
    stats = lambda net: {"net": net, "profit_factor": 2.0 if net > 0 else 0.5, "fill_rate": fill_rate}  # noqa: E731
    return {
        "B_time_exit_control": {"30m": horizon, "60m": horizon, "120m": horizon, "EOD": horizon},
        "C_documented_bracket": {"primary": "plan_fill", "plan_fill": stats(bracket_net)},
        "D1_structural_gates": {"survivor_share_risk_structural": survivor_share,
                                "bracket_pnl_of_risk_structural_survivors": stats(survivor_net)},
        "E_ioc_costs": {"1tick": {"risk_structural_survivors": stats(ioc_net)},
                        "3tick": {"risk_structural_survivors": stats(ioc_net)}},
    }


@pytest.mark.parametrize("kwargs, expected", [
    (dict(control_mean=-1, bracket_net=1, survivor_share=0.9, survivor_net=1, ioc_net=1, fill_rate=0.9), "SIGNAL_NOT_DIRECTIONAL"),
    (dict(control_mean=1, bracket_net=-1, survivor_share=0.9, survivor_net=1, ioc_net=1, fill_rate=0.9), "BRACKET_DESTROYS_EDGE"),
    (dict(control_mean=1, bracket_net=1, survivor_share=0.1, survivor_net=1, ioc_net=1, fill_rate=0.9), "RISK_GATES_REMOVE_EDGE"),
    (dict(control_mean=1, bracket_net=1, survivor_share=0.9, survivor_net=1, ioc_net=-1, fill_rate=0.9), "FILL_MODEL_REMOVES_EDGE"),
    (dict(control_mean=1, bracket_net=1, survivor_share=0.9, survivor_net=1, ioc_net=1, fill_rate=0.3), "FILL_MODEL_REMOVES_EDGE"),
    (dict(control_mean=1, bracket_net=1, survivor_share=0.9, survivor_net=1, ioc_net=1, fill_rate=0.9), "SURVIVES_PIPELINE"),
])
def test_classify_lane_reports_first_failing_stage(kwargs, expected):
    assert audit.classify_lane(_fake_lane_result(**kwargs))["primary_failure_stage"] == expected
