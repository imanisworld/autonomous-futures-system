from __future__ import annotations

from datetime import datetime, timedelta, timezone

from scripts.mfe_study import _analyze_trade
from sources.polygon_client import PolygonBar


def _bar(minute, o, h, l, c):
    base = datetime(2026, 6, 1, 14, 0, tzinfo=timezone.utc)
    return PolygonBar(ts=base + timedelta(minutes=minute), open=o, high=h, low=l,
                      close=c, volume=1.0, ticker="X")


def _trade(direction, entry, stop, target):
    return {"instrument": "MNQ", "entry_ts": "2026-06-01T14:00:00+00:00",
            "direction": direction, "entry": entry, "stop": stop, "target": target}


def test_long_hits_target_with_mfe_and_continuation():
    # entry 100, stop 90, target 120 → risk 10, target 2R.
    # runs to 124 (target hit, MFE 2.4R), then continues to 135 (+1.5R past target).
    bars = [
        _bar(0, 100, 105, 99, 104),    # MFE 0.5R, MAE 0.1R
        _bar(1, 104, 124, 103, 121),   # target 120 hit; high 124 → MFE 2.4R
        _bar(2, 121, 135, 120, 134),   # +15m continuation: 135 → 1.5R beyond target
    ]
    r = _analyze_trade(_trade("LONG", 100, 90, 120), bars, max_hold_min=480)
    assert r["outcome"] == "TARGET"
    assert round(r["mfe_R"], 2) == 2.40
    assert round(r["target_R"], 2) == 2.00
    assert round(r["cont_R"][15], 2) == 1.50
    assert r["reached_2R"] is True


def test_short_giveback_reached_1R_then_stopped():
    # SHORT entry 100, stop 110, target 80 → risk 10. Price drops to 92 (MFE 0.8R)...
    # actually reach 88 (1.2R) then reverses up through stop 110 → giveback.
    bars = [
        _bar(0, 100, 101, 88, 95),     # favourable: entry-low = 12 → 1.2R
        _bar(1, 95, 112, 94, 111),     # high 112 >= stop 110 → STOP
    ]
    r = _analyze_trade(_trade("SHORT", 100, 110, 80), bars, max_hold_min=480)
    assert r["outcome"] == "STOP"
    assert r["reached_1R"] is True
    assert round(r["mfe_R"], 2) == 1.20


def test_same_bar_stop_and_target_books_stop_first():
    # A bar spanning both stop and target is conservatively booked as STOP.
    bars = [
        _bar(0, 100, 100, 100, 100),
        _bar(1, 100, 125, 85, 100),    # spans target 120 AND stop 90
    ]
    r = _analyze_trade(_trade("LONG", 100, 90, 120), bars, max_hold_min=480)
    assert r["outcome"] == "STOP"
    assert r["ambiguous"] is True


def test_timeout_when_neither_hit():
    bars = [_bar(i, 100, 101, 99, 100) for i in range(5)]
    r = _analyze_trade(_trade("LONG", 100, 90, 120), bars, max_hold_min=480)
    assert r["outcome"] == "TIMEOUT"


def test_zero_risk_trade_skipped():
    bars = [_bar(0, 100, 101, 99, 100), _bar(1, 100, 101, 99, 100)]
    assert _analyze_trade(_trade("LONG", 100, 100, 120), bars, max_hold_min=480) is None
