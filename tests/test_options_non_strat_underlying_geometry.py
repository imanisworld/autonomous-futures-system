from __future__ import annotations

from datetime import datetime, timedelta, timezone

from alert_ranker.causal_bars import Bar
from research.options_non_strat_underlying_geometry import (
    Candidate,
    GEOMETRIES,
    collect_candidates,
    geometry_prices,
    gate_family,
    simulate,
)
from alert_ranker.session_calendar import nyse_session_for


BASE = datetime(2026, 6, 15, 14, 0, tzinfo=timezone.utc)


def _bar(i: int, *, o: float, h: float, l: float, c: float) -> Bar:
    return Bar(
        start=BASE + timedelta(minutes=5 * i),
        open=o,
        high=h,
        low=l,
        close=c,
        volume=1000.0,
        vwap=c,
    )


def _candidate(direction="LONG", level=100.0) -> Candidate:
    return Candidate(
        symbol="AAPL",
        session_date="2026-06-15",
        episode_id=f"ep-{direction}",
        family="PDH_RECLAIM_LONG" if direction == "LONG" else "PDH_REJECTION_SHORT",
        direction=direction,
        level_name="PDH",
        level_value=level,
        trigger_bar_start=BASE.isoformat(),
        trigger_idx=0,
        trigger_open=99.8,
        trigger_high=100.6,
        trigger_low=99.5,
        trigger_close=100.4 if direction == "LONG" else 99.6,
        market_aligned=True,
    )


def test_geometry_is_frozen_to_level_or_trigger_bar():
    long = _candidate("LONG")
    stop, target, risk = geometry_prices(long, 100.5, "O1_EVENT_LEVEL")
    assert stop == 99.99
    assert round(risk, 2) == 0.51
    assert round(target, 2) == 101.52

    stop2, target2, risk2 = geometry_prices(long, 100.5, "O2_TRIGGER_BAR")
    assert stop2 == 99.49
    assert round(risk2, 2) == 1.01
    assert round(target2, 2) == 102.52
    assert set(GEOMETRIES) == {"O1_EVENT_LEVEL", "O2_TRIGGER_BAR"}


def test_next_bar_open_is_entry_clock_and_slippage_reduces_r():
    cand = _candidate("LONG")
    bars = [
        _bar(0, o=99.8, h=100.6, l=99.5, c=100.4),
        _bar(1, o=100.5, h=101.6, l=100.2, c=101.2),
    ]
    row = simulate(
        cand,
        bars,
        geometry="O1_EVENT_LEVEL",
        slippage_label="base",
        slippage_dollars=0.01,
    )
    assert row.decision_open == 100.5
    assert row.fill_entry == 100.51
    assert row.decision_open != cand.trigger_close
    assert row.status == "RESOLVED"
    assert row.exit_reason == "TARGET"
    assert row.realized_r is not None and row.realized_r < 2.0


def test_same_bar_both_hit_resolves_stop_first_long_and_short():
    long = _candidate("LONG")
    long_bars = [
        _bar(0, o=99.8, h=100.6, l=99.5, c=100.4),
        _bar(1, o=100.5, h=102.0, l=99.0, c=100.5),
    ]
    long_row = simulate(
        long,
        long_bars,
        geometry="O1_EVENT_LEVEL",
        slippage_label="stress",
        slippage_dollars=0.03,
    )
    assert long_row.result == "LOSS"
    assert long_row.exit_reason == "STOP"

    short = _candidate("SHORT")
    short_bars = [
        _bar(0, o=100.2, h=100.6, l=99.4, c=99.6),
        _bar(1, o=99.5, h=101.0, l=98.0, c=99.5),
    ]
    short_row = simulate(
        short,
        short_bars,
        geometry="O1_EVENT_LEVEL",
        slippage_label="stress",
        slippage_dollars=0.03,
    )
    assert short_row.result == "LOSS"
    assert short_row.exit_reason == "STOP"


def test_level_geometry_fails_closed_when_decision_price_is_wrong_side():
    cand = _candidate("LONG")
    bars = [
        _bar(0, o=99.8, h=100.6, l=99.5, c=100.4),
        _bar(1, o=99.5, h=99.8, l=99.2, c=99.6),
    ]
    row = simulate(
        cand,
        bars,
        geometry="O1_EVENT_LEVEL",
        slippage_label="base",
        slippage_dollars=0.01,
    )
    assert row.status == "BRACKET_INVALID"


def _cell(*, n=120, mean_r=0.2, pf=1.2, net_r=24.0, share=0.2):
    return {
        "halves": {
            "H1": {"n": n, "mean_r": mean_r, "pf": pf, "net_r": net_r},
            "H2": {"n": n, "mean_r": mean_r, "pf": pf, "net_r": net_r},
        },
        "net_r": net_r * 2,
        "top_positive_ticker_share": share,
    }


def test_gate_requires_stress_strength_in_both_halves_and_low_concentration():
    family = "PDH_RECLAIM_LONG"
    geometry = "O1_EVENT_LEVEL"
    report = {
        "families": {
            family: {
                "cells": {
                    f"{geometry}:base": _cell(),
                    f"{geometry}:stress": _cell(),
                }
            }
        }
    }
    good = gate_family(report, family, geometry)
    assert good["passes"] is True
    assert good["classification"] == "PROMISING_BUT_UNPROVEN"

    report["families"][family]["cells"][f"{geometry}:stress"]["halves"]["H2"]["pf"] = 1.0
    bad = gate_family(report, family, geometry)
    assert bad["passes"] is False
    assert "H2:stress_mean_or_pf" in bad["reasons"]

def _full_session(session, base_price: float) -> list[Bar]:
    bars = []
    for i in range(session.minutes // 5):
        px = base_price + i * 0.01
        bars.append(
            Bar(
                start=session.open + timedelta(minutes=5 * i),
                open=px,
                high=px + 0.05,
                low=px - 0.05,
                close=px + 0.01,
                volume=1000.0,
                vwap=px,
            )
        )
    return bars


def test_missing_immediate_prior_session_does_not_fall_back_to_stale_day():
    s1 = nyse_session_for(datetime(2026, 6, 15).date())
    s2 = nyse_session_for(datetime(2026, 6, 16).date())
    s3 = nyse_session_for(datetime(2026, 6, 17).date())
    assert s1 and s2 and s3

    # Day 2 is intentionally absent. Day 3 must fail closed instead of using
    # Day 1 as a stale previous-day high/low source.
    bars = [*_full_session(s1, 100.0), *_full_session(s3, 102.0)]
    candidates, skipped = collect_candidates(
        "AAPL",
        bars,
        [s1, s2, s3],
        study_start=s3.date,
        study_end=s3.date,
        spy_bars=None,
        qqq_bars=None,
    )
    assert candidates == []
    assert skipped["no_prior"] == 1
