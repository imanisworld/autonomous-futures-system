from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from research.replay_4hr_market_counterfactual import _attach_regime
from research.replay_4hr_retrigger_honest import replay_one


ET = ZoneInfo("America/New_York")


def dt(hour, minute=0):
    return datetime(2026, 1, 6, hour, minute, tzinfo=ET)


def bar(ts, o, h, l, c):
    return {"ts": ts, "open": o, "high": h, "low": l, "close": c}


def signal(direction="LONG", target=None):
    return {
        "direction": direction,
        "entry_trigger": 100.0,
        "target": target if target is not None else (120.0 if direction == "LONG" else 80.0),
        "entry_window_open": dt(9, 30).isoformat(),
        "entry_window_close": dt(11).isoformat(),
    }


def hours():
    return [
        bar(dt(8), 100, 105, 95, 101),
        bar(dt(9), 101, 106, 96, 102),
    ]


def test_market_mode_fills_beyond_ioc_cap_with_adverse_slippage():
    bars = [
        bar(dt(9, 30), 99, 115, 99, 110),
        bar(dt(9, 35), 110, 120, 109, 119),
    ]
    ioc = replay_one(
        eval_date=date(2026, 1, 6),
        signal=signal(),
        bars_5m=bars,
        bars_1h=hours(),
        slippage_ticks=2,
    )
    market = replay_one(
        eval_date=date(2026, 1, 6),
        signal=signal(),
        bars_5m=bars,
        bars_1h=hours(),
        slippage_ticks=2,
        entry_model="market",
    )
    assert ioc["status"] == "IOC_CANCELLED"
    assert market["filled"]
    assert market["entry_fill"] == 110.5


def test_market_mode_fails_closed_when_displaced_fill_passes_target():
    out = replay_one(
        eval_date=date(2026, 1, 6),
        signal=signal(target=108),
        bars_5m=[bar(dt(9, 30), 99, 115, 99, 110)],
        bars_1h=hours(),
        slippage_ticks=2,
        entry_model="market",
    )
    assert not out["filled"]
    assert out["status"] == "INVALID_BRACKET_AT_FILL"
    assert out["invalid_bracket_reason"] == "TARGET_ALREADY_PASSED"
    assert out["modeled_entry_fill"] == 110.5


def test_market_mode_fails_closed_when_stop_is_non_protective():
    high_stop_hours = [
        bar(dt(8), 112, 116, 111, 115),
        bar(dt(9), 115, 117, 113, 116),
    ]
    out = replay_one(
        eval_date=date(2026, 1, 6),
        signal=signal(),
        bars_5m=[bar(dt(9, 30), 99, 115, 99, 110)],
        bars_1h=high_stop_hours,
        slippage_ticks=2,
        entry_model="market",
    )
    assert not out["filled"]
    assert out["status"] == "INVALID_BRACKET_AT_FILL"
    assert out["invalid_bracket_reason"] == "NON_PROTECTIVE_STOP"


def test_regime_join_uses_exact_causal_entry_timestamp():
    row = {"entry_bar_ts": dt(9, 30).isoformat()}
    key = dt(9, 30).astimezone(timezone.utc)
    out = _attach_regime(
        row,
        {
            key: {
                "market_condition": "TRENDING",
                "trend_direction": "UP",
                "trend_strength": "STRONG",
            }
        },
    )
    assert out["market_condition"] == "TRENDING"
    assert out["trend_direction"] == "UP"


def test_regime_join_fails_closed_on_missing_timestamp():
    with pytest.raises(ValueError, match="missing historical regime row"):
        _attach_regime({"entry_bar_ts": dt(9, 30).isoformat()}, {})
