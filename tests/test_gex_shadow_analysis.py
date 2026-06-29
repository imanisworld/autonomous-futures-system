from __future__ import annotations

from sources.gex_shadow_analysis import disabled_summary, summarize_gex_shadow


def _trade(
    ts: str,
    *,
    regime: str,
    close: float,
    direction: str = "LONG",
    gex_extra: dict | None = None,
) -> dict:
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
            **(gex_extra or {}),
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


def test_new_gex_fields_get_compact_cohorts_and_enrichment_verdict():
    entries = []
    bullish = {
        "spot": 19580,
        "delta_bias": "bullish",
        "spot_vs_flip": "above",
        "dist_to_flip": -80,
        "call_walls": [19600, 19585, 19650],
        "put_walls": [19400, 19450],
    }
    bearish = {
        "spot": 19420,
        "delta_bias": "bearish",
        "spot_vs_flip": "below",
        "dist_to_flip": 80,
        "call_walls": [19600, 19550],
        "put_walls": [19400, 19418, 19350],
    }
    for i in range(2):
        entries += [
            _trade(
                f"2026-06-23T14:{30 + 2*i:02d}:00+00:00",
                regime="positive",
                close=19584,
                gex_extra=bullish,
            ),
            _outcome("WIN", 40),
            _trade(
                f"2026-06-23T14:{31 + 2*i:02d}:00+00:00",
                regime="negative",
                close=19418,
                gex_extra=bearish,
            ),
            _outcome("LOSS", -30),
        ]

    summary = summarize_gex_shadow(entries, min_sample=2)

    assert {row["key"] for row in summary["cohorts"]["by_delta_bias"]} == {
        "bullish", "bearish"
    }
    assert {row["key"] for row in summary["cohorts"]["by_spot_vs_flip"]} == {
        "above", "below"
    }
    assert summary["cohorts"]["by_flip_distance"][0]["key"] == "mid_0.25_1pct"
    wall_keys = {row["key"] for row in summary["cohorts"]["by_wall_rank_context"]}
    assert wall_keys == {"near_call_secondary", "near_put_secondary"}
    evidence = summary["enrichment_evidence"]
    assert evidence["status"] == "ENRICHMENT_PROMISING"
    assert "delta_bias" in evidence["earned_dimensions"]
    assert "delta_alignment" in evidence["earned_dimensions"]
    assert "delta_bias" in evidence["stable_earned_dimensions"]
    delta = next(row for row in evidence["dimensions"] if row["dimension"] == "delta_bias")
    assert delta["coverage_pct"] == 100.0
    assert delta["expectancy_spread"] == 70.0
    assert delta["stable_across_time"] is True


def test_new_field_cohorts_fail_soft_on_malformed_or_missing_values():
    trade = _trade(
        "2026-06-23T14:30:00+00:00",
        regime="positive",
        close=19580,
        gex_extra={
            "delta_bias": {"unexpected": True},
            "spot_vs_flip": "sideways",
            "dist_to_flip": "not-a-number",
            "call_walls": "not-a-list",
            "put_walls": [None, "bad"],
        },
    )
    trade["gex_observed"]["flip_point"] = None
    trade["gex_observed"]["call_wall"] = None
    trade["gex_observed"]["put_wall"] = None

    summary = summarize_gex_shadow([trade, _outcome("WIN", 10)], min_sample=2)

    for name in (
        "by_delta_bias", "by_delta_alignment", "by_spot_vs_flip",
        "by_flip_distance", "by_wall_rank_context"
    ):
        assert summary["cohorts"][name][0]["key"] == "unknown"
    assert summary["enrichment_evidence"]["status"] == "JOURNAL_ONLY"
