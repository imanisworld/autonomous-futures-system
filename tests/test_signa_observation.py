from __future__ import annotations

import ast
from pathlib import Path

from sources.signa_observation import parse_action_card


def test_parse_documented_current_action_card_shape() -> None:
    payload = {
        "success": True,
        "request_id": "req-1",
        "server_time": "2026-08-05T19:00:00Z",
        "data_as_of": "2026-08-05T18:59:58Z",
        "data": {
            "signal": {
                "symbol": "aapl",
                "timeframe": "1d",
                "direction": "long",
                "score": 91,
                "grade": "A+",
                "confidence": 87,
                "entry_zone": {"low": 178.23, "high": 179.0},
                "stop_loss": 174.8,
                "targets": [185.9, 190.0],
                "reward_to_risk": 2.2,
                "component_scores": {
                    "trend": 90,
                    "momentum": 82,
                    "volume": None,
                },
                "model_version": "signa-consensus-v2.1",
            }
        },
    }

    obs = parse_action_card(payload, retrieved_at="2026-08-05T19:00:01Z")

    assert obs.ok is True
    assert obs.symbol == "AAPL"
    assert obs.timeframe == "1d"
    assert obs.direction == "LONG"
    assert obs.score == 91.0
    assert obs.grade == "A+"
    assert obs.confidence == 87.0
    assert obs.strength == 82.0
    assert obs.factor_count == 2
    assert obs.factor_conflicts == ()
    assert obs.observation_rating == "A"
    assert obs.observation_rating_basis == "complete_core;factor_coverage;no_conflicts"
    assert obs.entry_low == 178.23
    assert obs.entry_high == 179.0
    assert obs.stop_loss == 174.8
    assert obs.targets == (185.9, 190.0)
    assert obs.reward_to_risk == 2.2
    assert obs.component_scores == {"momentum": 82.0, "trend": 90.0, "volume": None}
    assert obs.model_version == "signa-consensus-v2.1"
    assert obs.data_as_of == "2026-08-05T18:59:58Z"


def test_missing_signal_fails_closed_without_legacy_fallback() -> None:
    obs = parse_action_card({"success": True, "data": {}})
    assert obs.ok is False
    assert obs.error == "signal_missing"
    assert obs.direction is None
    assert obs.grade is None
    assert obs.observation_rating == "N/A"
    assert obs.targets == ()


def test_malformed_optional_values_are_not_invented() -> None:
    payload = {
        "success": True,
        "data": {
            "signal": {
                "symbol": "QQQ",
                "direction": "WAIT",
                "confidence": "not-a-number",
                "entry_zone": {"low": "bad"},
                "targets": ["bad", 500.5],
                "component_scores": {"trend": "bad"},
            }
        },
    }
    obs = parse_action_card(payload)
    assert obs.ok is True
    assert obs.direction == "WAIT"
    assert obs.confidence is None
    assert obs.entry_low is None
    assert obs.entry_high is None
    assert obs.targets == (500.5,)
    assert obs.component_scores == {"trend": None}
    assert obs.observation_rating == "C"
    assert "factor_coverage" in str(obs.observation_rating_basis)


def test_telemetry_fields_are_namespaced_and_non_authoritative() -> None:
    obs = parse_action_card(
        {
            "success": True,
            "data": {"signal": {"symbol": "AAPL", "direction": "LONG", "confidence": 80}},
        },
        retrieved_at="2026-09-12T14:00:00Z",
    )
    fields = obs.telemetry_fields()
    assert fields["signa_v2_direction"] == "LONG"
    assert fields["signa_v2_confidence"] == 80.0
    assert fields["signa_v2_factor_count"] == 0
    assert fields["signa_v2_factor_conflicts"] == []
    assert fields["signa_v2_observation_rating"] == "C"
    assert fields["signa_v2_observation_rating_basis"].startswith("partial:")
    assert "signa_grade" not in fields
    assert "signa_score" not in fields
    assert "signa_daily_direction" not in fields
    assert "decision" not in fields
    assert "actionable" not in fields


def test_observation_module_is_pure_and_has_no_execution_or_runtime_imports() -> None:
    source = Path("sources/signa_observation.py").read_text()
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    forbidden = {
        "execution",
        "webhook",
        "risk",
        "strategy",
        "options_manager",
        "options_companion",
        "httpx",
        "requests",
    }
    assert not any(name.split(".")[0] in forbidden for name in imported)
    assert "LIVE_TRADING_ENABLED" not in source
    assert "SIGNA_GATE_ENFORCED" not in source
    assert ".post(" not in source
    assert ".put(" not in source
    assert ".delete(" not in source
    assert "open(" not in source



def test_video_defined_strength_and_factor_conflicts_are_observational() -> None:
    obs = parse_action_card(
        {
            "success": True,
            "data": {
                "signal": {
                    "symbol": "MRK",
                    "direction": "BULLISH",
                    "score": 72,
                    "confidence": 71,
                    "component_scores": {
                        "technicals": 65,
                        "options_flow": 66,
                        "sentiment": 60,
                        "momentum": 50,
                        "gamma": 48,
                    },
                }
            },
        }
    )

    assert obs.strength == 44.0
    assert obs.factor_count == 5
    assert obs.factor_conflicts == ("gamma",)
    assert obs.observation_rating == "B"
    assert obs.observation_rating_basis == "complete_core;factor_conflicts=1"
    fields = obs.telemetry_fields()
    assert fields["signa_v2_strength"] == 44.0
    assert fields["signa_v2_factor_count"] == 5
    assert fields["signa_v2_factor_conflicts"] == ["gamma"]
    assert fields["signa_v2_observation_rating"] == "B"
    assert "decision" not in fields
    assert "actionable" not in fields


def test_neutral_or_unknown_direction_cannot_rate_a() -> None:
    for direction in ("NEUTRAL", "SIDEWAYS"):
        obs = parse_action_card(
            {
                "success": True,
                "data": {
                    "signal": {
                        "symbol": "SPY",
                        "direction": direction,
                        "score": 72,
                        "confidence": 71,
                        "component_scores": {"technicals": 65, "gamma": 30},
                    }
                },
            }
        )
        assert obs.factor_conflicts == ()
        assert obs.observation_rating == "C"
        assert obs.observation_rating_basis == "partial:direction_unrecognized"
