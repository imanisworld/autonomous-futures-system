"""
tests/test_news_release_window.py

`news_blackout_mode: "release_window"` blocks entries ONLY within a window
(total minutes, centered) around each date's release time:
  - FOMC dates tagged "YYYY-MM-DD 14:00" -> block 13:45-14:15 ET
  - CPI/NFP dates (untagged) -> default release time (08:30) -> block 08:15-08:45 ET
Outside the window, normal daily limits apply (no special 1-trade cap/cutoff).
"""

from __future__ import annotations

from datetime import datetime, timezone

from risk.risk_engine import DailyState, RiskEngine, TradeSetup

# June 2026: ET = UTC-4. 14:00 ET = 18:00 UTC; 08:30 ET = 12:30 UTC.
_FOMC_DATE = "2026-06-17 14:00"
_CPI_DATE = "2026-06-11"  # untagged -> default 08:30 ET


def _engine(config):
    config.news_blackout_mode = "release_window"
    config.news_blackout_dates = [_FOMC_DATE, _CPI_DATE]
    config.news_blackout_release_window_minutes = 30
    config.news_blackout_release_default_et = "08:30"
    return RiskEngine(config=config)


def _setup_at(utc: datetime) -> TradeSetup:
    return TradeSetup(
        direction="LONG", entry=21450.25, stop=21435.25, target=21480.25,
        rr_ratio=2.0, strategy="vwap_reclaim", instrument="MNQ",
        session="new_york", contracts=1, entry_time=utc,
    )


def test_fomc_entry_inside_window_is_blocked(config):
    eng = _engine(config)
    # 13:50 ET = 17:50 UTC -> inside 13:45-14:15
    res = eng.validate(_setup_at(datetime(2026, 6, 17, 17, 50, tzinfo=timezone.utc)), DailyState())
    assert res.rejected and res.failed_rule == "news_release_window"


def test_fomc_entry_outside_window_is_allowed(config):
    eng = _engine(config)
    # 10:30 ET = 14:30 UTC -> well outside the 13:45-14:15 FOMC window
    res = eng.validate(_setup_at(datetime(2026, 6, 17, 14, 30, tzinfo=timezone.utc)), DailyState())
    assert res.approved


def test_cpi_uses_default_830_window(config):
    eng = _engine(config)
    # 08:35 ET = 12:35 UTC -> inside 08:15-08:45 default window
    blocked = eng.validate(_setup_at(datetime(2026, 6, 11, 12, 35, tzinfo=timezone.utc)), DailyState())
    assert blocked.rejected and blocked.failed_rule == "news_release_window"
    # 14:00 ET = 18:00 UTC -> CPI window long past -> allowed
    allowed = eng.validate(_setup_at(datetime(2026, 6, 11, 18, 0, tzinfo=timezone.utc)), DailyState())
    assert allowed.approved


def test_release_window_does_not_impose_trade_cap(config):
    """Outside the window, a prior trade does NOT trigger a news cap — normal
    daily limits (max_trades_per_day) govern instead."""
    eng = _engine(config)
    daily = DailyState(trade_count=1)  # would have tripped the old reduced 1-trade cap
    res = eng.validate(_setup_at(datetime(2026, 6, 17, 14, 30, tzinfo=timezone.utc)), daily)
    assert res.approved  # 1 < max_trades_per_day(3); no news cap in release_window


def test_non_blackout_date_is_unrestricted(config):
    eng = _engine(config)
    # 2026-06-16 is not in the list -> news never blocks
    res = eng.validate(_setup_at(datetime(2026, 6, 16, 17, 50, tzinfo=timezone.utc)), DailyState())
    assert res.approved


def test_real_risk_rules_load_with_release_window():
    """The shipped risk_rules.yaml uses release_window — it must load cleanly."""
    from config.settings import load_config
    cfg = load_config()
    assert cfg.news_blackout_mode == "release_window"
    assert cfg.news_blackout_release_window_minutes == 30
    # FOMC date carries an explicit 14:00 tag; data dates stay untagged (08:30).
    assert any(d.startswith("2026-06-17") and "14:00" in d for d in cfg.news_blackout_dates)
