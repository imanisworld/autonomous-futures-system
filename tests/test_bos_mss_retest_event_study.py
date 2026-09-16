from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from research.bos_mss_retest_event_study import (
    NO_RETEST,
    RETEST_FAIL,
    RETEST_HOLD,
    aggregate_5m_to_15m,
    detect_structure_events,
    first_retest,
    forward_excursions,
    summarize_records,
)

BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def five(i: int, *, o: float, h: float, l: float, c: float) -> dict:
    ts = BASE + timedelta(minutes=5 * i)
    return {
        "timestamp": ts.isoformat(),
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "volume": 1,
    }


def fifteen(i: int, *, h: float, l: float, c: float) -> dict:
    ts = BASE + timedelta(minutes=15 * i)
    return {
        "timestamp": ts.isoformat(),
        "close_ts": (ts + timedelta(minutes=15)).isoformat(),
        "open": c,
        "high": h,
        "low": l,
        "close": c,
    }


def event(*, direction: str = "LONG", level: float = 100.0, at_minute: int = 30) -> dict:
    return {
        "event_type": "BOS",
        "direction": direction,
        "event_ts": (BASE + timedelta(minutes=at_minute)).isoformat(),
        "event_price": 102.0 if direction == "LONG" else 98.0,
        "broken_level": level,
    }


def test_aggregate_5m_to_15m_requires_complete_exact_triples():
    rows = [
        five(0, o=100, h=102, l=99, c=101),
        five(1, o=101, h=103, l=100, c=102),
        five(2, o=102, h=104, l=101, c=103),
        five(3, o=103, h=105, l=102, c=104),
        five(5, o=104, h=106, l=103, c=105),
    ]
    bars, meta = aggregate_5m_to_15m(rows)
    assert len(bars) == 1
    assert bars[0]["open"] == 100
    assert bars[0]["high"] == 104
    assert bars[0]["low"] == 99
    assert bars[0]["close"] == 103
    assert bars[0]["close_ts"] == "2026-01-01T00:15:00+00:00"
    assert meta == {
        "fine_rows": 5,
        "complete_15m_bars": 1,
        "skipped_incomplete_15m_buckets": 1,
    }


def test_aggregate_duplicate_timestamp_fails_closed():
    row = five(0, o=100, h=101, l=99, c=100)
    with pytest.raises(ValueError, match="duplicate 5m timestamp"):
        aggregate_5m_to_15m([row, dict(row)])


def test_structure_state_suppresses_first_break_then_emits_bos_and_opposite_mss():
    bars = [
        fifteen(0, h=10, l=5, c=7),
        fifteen(1, h=12, l=6, c=8),
        fifteen(2, h=11, l=6.5, c=9),
        fifteen(3, h=13, l=7, c=13),
        fifteen(4, h=15, l=8, c=14),
        fifteen(5, h=14, l=9, c=13),
        fifteen(6, h=16, l=10, c=16),
        fifteen(7, h=17, l=11, c=15),
        fifteen(8, h=16, l=9, c=14),
        fifteen(9, h=15, l=10, c=13),
        fifteen(10, h=14, l=8, c=8),
    ]
    events = detect_structure_events(bars, swing=1)
    assert [(e["event_type"], e["direction"], e["broken_level"]) for e in events] == [
        ("BOS", "LONG", 15.0),
        ("MSS", "SHORT", 9.0),
    ]
    assert events[0]["event_bar_ts"] == "2026-01-01T01:30:00+00:00"
    assert events[0]["event_ts"] == "2026-01-01T01:45:00+00:00"


def test_equal_extreme_pivot_tie_is_ignored_fail_closed():
    bars = [
        fifteen(0, h=10, l=5, c=7),
        fifteen(1, h=12, l=6, c=8),
        fifteen(2, h=12, l=7, c=9),
        fifteen(3, h=13, l=8, c=13),
    ]
    assert detect_structure_events(bars, swing=1) == []


def test_first_retest_ignores_pre_event_touch_and_classifies_hold():
    ev = event(direction="LONG", level=100.0, at_minute=30)
    rows = [
        five(5, o=101, h=102, l=99, c=101),
        five(6, o=102, h=104, l=101, c=103),
        five(7, o=103, h=104, l=99.5, c=101),
    ]
    result = first_retest(ev, rows, max_minutes=120)
    assert result["retest_status"] == RETEST_HOLD
    assert result["retest_bar_ts"] == "2026-01-01T00:35:00+00:00"
    assert result["retest_ts"] == "2026-01-01T00:40:00+00:00"
    assert result["retest_latency_minutes"] == 10


def test_first_retest_first_touch_can_fail_and_later_hold_does_not_replace_it():
    ev = event(direction="SHORT", level=100.0, at_minute=30)
    rows = [
        five(6, o=98, h=99, l=97, c=98),
        five(7, o=98, h=101, l=97, c=100.5),
        five(8, o=100, h=101, l=98, c=99),
    ]
    result = first_retest(ev, rows)
    assert result["retest_status"] == RETEST_FAIL
    assert result["retest_bar_ts"] == "2026-01-01T00:35:00+00:00"


def test_first_retest_reports_no_retest_inside_horizon():
    ev = event(direction="LONG", level=100.0, at_minute=30)
    rows = [
        five(6, o=103, h=104, l=102, c=103),
        five(7, o=103, h=105, l=101, c=104),
    ]
    assert first_retest(ev, rows, max_minutes=10)["retest_status"] == NO_RETEST


def test_forward_excursions_are_directional_and_post_signal_only():
    rows = [
        five(5, o=100, h=500, l=1, c=100),
        five(6, o=100, h=103, l=99, c=102),
        five(7, o=102, h=105, l=101, c=104),
        five(8, o=104, h=106, l=103, c=105),
    ]
    long = forward_excursions(
        direction="LONG",
        reference_price=100,
        start_ts=(BASE + timedelta(minutes=30)).isoformat(),
        fine_5m_rows=rows,
        horizons_minutes=(15,),
    )["15"]
    assert long == {
        "bars": 3,
        "signed_close_points": 5.0,
        "mfe_points": 6.0,
        "mae_points": 1.0,
    }
    short = forward_excursions(
        direction="SHORT",
        reference_price=106,
        start_ts=(BASE + timedelta(minutes=30)).isoformat(),
        fine_5m_rows=rows,
        horizons_minutes=(15,),
    )["15"]
    assert short["signed_close_points"] == 1.0
    assert short["mfe_points"] == 7.0
    assert short["mae_points"] == 0.0


def test_summary_is_explicitly_event_only_and_not_a_trade_claim():
    rows = [
        {
            "event_type": "BOS",
            "direction": "LONG",
            "retest_status": RETEST_HOLD,
            "event_forward": {
                "15": {"signed_close_points": 2.0, "mfe_points": 3.0, "mae_points": 1.0}
            },
            "retest_forward": {
                "15": {"signed_close_points": 1.0, "mfe_points": 2.0, "mae_points": 0.5}
            },
        },
        {
            "event_type": "MSS",
            "direction": "SHORT",
            "retest_status": RETEST_FAIL,
            "event_forward": {
                "15": {"signed_close_points": -1.0, "mfe_points": 0.5, "mae_points": 2.0}
            },
            "retest_forward": None,
        },
    ]
    report = summarize_records(rows, horizons_minutes=(15,))
    assert report["authority"] == "research_event_study_only"
    assert report["trade_strategy_defined"] is False
    assert report["choch_claimed"] is False
    assert report["by_event_type"] == {"BOS": 1, "MSS": 1}
    assert report["retest_counts"] == {
        RETEST_HOLD: 1,
        RETEST_FAIL: 1,
        NO_RETEST: 0,
    }
    assert report["event_forward"]["15"]["n"] == 2
    assert report["retest_hold_forward"]["15"]["n"] == 1


def test_research_module_has_no_runtime_execution_import_surface():
    path = Path(__file__).resolve().parents[1] / "research/bos_mss_retest_event_study.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    forbidden_prefixes = (
        "execution",
        "risk",
        "webhook",
        "strategy.signal_engine",
        "requests",
        "httpx",
        "tradovate",
    )
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    assert not [
        name for name in imported
        if any(name == prefix or name.startswith(prefix + ".") for prefix in forbidden_prefixes)
    ]
