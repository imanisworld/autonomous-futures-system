from __future__ import annotations

import ast
from pathlib import Path

from sources.signa_intelligence import (
    parse_action_card,
    parse_analysis,
    parse_earnings,
    summarize_dataset,
)


def test_parse_current_action_card_contract():
    payload = {
        "success": True,
        "server_time": "2026-08-05T19:00:00Z",
        "data_as_of": "2026-08-05T18:59:00Z",
        "data": {
            "signal": {
                "symbol": "AAPL",
                "timeframe": "1d",
                "direction": "LONG",
                "confidence": 87,
                "grade": "A",
                "score": 91,
                "entry_zone": {"low": 178.23, "high": 179.00},
                "stop_loss": 174.8,
                "targets": [185.9, 190.0],
                "reward_to_risk": 2.2,
                "component_scores": {"trend": 88, "momentum": None},
                "model_version": "signa-consensus-v2.1",
            }
        },
    }
    obs = parse_action_card(payload)
    assert obs.symbol == "AAPL"
    assert obs.direction == "LONG"
    assert obs.entry_low == 178.23
    assert obs.entry_high == 179.0
    assert obs.stop_loss == 174.8
    assert obs.targets == (185.9, 190.0)
    assert obs.reward_to_risk == 2.2
    assert obs.component_scores == {"trend": 88.0, "momentum": None}
    assert obs.data_as_of == "2026-08-05T18:59:00Z"


def test_action_card_missing_fields_are_never_fabricated():
    obs = parse_action_card({"data": {"signal": {"symbol": "QQQ"}}})
    assert obs.symbol == "QQQ"
    assert obs.direction is None
    assert obs.targets == ()
    assert obs.component_scores == {}
    assert obs.stop_loss is None


def test_parse_analysis_current_documented_shape():
    obs = parse_analysis(
        {
            "symbol": "NVDA",
            "analysis": {
                "summary": "Stage 2 uptrend",
                "patterns": ["Cup and Handle"],
                "sentiment": "Bullish",
                "catalysts": ["AI cycle"],
                "risks": ["market correction"],
                "technicals": {"rsi": 61.4, "stage": 2},
            },
        }
    )
    assert obs.symbol == "NVDA"
    assert obs.patterns == ("Cup and Handle",)
    assert obs.sentiment == "Bullish"
    assert obs.technicals["stage"] == 2


def test_parse_earnings_current_documented_shape():
    obs = parse_earnings(
        {
            "symbol": "AAPL",
            "period": "Q1 2026",
            "reportDate": "2026-01-30",
            "signal": "STRONG_BEAT",
            "signalScore": 2,
            "eps": {"surprisePct": 5.73},
            "revenue": {"surprisePct": 0.65},
            "bullishPoints": ["EPS beat"],
            "bearishPoints": [],
            "minutesSinceReport": 47,
            "fetchedAt": "2026-01-30T22:47:00Z",
            "cached": False,
        }
    )
    assert obs.signal == "STRONG_BEAT"
    assert obs.eps_surprise_pct == 5.73
    assert obs.revenue_surprise_pct == 0.65
    assert obs.bullish_points == ("EPS beat",)
    assert obs.cached is False


def test_variable_dataset_summary_is_conservative():
    obs = summarize_dataset(
        "options_flow",
        {"symbol": "TSLA", "results": [{"x": 1}, {"x": 2}], "direction": "BULLISH", "other": 7},
    )
    assert obs.dataset == "options_flow"
    assert obs.symbol == "TSLA"
    assert obs.count == 2
    assert obs.direction == "BULLISH"
    assert obs.top_level_fields == ("direction", "other", "results", "symbol")


def test_module_has_no_network_or_trading_authority():
    path = Path("sources/signa_intelligence.py")
    source = path.read_text()
    tree = ast.parse(source)
    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imports |= {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    assert not any(name.startswith(("httpx", "requests", "webhook", "execution", "risk", "strategy")) for name in imports)
    lowered = source.lower()
    for forbidden in ("live_trading_enabled", "place_order", "submit_order", "execute_trade", "tradovate", "alpaca"):
        assert forbidden not in lowered
