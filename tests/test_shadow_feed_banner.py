"""
tests/test_shadow_feed_banner.py

Dashboard status fix (2026-07-13): an intentional 5-minute alert for the
vwap_hold early-signal shadow/observation lane must never render as the
loud red "LIVE ALERT MISCONFIGURED" banner. See webhook/app.py's
`_shadow_feed_status()` (new, additive, informational-only) and
`_timeframe_mismatch_state()` (existing, UNCHANGED by this fix).

Key fact this fix relies on, verified here rather than assumed: with
FIVE_MIN_FEED_ENABLED=true, webhook/runner.py's "Step 0a" early-return
means a 5-minute alert NEVER reaches the CONFIG_BLOCKED/TIMEFRAME_MISMATCH
journal write at all — so there is nothing to "reclassify" there. This
file proves that directly (not just cites the code), then proves the new
`_shadow_feed_status()` function supplies the missing positive signal, and
proves every other classification (feed disabled, unsupported timeframe,
main 15m alerts) is exactly unchanged.

This is a display-only fix: no execution/broker/risk/strategy/order
behavior changes anywhere in this file's assertions.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from tests.test_e2e_scenarios import _base_config, _base_payload
from webhook.runner import process_alert


def _isolate(monkeypatch, app_module, tmp_path):
    monkeypatch.setattr(app_module._config, "log_dir", str(tmp_path / "logs"))


def _set_feed(monkeypatch, *, feed_enabled: bool, mode: str, app_module=None):
    monkeypatch.setenv("FIVE_MIN_FEED_ENABLED", "true" if feed_enabled else "false")
    if app_module is not None:
        monkeypatch.setattr(app_module._config, "vwap_hold_early_mode", mode)


def _record_bar(log_dir, ticker="MNQ1!", ts=None, close=19500.0):
    from webhook.payload import AlertPayload
    from context.five_min_feed import record_five_min

    payload = AlertPayload(
        ticker=ticker,
        timestamp=(ts or datetime.now(timezone.utc)).isoformat(),
        open=close - 1, high=close + 1, low=close - 2, close=close,
        timeframe="5m",
    )
    record_five_min(payload, log_dir)


# ─── _shadow_feed_status(): pure informational signal ─────────────────────────

def test_none_when_feed_disabled(monkeypatch, tmp_path):
    import webhook.app as app_module

    _isolate(monkeypatch, app_module, tmp_path)
    _set_feed(monkeypatch, feed_enabled=False, mode="shadow", app_module=app_module)
    _record_bar(app_module._config.log_dir)

    assert app_module._shadow_feed_status() is None


def test_none_when_mode_off(monkeypatch, tmp_path):
    import webhook.app as app_module

    _isolate(monkeypatch, app_module, tmp_path)
    _set_feed(monkeypatch, feed_enabled=True, mode="off", app_module=app_module)
    _record_bar(app_module._config.log_dir)

    assert app_module._shadow_feed_status() is None


def test_shadow_label_with_recent_bar(monkeypatch, tmp_path):
    import webhook.app as app_module

    _isolate(monkeypatch, app_module, tmp_path)
    _set_feed(monkeypatch, feed_enabled=True, mode="shadow", app_module=app_module)
    now = datetime.now(timezone.utc)
    _record_bar(app_module._config.log_dir, ts=now)

    status = app_module._shadow_feed_status(now=now + timedelta(minutes=1))
    assert status is not None
    assert status["label"] == "SHADOW FEED ACTIVE"
    assert "early-signal evaluation" in status["detail"]
    assert "Main execution remains 15-minute only" in status["detail"]
    assert "SHADOW FEED: 5M" in status["footer"]
    assert "MAIN ORDERS: PAPER SIM ENABLED" in status["footer"]
    assert "SHADOW ORDERS: DISABLED" in status["footer"]


def test_observe_only_label_with_recent_bar(monkeypatch, tmp_path):
    import webhook.app as app_module

    _isolate(monkeypatch, app_module, tmp_path)
    _set_feed(monkeypatch, feed_enabled=True, mode="observe_only", app_module=app_module)
    now = datetime.now(timezone.utc)
    _record_bar(app_module._config.log_dir, ts=now)

    status = app_module._shadow_feed_status(now=now + timedelta(minutes=1))
    assert status is not None
    assert status["label"] == "OBSERVATION FEED ACTIVE"
    assert "observation only" in status["detail"]


def test_none_when_bar_is_stale(monkeypatch, tmp_path):
    import webhook.app as app_module

    _isolate(monkeypatch, app_module, tmp_path)
    _set_feed(monkeypatch, feed_enabled=True, mode="shadow", app_module=app_module)
    now = datetime.now(timezone.utc)
    _record_bar(app_module._config.log_dir, ts=now)

    # Well past the 5-minute feed's staleness window (2 bars + grace = 11 min).
    status = app_module._shadow_feed_status(now=now + timedelta(minutes=30))
    assert status is None


def test_footer_reflects_demo_execution(monkeypatch, tmp_path):
    import webhook.app as app_module

    _isolate(monkeypatch, app_module, tmp_path)
    _set_feed(monkeypatch, feed_enabled=True, mode="shadow", app_module=app_module)
    monkeypatch.setenv("BROKER", "tradovate")
    monkeypatch.setenv("TRADOVATE_ENV", "demo")
    now = datetime.now(timezone.utc)
    _record_bar(app_module._config.log_dir, ts=now)

    status = app_module._shadow_feed_status(now=now + timedelta(minutes=1))
    assert status is not None
    assert "EXECUTION: DEMO" in status["footer"]
    assert "MAIN ORDERS: DEMO ENABLED" in status["footer"]
    assert "SHADOW ORDERS: DISABLED" in status["footer"]


def test_shadow_feed_status_never_implies_live_trading(monkeypatch, tmp_path):
    """The new banner's own dict must carry no order/execution-approval keys,
    and calling it must never itself toggle live-trading state."""
    import webhook.app as app_module

    _isolate(monkeypatch, app_module, tmp_path)
    _set_feed(monkeypatch, feed_enabled=True, mode="shadow", app_module=app_module)
    monkeypatch.setattr(app_module._config, "live_trading_enabled", False)
    now = datetime.now(timezone.utc)
    _record_bar(app_module._config.log_dir, ts=now)

    status = app_module._shadow_feed_status(now=now + timedelta(minutes=1))
    assert status is not None
    assert set(status.keys()) == {"label", "detail", "footer", "mode", "last_ts"}
    assert app_module._config.live_trading_enabled is False


# ─── _timeframe_mismatch_state(): existing logic, proven UNCHANGED ────────────

def _mismatch_row(ts, received_tf="5m"):
    return {
        "ts": ts, "decision": "CONFIG_BLOCKED", "config_block": "TIMEFRAME_MISMATCH",
        "received_timeframe": received_tf, "type": "DECISION",
    }


def _good_row(ts):
    return {"ts": ts, "decision": "NO_TRADE", "type": "DECISION"}


def test_5m_mismatch_still_blocks_when_feed_disabled_shape(monkeypatch):
    """Models the FIVE_MIN_FEED_ENABLED=false scenario's journal shape — the
    runner still writes this exact row in that case (unchanged code path)."""
    import webhook.app as app_module

    entries = [_mismatch_row("2026-07-13T10:00:00+00:00", "5m")]
    state = app_module._timeframe_mismatch_state(entries)
    assert state is not None
    assert state["current"] is True


def test_unsupported_timeframe_still_blocks(monkeypatch):
    import webhook.app as app_module

    entries = [_mismatch_row("2026-07-13T10:00:00+00:00", "60")]
    state = app_module._timeframe_mismatch_state(entries)
    assert state is not None
    assert state["current"] is True


def test_main_15m_decision_clears_prior_mismatch(monkeypatch):
    """A subsequent on-timeframe (15m) decision resolves the banner — this is
    the existing self-clearing mechanism, untouched by this fix."""
    import webhook.app as app_module

    entries = [
        _mismatch_row("2026-07-13T10:00:00+00:00", "5m"),
        _good_row("2026-07-13T10:15:00+00:00"),
    ]
    state = app_module._timeframe_mismatch_state(entries)
    assert state is not None
    assert state["current"] is False


# ─── End-to-end via process_alert(): main execution lane + journal shape ──────

def _five_min_payload(**overrides):
    return _base_payload(timeframe="5", **overrides)


def test_5m_alert_excluded_from_execution_lane_when_feed_enabled(monkeypatch, tmp_path):
    monkeypatch.setenv("FIVE_MIN_FEED_ENABLED", "true")
    cfg = replace(_base_config(tmp_path), vwap_hold_early_mode="shadow")

    result = process_alert(_five_min_payload(), config=cfg, log_dir=cfg.log_dir)

    assert result["decision"] == "FIVE_MIN_CONTEXT"


def test_5m_alert_writes_no_config_blocked_row_when_feed_enabled(monkeypatch, tmp_path):
    from journal.journal_logger import JournalLogger
    from datetime import date

    monkeypatch.setenv("FIVE_MIN_FEED_ENABLED", "true")
    cfg = replace(_base_config(tmp_path), vwap_hold_early_mode="shadow")

    process_alert(_five_min_payload(), config=cfg, log_dir=cfg.log_dir)

    journal = JournalLogger(log_dir=cfg.log_dir)
    path = journal._journal_path(date.today())
    entries = journal._read_entries(path) if path.exists() else []
    blocked = [
        e for e in entries
        if e.get("decision") == "CONFIG_BLOCKED" and e.get("config_block") == "TIMEFRAME_MISMATCH"
    ]
    assert blocked == []


def test_5m_alert_still_blocked_when_feed_disabled(monkeypatch, tmp_path):
    monkeypatch.setenv("FIVE_MIN_FEED_ENABLED", "false")
    cfg = _base_config(tmp_path)

    result = process_alert(_five_min_payload(), config=cfg, log_dir=cfg.log_dir)

    assert result["decision"] == "CONFIG_BLOCKED"
    assert result["config_block"] == "TIMEFRAME_MISMATCH"
    assert result["received_timeframe"] in ("5", "5m")


def test_main_15m_alert_behavior_is_unchanged(monkeypatch, tmp_path):
    """Sanity: a normal on-timeframe 15m alert is unaffected by any of this —
    same decision engine, same journal shape, feed flag irrelevant to it."""
    monkeypatch.setenv("FIVE_MIN_FEED_ENABLED", "true")
    cfg = replace(_base_config(tmp_path), vwap_hold_early_mode="shadow")

    result = process_alert(_base_payload(), config=cfg, log_dir=cfg.log_dir)

    assert result["decision"] in ("TRADE", "NO_TRADE", "RISK_REJECTED")
    assert result["decision"] != "FIVE_MIN_CONTEXT"
    assert result["decision"] != "CONFIG_BLOCKED"


def test_shadow_feed_status_reflects_a_real_process_alert_bar(monkeypatch, tmp_path):
    """Closest to true end-to-end: the real webhook path records the bar, the
    new status function picks it up from the SAME log_dir/config."""
    import webhook.app as app_module

    _isolate(monkeypatch, app_module, tmp_path)
    monkeypatch.setenv("FIVE_MIN_FEED_ENABLED", "true")
    monkeypatch.setattr(app_module._config, "vwap_hold_early_mode", "shadow")
    cfg = replace(_base_config(tmp_path), vwap_hold_early_mode="shadow")
    # Use the SAME log_dir the app module reads from.
    cfg = replace(cfg, log_dir=app_module._config.log_dir)

    now = datetime.now(timezone.utc)
    process_alert(_five_min_payload(timestamp=now.isoformat()), config=cfg, log_dir=cfg.log_dir)

    status = app_module._shadow_feed_status(now=now + timedelta(minutes=1))
    assert status is not None
    assert status["label"] == "SHADOW FEED ACTIVE"
