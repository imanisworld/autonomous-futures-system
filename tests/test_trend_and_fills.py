"""
tests/test_trend_and_fills.py

Locks in the two fixes for the "live fires zero trades" root cause:
  1. Single-source-of-truth trend classification (scale-free EMA stack), shared
     by live (state_builder) and replay (csv_to_replay).
  2. Realistic paper fills — adverse slippage on market fills, and worst-case
     (stop) resolution when a bar straddles both stop and target.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

from context.trend import classify_trend, has_ema_inputs
from execution.broker_interface import BracketOrder
from execution.paper_broker import NextBarOHLC, PaperBroker


# ─── Trend: scale-free EMA stack ──────────────────────────────────────────────

def test_full_bull_stack_is_strong():
    assert classify_trend(100, 99, 98, 97) == ("UP", "STRONG")


def test_full_bear_stack_is_strong():
    assert classify_trend(90, 91, 92, 93) == ("DOWN", "STRONG")


def test_moderate_when_above_ema21_but_not_full_stack():
    # close>ema21 and ema9>ema21, but ema55 above ema21 → not a full stack
    assert classify_trend(100, 100.5, 99, 101) == ("UP", "MODERATE")


def test_scale_free_same_verdict_across_price_scales():
    # MES (~6000) and MNQ (~30000) with the same proportional stack → same label.
    mes = classify_trend(6000, 5995, 5990, 5985)
    mnq = classify_trend(30000, 29975, 29950, 29925)
    assert mes == mnq == ("UP", "STRONG")


def test_missing_ema_falls_back_to_neutral():
    assert classify_trend(100, None, 99, 98) == ("SIDEWAYS", "WEAK")
    assert has_ema_inputs(1, 2, 3) is True
    assert has_ema_inputs(1, None, 3) is False


# ─── Fills: slippage + both-hit worst case ────────────────────────────────────

def _open_long(broker: PaperBroker, entry=100.0, stop=99.0, target=103.0):
    return broker.execute_bracket(
        BracketOrder(
            instrument="MES",
            direction="LONG",
            entry=entry,
            stop=stop,
            target=target,
            rr_ratio=3.0,
            strategy="test",
            notes="",
            contracts=1,
        )
    )


def test_entry_slippage_is_adverse_for_long():
    # 1 tick = 0.25 for MES; LONG entry fills 1 tick higher.
    broker = PaperBroker(starting_balance=1000, slippage_ticks=1.0)
    fill = _open_long(broker, entry=100.0)
    assert fill.result == "OPEN"
    assert fill.entry_price == 100.25


def test_both_hit_resolves_as_stop_when_pessimistic():
    broker = PaperBroker(starting_balance=1000, slippage_ticks=0.0, pessimistic_both_hit=True)
    _open_long(broker, entry=100.0, stop=99.0, target=103.0)
    # Bar straddles BOTH stop (99) and target (103).
    fill = broker.resolve_position(NextBarOHLC(high=103.5, low=98.5))
    assert fill.result == "LOSS"
    assert fill.exit_reason == "STOP_HIT"


def test_both_hit_resolves_as_target_when_optimistic_legacy():
    broker = PaperBroker(starting_balance=1000, slippage_ticks=0.0, pessimistic_both_hit=False)
    _open_long(broker, entry=100.0, stop=99.0, target=103.0)
    fill = broker.resolve_position(NextBarOHLC(high=103.5, low=98.5))
    assert fill.result == "WIN"
    assert fill.exit_reason == "TARGET_HIT"


def test_stop_exit_is_slipped_past_the_stop():
    broker = PaperBroker(starting_balance=1000, slippage_ticks=1.0, pessimistic_both_hit=True)
    _open_long(broker, entry=100.0, stop=99.0, target=103.0)
    # Only the stop is hit; LONG stop fills 1 tick below 99.0 → 98.75.
    fill = broker.resolve_position(NextBarOHLC(high=101.0, low=98.9))
    assert fill.result == "LOSS"
    assert fill.exit_price == 98.75


def test_target_exit_fills_clean_no_slippage():
    broker = PaperBroker(starting_balance=1000, slippage_ticks=1.0, pessimistic_both_hit=True)
    _open_long(broker, entry=100.0, stop=99.0, target=103.0)
    fill = broker.resolve_position(NextBarOHLC(high=103.2, low=99.5))
    assert fill.result == "WIN"
    assert fill.exit_price == 103.0  # limit fill, no slippage


def test_defaults_preserve_legacy_optimistic_behavior():
    # No args → 0 slippage, target-priority (back-compat for existing callers).
    broker = PaperBroker(starting_balance=1000)
    fill_open = _open_long(broker, entry=100.0)
    assert fill_open.entry_price == 100.0
    fill = broker.resolve_position(NextBarOHLC(high=103.5, low=98.5))
    assert fill.result == "WIN"


def test_production_fill_defaults_are_honest(monkeypatch):
    """The PaperBroker constructor stays optimistic for back-compat, but the
    PRODUCTION config default (paper/replay) must be the honest fill model:
    pessimistic both-hit + >=1 tick slippage. This is the lock that stops a
    future backtest from silently inflating the win rate."""
    from config.settings import load_config

    monkeypatch.delenv("FILL_SLIPPAGE_TICKS", raising=False)
    monkeypatch.delenv("FILL_PESSIMISTIC_BOTH_HIT", raising=False)
    monkeypatch.setenv("SIGNA_SYMBOL_MAP", "MES:SPY,MNQ:QQQ")
    cfg = load_config()
    assert cfg.fill_pessimistic_both_hit is True
    assert cfg.fill_slippage_ticks >= 1.0


# ─── Timeframe guard ──────────────────────────────────────────────────────────

from config.settings import load_config
from webhook.payload import AlertPayload
from webhook.runner import (
    _check_timeframe,
    normalize_timeframe_minutes,
    process_alert,
)


def test_normalize_timeframe_minutes_forms():
    assert normalize_timeframe_minutes("5") == 5
    assert normalize_timeframe_minutes("15") == 15
    assert normalize_timeframe_minutes("15m") == 15
    assert normalize_timeframe_minutes("1h") == 60
    assert normalize_timeframe_minutes("D") == 1440
    assert normalize_timeframe_minutes("garbage") is None


def _tf_payload(tf: str) -> AlertPayload:
    return AlertPayload(
        ticker="MES1!",
        timestamp="2026-06-04T14:30:00+00:00",
        open=6000.0, high=6001.0, low=5999.0, close=6000.0,
        timeframe=tf,
    )


def test_check_timeframe_flags_5m_and_passes_15m():
    cfg = load_config()
    assert cfg.expected_timeframe_minutes == 15
    mismatch = _check_timeframe(_tf_payload("5"), cfg)
    assert mismatch is not None
    assert mismatch["expected"] == "15m" and mismatch["received"] == "5m"
    assert _check_timeframe(_tf_payload("15"), cfg) is None


def test_process_alert_blocks_off_timeframe_as_config_blocked(tmp_path):
    cfg = replace(load_config(), max_staleness_seconds=0)
    result = process_alert(_tf_payload("5"), config=cfg, log_dir=str(tmp_path))
    # Must NOT be evaluated as a normal NO_TRADE.
    assert result["decision"] == "CONFIG_BLOCKED"
    assert result["config_block"] == "TIMEFRAME_MISMATCH"
    assert result["received_timeframe"] == "5m"
    # Journaled under the distinct category, not NO_TRADE.
    import json
    lines = [json.loads(l) for l in (tmp_path / f"journal_{date.today().isoformat()}.jsonl").read_text().splitlines() if l.strip()]
    blocks = [e for e in lines if e.get("decision") == "CONFIG_BLOCKED"]
    assert blocks and blocks[-1]["config_block"] == "TIMEFRAME_MISMATCH"
    assert not [e for e in lines if e.get("decision") == "NO_TRADE"]


def test_required_instruments_present_in_config():
    """Isolated MNQ orb_breakout lane (risk_rules 1.2.0): MES is deliberately
    disabled, so MNQ alone is required. The load-bearing assertion — every
    required instrument must actually be allowed, catching a stale in-memory
    universe silently dropping one — is unchanged and still enforced."""
    cfg = load_config()
    assert set(["MNQ"]).issubset(set(cfg.required_instruments))
    missing = [s for s in cfg.required_instruments if s not in cfg.allowed_instruments]
    assert missing == []
