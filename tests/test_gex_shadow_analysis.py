from __future__ import annotations

from sources.gex_shadow_analysis import disabled_summary, summarize_gex_shadow


def _trade(ts: str, *, regime: str, close: float, direction: str = "LONG") -> dict:
    return {
        "ts": ts,
        "instrument": "MNQ",
        "session": "new_york",
        "decision": "TRADE",
        "risk_check": {"result": "APPROVED"},
        "setup": {
            "direction": direction,
            "entry": close,
            "stop": close - 20,
            "target": close + 40,
            "strategy": "orb_reclaim",
        },
        "context": {"close": close},
        "gex_observed": {
            "ok": True,
            "ticker": "NDX",
            "regime": regime,
            "flip_point": 19500,
            "call_wall": 19600,
            "put_wall": 19400,
        },
    }


def _outcome(result: str, pnl: float) -> dict:
    return {
        "type": "OUTCOME",
        "instrument": "MNQ",
        "outcome": {
            "result": result,
            "pnl_dollars": pnl,
            "entry_price": 19500,
            "exit_price": 19540 if pnl > 0 else 19480,
        },
    }


def test_disabled_summary_is_explicitly_observe_only():
    summary = disabled_summary()
    assert summary["enabled"] is False
    assert summary["trade_gating_changed"] is False
    assert "GEX_SHADOW_ANALYSIS_ENABLED" in summary["reason"]


def test_summarize_pairs_approved_trades_to_resolved_outcomes_fifo():
    entries = [
        _trade("2026-06-23T14:30:00+00:00", regime="positive", close=19580),
        {"decision": "NO_TRADE", "gex_observed": {"ok": True, "regime": "negative"}},
        _trade("2026-06-23T14:45:00+00:00", regime="negative", close=19420, direction="SHORT"),
        _outcome("WIN", 50),
        _outcome("LOSS", -25),
    ]

    summary = summarize_gex_shadow(entries, min_sample=2)

    assert summary["mode"] == "observe_only"
    assert summary["trade_gating_changed"] is False
    assert summary["resolved_trades"] == 2
    assert summary["measured_trades"] == 2
    assert summary["overall"] == {
        "sample_size": 2,
        "wins": 1,
        "losses": 1,
        "breakeven": 0,
        "win_rate": 50.0,
        "pnl_dollars": 25.0,
        "expectancy": 12.5,
        "sufficient_sample": True,
    }
    regimes = {row["key"]: row for row in summary["cohorts"]["by_regime"]}
    assert regimes["positive"]["wins"] == 1
    assert regimes["negative"]["losses"] == 1


def test_wall_context_marks_near_call_and_missing_gex_is_skipped():
    entries = [
        _trade("2026-06-23T14:30:00+00:00", regime="positive", close=19598),
        {
            **_trade("2026-06-23T14:45:00+00:00", regime="negative", close=19420),
            "gex_observed": {"ok": False, "error": "missing_api_key"},
        },
        _outcome("WIN", 40),
        _outcome("LOSS", -20),
    ]

    summary = summarize_gex_shadow(entries, min_sample=5)

    assert summary["resolved_trades"] == 2
    assert summary["measured_trades"] == 1
    assert summary["skipped_missing_gex"] == 1
    wall_rows = {row["key"]: row for row in summary["cohorts"]["by_wall_context"]}
    assert wall_rows["near_call_wall"]["sample_size"] == 1
    assert summary["verdict"]["status"] == "JOURNAL_ONLY"


def test_sufficient_cohort_separation_gets_promising_shadow_verdict():
    entries = []
    for i in range(3):
        entries.append(_trade(f"2026-06-23T14:{30+i:02d}:00+00:00", regime="positive", close=19580))
        entries.append(_outcome("WIN", 30))
    for i in range(3):
        entries.append(_trade(f"2026-06-23T15:{30+i:02d}:00+00:00", regime="negative", close=19420))
        entries.append(_outcome("LOSS", -20))

    summary = summarize_gex_shadow(entries, min_sample=3)

    assert summary["best_cohort"]["expectancy"] == 30.0
    assert summary["worst_cohort"]["expectancy"] == -20.0
    assert summary["verdict"]["status"] == "PROMISING_SHADOW_EDGE"
