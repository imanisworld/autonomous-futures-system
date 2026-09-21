from __future__ import annotations

import ast
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from alert_ranker.causal_bars import Bar
from alert_ranker.non_strat_coverage import (
    OBSERVER_VERSION,
    NonStratEvent,
    assign_episode_ids,
    measure_outcomes,
    observe_session,
)


BASE = datetime(2026, 9, 18, 13, 30, tzinfo=timezone.utc)


def _bar(i, *, o=100.0, h=101.0, l=99.0, c=100.0, v=100.0, vw=None):
    return Bar(
        start=BASE + timedelta(minutes=5 * i),
        open=o,
        high=h,
        low=l,
        close=c,
        volume=v,
        vwap=c if vw is None else vw,
    )


def _prior():
    return [
        Bar(
            start=BASE - timedelta(days=1) + timedelta(minutes=5 * i),
            open=100.0,
            high=105.0 if i == 2 else 103.0,
            low=95.0 if i == 3 else 97.0,
            close=100.0,
            volume=100.0,
            vwap=100.0,
        )
        for i in range(6)
    ]


def _families(events):
    return {event.family for event in events}


def test_vwap_reclaim_and_immediate_failed_reclaim_are_distinct():
    bars = [
        _bar(0, h=100.0, l=98.5, c=99.0, vw=100.0),
        _bar(1, h=101.5, l=99.0, c=101.0, vw=100.0),
        _bar(2, h=101.0, l=98.0, c=99.0, vw=100.0),
    ]
    events = observe_session(
        symbol="AAPL",
        session_date="2026-09-18",
        prior_session_bars=_prior(),
        session_bars=bars,
    )
    assert "VWAP_RECLAIM_LONG" in _families(events)
    assert "VWAP_FAILED_RECLAIM_SHORT" in _families(events)


def test_vwap_test_hold_does_not_require_a_new_cross():
    bars = [
        _bar(0, h=102.0, l=100.5, c=101.0, vw=100.0),
        _bar(1, h=102.0, l=99.5, c=101.2, vw=100.0),
    ]
    events = observe_session(
        symbol="AAPL",
        session_date="2026-09-18",
        prior_session_bars=_prior(),
        session_bars=bars,
    )
    assert "VWAP_TEST_HOLD_LONG" in _families(events)
    assert "VWAP_RECLAIM_LONG" not in _families(events)


def test_pdh_break_and_later_retest_are_recorded_separately():
    bars = [
        _bar(0, h=104.5, l=103.0, c=104.0, vw=103.8),
        _bar(1, h=106.5, l=104.0, c=106.0, vw=104.5),
        _bar(2, h=107.0, l=104.5, c=106.5, vw=105.0),
    ]
    events = observe_session(
        symbol="AAPL",
        session_date="2026-09-18",
        prior_session_bars=_prior(),
        session_bars=bars,
    )
    families = _families(events)
    assert "PDH_RECLAIM_LONG" in families
    assert "PDH_BREAK_RETEST_LONG" in families


def test_pdl_break_and_rejection_are_observed():
    break_bars = [
        _bar(0, h=97.0, l=95.5, c=96.0, vw=96.5),
        _bar(1, h=96.0, l=93.5, c=94.0, vw=95.0),
        _bar(2, h=95.5, l=93.5, c=94.5, vw=94.8),
    ]
    events = observe_session(
        symbol="AAPL",
        session_date="2026-09-18",
        prior_session_bars=_prior(),
        session_bars=break_bars,
    )
    assert "PDL_RECLAIM_SHORT" in _families(events)
    assert "PDL_BREAK_RETEST_SHORT" in _families(events)

    rejection_bars = [
        _bar(0, h=98.0, l=96.0, c=97.0, vw=97.0),
        _bar(1, h=98.0, l=94.0, c=96.0, vw=96.5),
    ]
    rejected = observe_session(
        symbol="AAPL",
        session_date="2026-09-18",
        prior_session_bars=_prior(),
        session_bars=rejection_bars,
    )
    assert "PDL_REJECTION_LONG" in _families(rejected)


def test_orb_breakout_retest_and_rejection_wait_for_first_30_minutes():
    opening = [
        _bar(0, h=104, l=99, c=101),
        _bar(1, h=105, l=100, c=102),
        _bar(2, h=106, l=98, c=103),
        _bar(3, h=107, l=97, c=104),
        _bar(4, h=108, l=96, c=105),
        _bar(5, h=110, l=95, c=109),
    ]
    breakout = [
        *opening,
        _bar(6, h=112, l=109, c=111),
        _bar(7, h=112, l=109.5, c=111.5),
    ]
    events = observe_session(
        symbol="AAPL",
        session_date="2026-09-18",
        prior_session_bars=_prior(),
        session_bars=breakout,
    )
    families = _families(events)
    assert "ORB_BREAKOUT_LONG" in families
    assert "ORB_BREAK_RETEST_LONG" in families

    rejection = [
        *opening,
        _bar(6, h=111, l=108, c=109),
    ]
    rejected = observe_session(
        symbol="AAPL",
        session_date="2026-09-18",
        prior_session_bars=_prior(),
        session_bars=rejection,
    )
    assert "ORB_REJECTION_SHORT" in _families(rejected)


def _event(start, *, family="PDH_RECLAIM_LONG", direction="LONG"):
    return NonStratEvent(
        observer_id="OPTIONS_NON_STRAT_COVERAGE",
        observer_version=OBSERVER_VERSION,
        symbol="AAPL",
        timeframe="5m",
        session_date="2026-09-18",
        bar_start=start.isoformat(),
        bar_close=(start + timedelta(minutes=5)).isoformat(),
        family=family,
        direction=direction,
        level_name="PDH",
        level_value=100.0,
        trigger_price=100.0,
        vwap=99.5,
        ema20=99.0,
        volume_ratio=1.3,
        spy_trend="bullish",
        qqq_trend="bullish",
        market_aligned=True,
        earliest_sip_visibility=(start + timedelta(minutes=21)).isoformat(),
        source_rule="test",
    )


def test_episode_ids_only_merge_contiguous_same_family_direction_and_level():
    e1 = _event(BASE)
    e2 = _event(BASE + timedelta(minutes=5))
    e3 = _event(BASE + timedelta(minutes=15))
    stamped = assign_episode_ids([e1, e2, e3])
    assert stamped[0].episode_id == stamped[1].episode_id
    assert stamped[2].episode_id != stamped[1].episode_id


def test_outcomes_measure_forward_bars_only_and_preserve_direction():
    bars = [
        _bar(0, h=100.5, l=99.5, c=100.0),
        _bar(1, h=102.0, l=99.0, c=101.0),
        _bar(2, h=103.0, l=100.0, c=102.0),
        _bar(3, h=104.0, l=101.0, c=103.0),
    ]
    long_event = replace(_event(bars[0].start_utc), episode_id="long-1")
    short_event = replace(
        _event(bars[0].start_utc, family="PDH_REJECTION_SHORT", direction="SHORT"),
        episode_id="short-1",
    )
    rows = measure_outcomes(
        [long_event, short_event],
        {"AAPL": bars},
        horizons_minutes=(15,),
    )
    long_15 = next(row for row in rows if row.episode_id == "long-1" and row.horizon == "15m")
    short_15 = next(row for row in rows if row.episode_id == "short-1" and row.horizon == "15m")
    assert long_15.bars_observed == 3
    assert long_15.close_return_bps > 0
    assert long_15.mfe_bps > 0
    assert long_15.mae_bps > 0
    assert short_15.close_return_bps < 0


def test_observer_module_has_no_execution_or_broker_imports():
    path = Path("alert_ranker/non_strat_coverage.py")
    tree = ast.parse(path.read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert imported.isdisjoint(
        {"execution", "risk", "webhook", "notifications", "options_manager"}
    )


def test_cli_is_bound_to_dedicated_observer_database():
    source = Path("scripts/options_non_strat_coverage.py").read_text()
    assert "options_non_strat_coverage.sqlite" in source
    assert "options_scanner.sqlite" not in source
