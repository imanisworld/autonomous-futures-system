"""Daily paper lane: entry timing + target floor (2026-09-14 operator rulings).

Two things made every gap-through day a late entry for the Daily lane:

1. The lane required the session-hourly candle to be a 2U/2D in the setup's
   direction. That candle is rebuilt from two completed session hours, so it
   cannot exist before ~11:46 ET once the SIP delay is added. On 2026-09-11
   AAPL/SPY were detected with valid targets at 10:20 ET but only became
   TRIGGERED at 11:50 / 13:20 ET (`hourly=missing` until then), by which time
   price was past the target. The hourly requirement is now off by default
   for the Daily lane (SPY/QQQ trend alignment stays); the old behaviour is
   one env flag away.

2. The target finder took the nearest prior-day level, which on those rows
   sat 0.16R / 0.24R from the trigger. Levels closer than a configurable
   floor (default 1.0R) are now skipped when choosing target_1/target_2.

Both changes are confined to the Daily lane and the target finder input; the
30m lane and the 1H/4H evidence populations are untouched.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from alert_ranker.bar_context import BarContextBuilder, SymbolContext
from alert_ranker.causal_bars import MINUTE_30, Bar
from alert_ranker.config import ScannerConfig, load_config
from alert_ranker.discord import DiscordAlerter
from alert_ranker.scanner import OptionsScanner
from alert_ranker.session_calendar import Session
from alert_ranker.storage import ScanStorage
from options_manager.levels import LevelFinderInputs, find_targets
from strategy.strat_classifier import TWO_UP

ET = ZoneInfo("America/New_York")


# --- target finder: the floor SKIPS, it does not fail ----------------------


def _inputs(**overrides):
    base = dict(
        direction="CALL",
        entry=104.0,
        underlying_invalidation=99.0,  # risk 5.0
        resistance_levels=(106.5, 118.0, 120.0),  # 0.5R, 2.8R, 3.2R
    )
    base.update(overrides)
    return LevelFinderInputs(**base)


def test_floor_skips_nearer_levels_and_keeps_the_next_two():
    result = find_targets(_inputs(min_target_rr=1.0))
    assert result.status == "VALID"
    assert (result.target_1, result.target_2) == (118.0, 120.0)
    assert result.rr_1 == pytest.approx(2.8)
    assert result.warnings == ["skipped_1_level(s)_below_1.0R"]


def test_no_floor_is_the_previous_behaviour_exactly():
    result = find_targets(_inputs())
    assert result.status == "VALID"
    assert (result.target_1, result.target_2) == (106.5, 118.0)
    assert result.rr_1 == pytest.approx(0.5)
    assert result.warnings == []


def test_floor_fails_closed_with_a_distinct_reason_when_nothing_is_far_enough():
    result = find_targets(_inputs(resistance_levels=(105.0, 106.5), min_target_rr=1.0))
    assert result.status == "INVALID"
    assert result.reason_code == "no_target_1_at_min_rr"
    assert result.target_1 is None

    only_one = find_targets(_inputs(resistance_levels=(106.5, 118.0), min_target_rr=1.0))
    assert only_one.status == "INVALID"
    assert only_one.reason_code == "no_target_2_at_min_rr"


def test_floor_is_measured_in_r_from_the_entry_for_puts_too():
    result = find_targets(
        LevelFinderInputs(
            direction="PUT",
            entry=104.0,
            underlying_invalidation=109.0,  # risk 5.0
            support_levels=(102.0, 98.0, 90.0),  # 0.4R, 1.2R, 2.8R
            min_target_rr=1.0,
        )
    )
    assert result.status == "VALID"
    assert (result.target_1, result.target_2) == (98.0, 90.0)


def test_floor_and_legacy_threshold_compose():
    # min_rr_threshold still fails the result when the CHOSEN target_1 is
    # under it; the floor has already moved target_1 out to 2.8R here.
    result = find_targets(_inputs(min_target_rr=1.0, min_rr_threshold=2.0))
    assert result.status == "VALID"
    result = find_targets(_inputs(min_target_rr=1.0, min_rr_threshold=3.0))
    assert result.status == "INVALID"
    assert result.reason_code == "rr_below_threshold"


# --- config -----------------------------------------------------------------


def test_config_defaults_and_env(monkeypatch):
    for key in ("OPTIONS_PAPER_V1_DAILY_MIN_TARGET_RR", "OPTIONS_PAPER_V1_DAILY_REQUIRE_HOURLY"):
        monkeypatch.delenv(key, raising=False)
    cfg = load_config()
    assert cfg.paper_v1_daily_min_target_rr == 1.0
    assert cfg.paper_v1_daily_require_hourly_alignment is False
    monkeypatch.setenv("OPTIONS_PAPER_V1_DAILY_MIN_TARGET_RR", "0")
    monkeypatch.setenv("OPTIONS_PAPER_V1_DAILY_REQUIRE_HOURLY", "true")
    cfg = load_config()
    assert cfg.paper_v1_daily_min_target_rr == 0.0  # explicit 0 = floor OFF
    assert cfg.paper_v1_daily_require_hourly_alignment is True
    monkeypatch.setenv("OPTIONS_PAPER_V1_DAILY_MIN_TARGET_RR", "1.5")
    assert load_config().paper_v1_daily_min_target_rr == 1.5
    monkeypatch.setenv("OPTIONS_PAPER_V1_DAILY_MIN_TARGET_RR", "-1")
    assert load_config().paper_v1_daily_min_target_rr == 1.0  # nonsense -> default


# --- the Daily lane end to end from synthetic 30m bars ---------------------


def _cfg(tmp_path: Path, **overrides) -> ScannerConfig:
    return ScannerConfig(
        market_data_provider="public",
        tastytrade_username="",
        tastytrade_password="",
        tastytrade_base_url="https://api.tastyworks.com",
        public_api_key_configured=True,
        public_base_url="https://api.public.com",
        alpaca_api_key_configured=True,
        alpaca_secret_key_configured=True,
        alpaca_paper=True,
        alpaca_data_base_url="https://data.alpaca.markets",
        port=8010,
        discord_webhook_url="",
        watchlist=["AAPL"],
        interval_minutes=5,
        sqlite_path=tmp_path / "options.sqlite",
        bar_context_enabled=True,
        **overrides,
    )


class _Builder:
    """Just enough of BarContextBuilder for _daily_candidate_from_raw."""

    timeframe = MINUTE_30
    exchange_timezone = "America/New_York"
    _trend = staticmethod(BarContextBuilder._trend)


def _session(day: date) -> Session:
    open_local = datetime.combine(day, datetime.min.time(), tzinfo=ET).replace(hour=9, minute=30)
    close_local = open_local.replace(hour=16, minute=0)
    return Session(day, open_local.astimezone(timezone.utc), close_local.astimezone(timezone.utc))


def _day_bar(day: date, high: float, low: float) -> Bar:
    # One 30m bar at the open stands in for the whole session; the session
    # candle builder only needs the aggregate high/low.
    start = datetime.combine(day, datetime.min.time(), tzinfo=ET).replace(hour=9, minute=30)
    return Bar(start=start, open=low, high=high, low=low, close=high, volume=1.0)


def _symbol(symbol: str, *, bullish: bool | None, hourly: str | None = None) -> SymbolContext:
    if bullish is None:
        return SymbolContext(symbol=symbol, available=True, close=100.0, vwap=100.0, ema20=100.0)
    close, vwap, ema = (101.0, 100.0, 99.0) if bullish else (99.0, 100.0, 101.0)
    return SymbolContext(
        symbol=symbol, available=True, close=close, vwap=vwap, ema20=ema, hourly_candle_type=hourly
    )


# Prior sessions (oldest first). Only the last three set the 2-2-2 pattern;
# the earlier ones supply resistance levels above the trigger.
_PRIOR = [
    (date(2026, 9, 3), 118.0, 108.0),
    (date(2026, 9, 4), 120.0, 110.0),
    (date(2026, 9, 8), 106.5, 100.0),
    (date(2026, 9, 9), 100.0, 95.0),
    (date(2026, 9, 10), 102.0, 97.0),   # 2U vs 09-09
    (date(2026, 9, 11), 104.0, 99.0),   # 2U vs 09-10 -> trigger 104, invalidation 99
]
_TODAY = date(2026, 9, 14)


def _run_daily(tmp_path: Path, *, hourly: str | None, **cfg_overrides):
    config = _cfg(tmp_path, **cfg_overrides)
    storage = ScanStorage(config.sqlite_path)
    scanner = OptionsScanner(config, object(), storage, DiscordAlerter(config, storage))

    bars = [_day_bar(day, high, low) for day, high, low in _PRIOR]
    # First 30m bar of today breaks yesterday's high: 2U on the open bar.
    bars.append(_day_bar(_TODAY, 106.0, 103.0))
    required = [_session(day) for day, _, _ in _PRIOR] + [_session(_TODAY)]
    session = required[-1]
    # 10:16 ET: the 09:30 bar has closed (10:00) and cleared the 16-min delay,
    # but no session-hourly candle can exist yet.
    cutoff = session.open + timedelta(minutes=46)

    return scanner._daily_candidate_from_raw(
        _Builder(),
        bars,
        session,
        required,
        cutoff,
        _symbol("AAPL", bullish=True, hourly=hourly),
        _symbol("SPY", bullish=True),
        _symbol("QQQ", bullish=True),
    )


def test_daily_setup_triggers_on_the_first_bar_that_breaks_the_trigger(tmp_path):
    candidate = _run_daily(tmp_path, hourly=None)
    assert candidate is not None
    assert candidate["setup_type"] == "DAILY_222_CONTINUATION"
    assert candidate["setup_entry_trigger"] == 104.0
    assert candidate["underlying_invalidation"] == 99.0
    assert candidate["setup_status"] == "TRIGGERED"
    assert candidate["setup_market_status"] == "VALID"
    assert candidate["setup_market_reason"] == "spy=bullish;qqq=bullish;hourly=missing"
    # The Daily lane pools prior highs AND lows as candidate levels; above
    # the 104 trigger they are 106.5 (0.5R), 108 (0.8R), 110 (1.2R), 118, 120.
    # Floor 1.0R with risk 5 skips the first two.
    assert (candidate["target_1"], candidate["target_2"]) == (110.0, 118.0)
    assert candidate["setup_rr_1"] == pytest.approx(1.2)


def test_spy_qqq_alignment_is_still_required(tmp_path):
    config = _cfg(tmp_path)
    storage = ScanStorage(config.sqlite_path)
    scanner = OptionsScanner(config, object(), storage, DiscordAlerter(config, storage))
    bars = [_day_bar(day, high, low) for day, high, low in _PRIOR] + [_day_bar(_TODAY, 106.0, 103.0)]
    required = [_session(day) for day, _, _ in _PRIOR] + [_session(_TODAY)]
    session = required[-1]
    candidate = scanner._daily_candidate_from_raw(
        _Builder(), bars, session, required, session.open + timedelta(minutes=46),
        _symbol("AAPL", bullish=True), _symbol("SPY", bullish=True), _symbol("QQQ", bullish=False),
    )
    assert candidate["setup_status"] == "INVALID"
    assert candidate["setup_suppression_reason"] == "setup_proof_incomplete:market_not_aligned"
    assert candidate["setup_market_reason"] == "spy=bullish;qqq=bearish;hourly=missing"


def test_old_hourly_requirement_is_one_flag_away(tmp_path):
    late = _run_daily(tmp_path, hourly=None, paper_v1_daily_require_hourly_alignment=True)
    assert late["setup_status"] == "INVALID"
    assert late["setup_market_status"] == "NOT_ALIGNED"
    confirmed = _run_daily(tmp_path, hourly=TWO_UP, paper_v1_daily_require_hourly_alignment=True)
    assert confirmed["setup_status"] == "TRIGGERED"


def test_target_floor_off_restores_nearest_level_targets(tmp_path):
    candidate = _run_daily(tmp_path, hourly=None, paper_v1_daily_min_target_rr=0.0)
    assert candidate["setup_status"] == "TRIGGERED"
    assert (candidate["target_1"], candidate["target_2"]) == (106.5, 108.0)
    assert candidate["setup_rr_1"] == pytest.approx(0.5)


def test_no_level_beyond_the_floor_is_recorded_not_traded(tmp_path):
    # Raise the floor past every available level: the setup stays a
    # mechanically triggered Daily row but with incomplete targets, which
    # the evidence hardening turns into a counterfactual observer row.
    candidate = _run_daily(tmp_path, hourly=None, paper_v1_daily_min_target_rr=5.0)
    assert candidate["setup_status"] == "INVALID"
    assert candidate["setup_reason_code"] == "daily_targets_incomplete"
    assert candidate["setup_target_reason"] == "no_target_1_at_min_rr"
    assert candidate["target_1"] is None
