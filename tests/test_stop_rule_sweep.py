from __future__ import annotations

from datetime import datetime, timedelta, timezone

from scripts.stop_rule_sweep import _simulate

# MNQ point value = 0.50/0.25 = 2.0 $/point.
BASE = datetime(2026, 6, 1, 14, 0, tzinfo=timezone.utc)


def _c(minute, hi, lo, close):
    return (BASE + timedelta(minutes=minute), hi, lo, close)


def _trade(direction="LONG", entry=100.0, stop=90.0, target=120.0):
    return {"instrument": "MNQ", "entry_ts": BASE.isoformat(),
            "direction": direction, "entry": entry, "stop": stop, "target": target}


def test_static_target_hit():
    candles = [_c(15, 121, 105, 118), _c(30, 122, 119, 120)]
    assert _simulate(_trade(), candles, "static") == (120 - 100) * 2.0


def test_static_stop_hit():
    candles = [_c(15, 102, 88, 92), _c(30, 95, 90, 93)]
    assert _simulate(_trade(), candles, "static") == (90 - 100) * 2.0


def test_be_1R_moves_stop_to_entry_and_saves_trade():
    # bar1 runs to +1.2R (high 112) but no exit; bar2 dips to entry -> BE stop 100;
    # bar3 would have hit the original stop 90. Static loses, BE scratches.
    candles = [_c(15, 112, 105, 108), _c(30, 108, 100, 101), _c(45, 95, 88, 90)]
    assert _simulate(_trade(), candles, "be_1R") == 0.0          # exits at entry
    assert _simulate(_trade(), candles, "static") == (90 - 100) * 2.0  # full stop


def test_runner_trail_exits_above_entry_with_no_target():
    # Target dropped; price runs to 118 then 119, trail (1R=10) sits at 118-10=108;
    # bar2 low 108 taps the trailed stop -> exit at 108 = +16 points.
    candles = [_c(15, 118, 105, 116), _c(30, 119, 108, 110)]
    assert _simulate(_trade(), candles, "run_trail_1R") == (108 - 100) * 2.0


def test_short_static_target():
    # SHORT entry 100, target 80, stop 110. Bar prints low 79 -> target.
    candles = [_c(15, 101, 79, 82), _c(30, 90, 80, 85)]
    assert _simulate(_trade("SHORT", 100, 110, 80), candles, "static") == (100 - 80) * 2.0
