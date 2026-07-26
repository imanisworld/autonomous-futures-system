"""Regression coverage for the 4HR Batch-1 evidence study's own driver logic.

Does not re-test strategy/four_hr_retrigger.py's state machine (already
covered by tests/test_four_hr_retrigger_executable.py) -- only this script's
resolution loop (day-only-exit precedence + fail-closed exclusion), summary
math, chronological split, and per-instrument classification.
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from scripts.four_hr_retrigger_stop_study import (
    BASELINE_SLIPPAGE_TICKS,
    COMMISSION_ROUND_TRIP,
    Candidate,
    _classify_one,
    by_direction,
    chronological_halves,
    classify,
    resolve_candidate,
    summarize,
)

ET = ZoneInfo("America/New_York")


def _bar(day, hour, minute, high, low, close=None, open_=None):
    ts = datetime(2026, 1, day, hour, minute, tzinfo=ET)
    mid = (high + low) / 2
    return {"_dt": ts, "open": open_ if open_ is not None else mid, "high": high, "low": low, "close": close if close is not None else mid}


def _candidate(entry_index, entry=100.0, stop=95.0, target=110.0, direction="LONG", day=1, hour=9, minute=35):
    return Candidate(
        instrument="MNQ",
        day=f"2026-01-{day:02d}",
        direction=direction,
        entry=entry,
        stop=stop,
        target=target,
        entry_time=datetime(2026, 1, day, hour, minute, tzinfo=ET),
        entry_index=entry_index,
    )


def test_resolve_candidate_target_hit_is_clean_no_slippage():
    candidate = _candidate(entry_index=0)
    bars = [
        _bar(1, 9, 35, high=100, low=100),  # entry bar itself, not scanned for resolution
        _bar(1, 9, 40, high=111, low=99),   # target touched
    ]
    result = resolve_candidate(candidate, bars, slippage_ticks=1.0)
    assert result["excluded"] is False
    assert result["exit_reason"] == "TARGET_HIT"
    assert result["result"] == "WIN"
    # entry still fills at 100 + 1-tick adverse slip (100.25); target exit is a
    # clean limit fill at 110 -- (110-100.25)/0.25 ticks * $0.50/tick = 19.5
    assert result["gross_pnl"] == 19.5
    assert result["net_pnl"] == round(19.5 - COMMISSION_ROUND_TRIP, 2)


def test_resolve_candidate_stop_hit_applies_adverse_slippage():
    candidate = _candidate(entry_index=0)
    bars = [
        _bar(1, 9, 35, high=100, low=100),
        _bar(1, 9, 40, high=101, low=94),  # stop (95) breached
    ]
    result = resolve_candidate(candidate, bars, slippage_ticks=2.0)
    assert result["exit_reason"] == "STOP_HIT"
    assert result["result"] == "LOSS"
    # entry fills at 100 (no market_price cap in "market" model beyond order.entry),
    # stop exit = 95 - 2 ticks adverse (0.25*2=0.5) = 94.5 -> pnl = (94.5-100)/0.25 * .50
    assert result["gross_pnl"] < 0


def test_resolve_candidate_stop_and_target_same_bar_is_pessimistic_stop_first():
    candidate = _candidate(entry_index=0)
    bars = [
        _bar(1, 9, 35, high=100, low=100),
        _bar(1, 9, 40, high=111, low=94),  # straddles both stop and target
    ]
    result = resolve_candidate(candidate, bars, slippage_ticks=1.0)
    assert result["exit_reason"] == "STOP_HIT"
    assert result["result"] == "LOSS"


def test_resolve_candidate_day_only_flatten_when_neither_stop_nor_target_hit():
    candidate = _candidate(entry_index=0)
    bars = [_bar(1, 9, 35, high=100, low=100)]
    # fill every 5-min bar through the exact EOD bar (15:55) without touching stop/target
    minute = 40
    hour = 9
    while not (hour == 15 and minute == 55):
        bars.append(_bar(1, hour, minute, high=101, low=99))
        minute += 5
        if minute >= 60:
            minute = 0
            hour += 1
    bars.append(_bar(1, 15, 55, high=101, low=99, close=104.0))  # exact EOD bar
    result = resolve_candidate(candidate, bars, slippage_ticks=1.0)
    assert result["exit_reason"] == "DAY_ONLY_FLATTEN"
    assert result["result"] == "WIN"  # close 104 > filled entry for LONG
    # day-only fill uses the ACTUAL filled entry (100 + 1-tick adverse slip =
    # 100.25), not the raw candidate.entry -- (104-100.25)/0.25*0.50 = 7.5
    assert result["gross_pnl"] == 7.5


def test_resolve_candidate_missing_eod_bar_fails_closed_excluded():
    candidate = _candidate(entry_index=0)
    # bars stop at 15:50, never reach the exact 15:55 EOD bar, never hit stop/target
    bars = [_bar(1, 9, 35, high=100, low=100), _bar(1, 15, 50, high=101, low=99)]
    result = resolve_candidate(candidate, bars, slippage_ticks=1.0)
    assert result["excluded"] is True
    assert result["exclusion_reason"] == "EOD_BAR_MISSING_FAIL_CLOSED"


def test_resolve_candidate_never_looks_past_next_calendar_day():
    candidate = _candidate(entry_index=0, day=1)
    bars = [
        _bar(1, 9, 35, high=100, low=100),
        _bar(2, 9, 40, high=111, low=99),  # next day's bar must never resolve a prior day's trade
    ]
    result = resolve_candidate(candidate, bars, slippage_ticks=1.0)
    assert result["excluded"] is True


def test_summarize_computes_profit_factor_and_expectancy():
    trades = [
        {"excluded": False, "net_pnl": 100.0, "gross_pnl": 101.48, "mae_ticks": -5.0, "mfe_ticks": 10.0},
        {"excluded": False, "net_pnl": -50.0, "gross_pnl": -48.52, "mae_ticks": -20.0, "mfe_ticks": 2.0},
        {"excluded": True, "exclusion_reason": "EOD_BAR_MISSING_FAIL_CLOSED"},
    ]
    stats = summarize(trades)
    assert stats["candidates"] == 3
    assert stats["resolved"] == 2
    assert stats["excluded_fail_closed"] == 1
    assert stats["wins"] == 1
    assert stats["losses"] == 1
    assert stats["net_pnl"] == 50.0
    assert stats["profit_factor"] == round(100.0 / 50.0, 3)
    assert stats["expectancy_per_trade"] == 25.0


def test_summarize_empty_is_labeled_not_extrapolated():
    stats = summarize([])
    assert stats["resolved"] == 0
    assert stats["win_rate_pct"] is None
    assert stats["expectancy_per_trade"] is None
    assert stats["profit_factor"] is None


def test_chronological_halves_splits_by_day_not_arrival_order():
    trades = [
        {"day": "2026-01-05", "instrument": "MNQ"},
        {"day": "2026-01-01", "instrument": "MNQ"},
        {"day": "2026-01-03", "instrument": "MNQ"},
        {"day": "2026-01-02", "instrument": "MNQ"},
    ]
    halves = chronological_halves(trades)
    assert [t["day"] for t in halves["first_half"]] == ["2026-01-01", "2026-01-02"]
    assert [t["day"] for t in halves["second_half"]] == ["2026-01-03", "2026-01-05"]


def test_by_direction_splits_long_and_short():
    trades = [
        {"direction": "LONG"}, {"direction": "SHORT"}, {"direction": "LONG"},
    ]
    split = by_direction(trades)
    assert len(split["long"]) == 2
    assert len(split["short"]) == 1


def _fake_report(resolved, net, h1_net, h2_net, slip_nets, long_net=1.0, short_net=1.0):
    def _overall(n, r):
        return {"resolved": r, "net_pnl": n}
    return {
        "variants": {
            "slippage_1_tick": {
                "overall": _overall(net, resolved),
                "chronological_first_half": {"net_pnl": h1_net},
                "chronological_second_half": {"net_pnl": h2_net},
                "long": {"net_pnl": long_net},
                "short": {"net_pnl": short_net},
            },
            "slippage_2_tick": {"overall": {"net_pnl": slip_nets[1], "resolved": resolved}},
            "slippage_3_tick": {"overall": {"net_pnl": slip_nets[2], "resolved": resolved}},
        }
    }


def test_classify_one_wait_below_sample_floor():
    report = _fake_report(resolved=5, net=100, h1_net=50, h2_net=50, slip_nets=[100, 90, 80])
    result = _classify_one("MNQ", report, "slippage_1_tick")
    assert result["verdict"] == "WAIT"


def test_classify_one_promising_when_robust_across_halves_and_slippage():
    report = _fake_report(resolved=80, net=3069.6, h1_net=1794.8, h2_net=1274.8, slip_nets=[3069.6, 3014.1, 2958.6])
    result = _classify_one("MNQ", report, "slippage_1_tick")
    assert result["verdict"] == "PROMISING BUT UNPROVEN"


def test_classify_one_broken_when_second_half_negative():
    report = _fake_report(resolved=75, net=166.5, h1_net=801.49, h2_net=-634.99, slip_nets=[166.5, 31.5, -103.5])
    result = _classify_one("MES", report, "slippage_1_tick")
    assert result["verdict"] == "BROKEN"


def test_classify_one_broken_when_slippage_flips_sign_even_if_halves_positive():
    report = _fake_report(resolved=30, net=100, h1_net=60, h2_net=40, slip_nets=[100, 20, -10])
    result = _classify_one("MES", report, "slippage_1_tick")
    assert result["verdict"] == "BROKEN"


def test_classify_never_blends_a_single_verdict_across_instruments():
    good = _fake_report(resolved=80, net=3069.6, h1_net=1794.8, h2_net=1274.8, slip_nets=[3069.6, 3014.1, 2958.6])
    bad = _fake_report(resolved=75, net=166.5, h1_net=801.49, h2_net=-634.99, slip_nets=[166.5, 31.5, -103.5])
    result = classify({"MNQ": good, "MES": bad})
    assert result["per_instrument"]["MNQ"]["verdict"] == "PROMISING BUT UNPROVEN"
    assert result["per_instrument"]["MES"]["verdict"] == "BROKEN"
    assert "warning" in result
