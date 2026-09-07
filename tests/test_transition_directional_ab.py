from __future__ import annotations

from scripts.transition_directional_ab import mirror_candidate, run_variant, summarize


def _row() -> dict:
    return {
        "ts": "2026-01-05T00:00:00+00:00",
        "instrument": "MNQ",
        "session_bucket": "london",
        "candidate": {"strategy": "transition_failed_breakdown_reclaim", "direction": "LONG", "entry": 100.0, "stop": 90.0, "target": 110.0, "rr_ratio": 1.0},
    }


def _bars():
    return [
        {"timestamp": "2026-01-05T00:00:00+00:00", "open": 100, "high": 100, "low": 100, "close": 100},
        {"timestamp": "2026-01-05T00:05:00+00:00", "open": 100, "high": 111, "low": 89, "close": 100},
    ]


def test_mirror_preserves_entry_and_reflects_bracket():
    mirrored = mirror_candidate(_row()["candidate"])
    assert mirrored["direction"] == "SHORT"
    assert mirrored["entry"] == 100.0
    assert mirrored["stop"] == 110.0
    assert mirrored["target"] == 90.0


def test_original_and_mirror_use_same_ioc_and_pessimistic_bar():
    original = run_variant(_row(), _bars(), "original")
    mirrored = run_variant(_row(), _bars(), "mirrored")
    assert original["result"] == "LOSS"
    assert mirrored["result"] == "LOSS"
    assert original["net_pnl"] < 0
    assert mirrored["net_pnl"] < 0


def test_control_has_no_fills_or_costs():
    control = run_variant(_row(), _bars(), "control")
    assert control == {"status": "NO_TRADE", "result": "CONTROL", "gross_pnl": 0.0, "slippage": 0.0, "commission": 0.0, "net_pnl": 0.0}


def test_summary_counts_only_resolved_pnl():
    summary = summarize([
        {"status": "FILLED", "result": "WIN", "gross_pnl": 5.0, "slippage": 0.0, "commission": 0.0, "net_pnl": 5.0},
        {"status": "NO_FILL", "result": "NO_FILL", "gross_pnl": 0.0, "slippage": 0.0, "commission": 0.0, "net_pnl": 0.0},
        {"status": "OPEN", "result": "OPEN", "gross_pnl": 0.0, "slippage": 0.0, "commission": 0.0, "net_pnl": 0.0},
    ])
    assert summary["attempts"] == 3
    assert summary["fills"] == 1
    assert summary["gross_pnl"] == 5.0
    assert summary["costs"] == 0.0
    assert summary["net_pnl"] == 5.0
    assert summary["expectancy_per_fill"] == 5.0
