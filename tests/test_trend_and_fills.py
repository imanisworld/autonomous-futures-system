"""
tests/test_trend_and_fills.py

Locks in the two fixes for the "live fires zero trades" root cause:
  1. Single-source-of-truth trend classification (scale-free EMA stack), shared
     by live (state_builder) and replay (csv_to_replay).
  2. Realistic paper fills — adverse slippage on market fills, and worst-case
     (stop) resolution when a bar straddles both stop and target.
"""

from __future__ import annotations

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
