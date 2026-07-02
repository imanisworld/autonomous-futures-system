"""Tests for context/live_direction.py — live daily/4H direction from price.

The motivating case throughout: 2026-07-02, where payload daily/4H labels read
UP through an all-afternoon FULL_SHORT selloff. Live computation must flip
DOWN as soon as price takes the level — not after the higher-TF bar closes.
"""

from datetime import datetime, timezone

from context.live_direction import (
    apply_live_direction,
    daily_direction_live,
    four_hour_direction_live,
)
from context.market_context import HTFContext


# ── daily ────────────────────────────────────────────────────────────────────

def test_daily_above_pdh_is_up():
    assert daily_direction_live(7605.0, 7600.0, 7500.0, 7550.0) == "UP"


def test_daily_below_pdl_is_down():
    assert daily_direction_live(7495.0, 7600.0, 7500.0, 7550.0) == "DOWN"


def test_daily_inside_range_leans_on_prev_close():
    # Well above prior close but inside range → UP lean
    assert daily_direction_live(7580.0, 7600.0, 7500.0, 7550.0) == "UP"
    # Well below prior close but inside range → DOWN lean
    assert daily_direction_live(7520.0, 7600.0, 7500.0, 7550.0) == "DOWN"


def test_daily_near_prev_close_is_neutral():
    # Within the lean buffer (0.05% of 7550 ≈ 3.8 pts) → NEUTRAL, not noise
    assert daily_direction_live(7551.0, 7600.0, 7500.0, 7550.0) == "NEUTRAL"
    assert daily_direction_live(7549.0, 7600.0, 7500.0, 7550.0) == "NEUTRAL"


def test_daily_missing_inputs_returns_none():
    assert daily_direction_live(None, 7600.0, 7500.0, 7550.0) is None
    assert daily_direction_live(7550.0, None, 7500.0, 7550.0) is None
    assert daily_direction_live(7550.0, 7600.0, None, 7550.0) is None
    assert daily_direction_live(7550.0, 7600.0, 7500.0, None) is None


def test_daily_turns_down_intraday_even_after_up_open():
    """The 07-02 shape: opens strong (above PDH), then collapses below PDL.

    A completed-bar label stays UP all day; the live read must flip.
    """
    pdh, pdl, pdc = 7560.0, 7510.0, 7540.0
    assert daily_direction_live(7585.0, pdh, pdl, pdc) == "UP"      # morning pop
    assert daily_direction_live(7530.0, pdh, pdl, pdc) == "DOWN"    # lean flips
    assert daily_direction_live(7495.0, pdh, pdl, pdc) == "DOWN"    # range break


# ── four hour ────────────────────────────────────────────────────────────────

def _bar(hour, minute, high, low, close, day=2):
    ts = datetime(2026, 7, day, hour, minute, tzinfo=timezone.utc)
    return {"ts": ts.isoformat(), "high": high, "low": low, "close": close}


def _now(hour, minute=0, day=2):
    return datetime(2026, 7, day, hour, minute, tzinfo=timezone.utc)


def test_four_hour_break_of_prior_window_high_is_up():
    # Prior window 08:00–12:00 UTC: high 7560, low 7540, close 7550
    bars = [_bar(8, 0, 7555, 7540, 7550), _bar(11, 45, 7560, 7548, 7550)]
    assert four_hour_direction_live(bars, 7565.0, _now(13)) == "UP"


def test_four_hour_break_of_prior_window_low_is_down():
    bars = [_bar(8, 0, 7555, 7540, 7550), _bar(11, 45, 7560, 7548, 7550)]
    assert four_hour_direction_live(bars, 7535.0, _now(13)) == "DOWN"


def test_four_hour_inside_prior_window_leans_on_its_close():
    bars = [_bar(8, 0, 7560, 7500, 7530)]
    assert four_hour_direction_live(bars, 7545.0, _now(13)) == "UP"
    assert four_hour_direction_live(bars, 7515.0, _now(13)) == "DOWN"
    assert four_hour_direction_live(bars, 7531.0, _now(13)) == "NEUTRAL"


def test_four_hour_ignores_current_window_bars():
    # Bars inside the current window must not become their own reference
    bars = [
        _bar(8, 0, 7560, 7540, 7550),
        _bar(12, 15, 7600, 7595, 7598),  # current window (12:00–16:00)
    ]
    # close 7570 is below the current-window bar but above prior high → UP
    assert four_hour_direction_live(bars, 7570.0, _now(13)) == "UP"


def test_four_hour_skips_empty_buckets_to_last_populated_window():
    # Maintenance-halt gap: nothing 12:00–16:00; prior populated window is 08–12
    bars = [_bar(9, 0, 7560, 7540, 7550)]
    assert four_hour_direction_live(bars, 7565.0, _now(17)) == "UP"


def test_four_hour_no_history_returns_none():
    assert four_hour_direction_live([], 7550.0, _now(13)) is None
    # Only current-window bars → still no prior reference
    bars = [_bar(12, 15, 7560, 7540, 7550)]
    assert four_hour_direction_live(bars, 7550.0, _now(13)) is None


def test_four_hour_bad_timestamps_skipped():
    bars = [
        {"ts": "not-a-time", "high": 7560, "low": 7540, "close": 7550},
        _bar(9, 0, 7561, 7541, 7551),
    ]
    assert four_hour_direction_live(bars, 7570.0, _now(13)) == "UP"


def test_four_hour_epoch_timestamps_accepted():
    ts = int(datetime(2026, 7, 2, 9, 0, tzinfo=timezone.utc).timestamp())
    bars = [{"ts": str(ts), "high": 7560.0, "low": 7540.0, "close": 7550.0}]
    assert four_hour_direction_live(bars, 7565.0, _now(13)) == "UP"


def test_four_hour_selloff_flips_down_next_window():
    """07-02 MNQ shape: up-window, then price collapses through its low."""
    bars = [
        _bar(8, 0, 30400, 30300, 30380),
        _bar(11, 45, 30450, 30350, 30400),
    ]
    assert four_hour_direction_live(bars, 30460.0, _now(12, 30)) == "UP"
    assert four_hour_direction_live(bars, 30340.0, _now(15, 0)) == "DOWN"


# ── apply_live_direction ─────────────────────────────────────────────────────

class _Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _state(close=7530.0, htf=None):
    return _Obj(
        timestamp=_now(15),
        ohlc=_Obj(close=close),
        previous_day=_Obj(high=7560.0, low=7510.0, close=7540.0),
        htf=htf,
    )


def test_apply_overrides_payload_directions_keeps_rest():
    htf = HTFContext(
        daily_bar_type="2U",
        daily_direction="UP",           # stale payload label
        four_hour_bar_type="2U",
        four_hour_direction="UP",       # stale payload label
        ftfc_direction="UP",
        ftfc_aligned=True,
    )
    bars = [_bar(8, 0, 7555, 7540, 7550), _bar(11, 45, 7560, 7548, 7550)]
    state = _state(close=7530.0, htf=htf)
    apply_live_direction(state, bars)
    assert state.htf.daily_direction == "DOWN"        # live lean vs PDC
    assert state.htf.four_hour_direction == "DOWN"    # broke prior 4h low
    assert state.htf.direction_source == "live"
    # payload-owned fields untouched
    assert state.htf.daily_bar_type == "2U"
    assert state.htf.ftfc_direction == "UP"
    assert state.htf.ftfc_aligned is True


def test_apply_creates_htf_when_payload_had_none():
    state = _state(close=7495.0, htf=None)
    apply_live_direction(state, [])
    assert state.htf is not None
    assert state.htf.daily_direction == "DOWN"        # below PDL
    assert state.htf.four_hour_direction is None      # no history
    assert state.htf.direction_source == "live"


def test_apply_never_mixes_in_stale_payload_value_when_live_unavailable():
    htf = HTFContext(four_hour_direction="UP")
    state = _state(htf=htf)
    apply_live_direction(state, [])                   # no bar history
    assert state.htf.four_hour_direction is None      # not the stale UP


# ── config plumbing ──────────────────────────────────────────────────────────

def test_settings_default_and_env_override(monkeypatch):
    from config.settings import load_config

    monkeypatch.delenv("HTF_DIRECTION_SOURCE", raising=False)
    assert load_config().htf_direction_source == "payload"
    monkeypatch.setenv("HTF_DIRECTION_SOURCE", "LIVE")
    assert load_config().htf_direction_source == "live"


def test_settings_rejects_invalid_source(monkeypatch):
    import pytest
    from config.settings import ConfigError, load_config

    monkeypatch.setenv("HTF_DIRECTION_SOURCE", "psychic")
    with pytest.raises(ConfigError):
        load_config()
