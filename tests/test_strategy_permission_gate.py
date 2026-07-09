"""Strategy permission gate — config/settings.py (strategy_permission_gate_enabled,
strategy_permission_default_status, strategy_status) + the enforcement point in
strategy/signal_engine.py's DecisionEngine.evaluate(), right before a setup's
final TRADE return.

Disabled by default (dataclass level) so every existing test/fixture that
builds SystemConfig directly is unaffected. Production behavior comes from
risk_rules.yaml's strategy_permission_gate block.
"""
from __future__ import annotations

import dataclasses
from copy import deepcopy

import pytest

from config.settings import ConfigError, SystemConfig, _validate_config
from risk.risk_engine import DailyState
from strategy.signal_engine import DecisionEngine


def _gated_engine(config, *, status_map=None, default_status="SHADOW_ONLY"):
    cfg = dataclasses.replace(
        config,
        strategy_permission_gate_enabled=True,
        strategy_permission_default_status=default_status,
        strategy_status=dict(status_map or {}),
    )
    return DecisionEngine(config=cfg)


def test_gate_disabled_by_default_at_dataclass_level(config):
    assert config.strategy_permission_gate_enabled is False
    assert config.strategy_status == {}


def test_disabled_gate_is_a_no_op(config, fresh_market_state):
    """When the gate is off, orb_reclaim fires exactly as before this feature."""
    engine = DecisionEngine(config=config)
    decision = engine.evaluate(deepcopy(fresh_market_state), DailyState())
    assert decision.decision == "TRADE"
    assert decision.setup.strategy == "orb_reclaim"


def test_paper_eligible_strategy_proceeds(config, fresh_market_state):
    engine = _gated_engine(config, status_map={"orb_reclaim": "PAPER_ELIGIBLE"})
    decision = engine.evaluate(deepcopy(fresh_market_state), DailyState())
    assert decision.decision == "TRADE"
    assert decision.setup.strategy == "orb_reclaim"


def test_shadow_only_strategy_is_blocked(config, fresh_market_state):
    engine = _gated_engine(config, status_map={"orb_reclaim": "SHADOW_ONLY"})
    decision = engine.evaluate(deepcopy(fresh_market_state), DailyState())
    assert decision.decision == "NO_TRADE"
    assert "STRATEGY_NOT_PAPER_ELIGIBLE" in decision.failed_gates
    assert decision.setup is not None and decision.setup.strategy == "orb_reclaim"


def test_research_only_strategy_is_blocked(config, fresh_market_state):
    engine = _gated_engine(config, status_map={"orb_reclaim": "RESEARCH_ONLY"})
    decision = engine.evaluate(deepcopy(fresh_market_state), DailyState())
    assert decision.decision == "NO_TRADE"
    assert "STRATEGY_NOT_PAPER_ELIGIBLE" in decision.failed_gates


def test_disabled_strategy_is_blocked(config, fresh_market_state):
    engine = _gated_engine(config, status_map={"orb_reclaim": "DISABLED"})
    decision = engine.evaluate(deepcopy(fresh_market_state), DailyState())
    assert decision.decision == "NO_TRADE"
    assert "STRATEGY_NOT_PAPER_ELIGIBLE" in decision.failed_gates


def test_unknown_strategy_defaults_to_shadow_only(config, fresh_market_state):
    """A strategy with no entry in strategy_status falls back to
    strategy_permission_default_status, not an implicit allow."""
    engine = _gated_engine(config, status_map={})
    decision = engine.evaluate(deepcopy(fresh_market_state), DailyState())
    assert decision.decision == "NO_TRADE"
    assert "STRATEGY_NOT_PAPER_ELIGIBLE" in decision.failed_gates


def test_default_status_can_be_configured_open(config, fresh_market_state):
    """default_status is itself configurable (not hardcoded) — proves the gate
    reads it rather than assuming SHADOW_ONLY everywhere."""
    engine = _gated_engine(config, status_map={}, default_status="PAPER_ELIGIBLE")
    decision = engine.evaluate(deepcopy(fresh_market_state), DailyState())
    assert decision.decision == "TRADE"


def test_blocked_trade_preserves_candidate_details_for_research(config, fresh_market_state):
    engine = _gated_engine(config, status_map={"orb_reclaim": "SHADOW_ONLY"})
    decision = engine.evaluate(deepcopy(fresh_market_state), DailyState())
    assert decision.decision == "NO_TRADE"
    assert decision.setup is not None
    assert decision.setup.entry > 0 and decision.setup.stop > 0 and decision.setup.target > 0
    audit = decision.candidate_audit
    winner_rows = [row for row in audit if row.get("winner")]
    assert winner_rows, "winning candidate row must still be present in candidate_audit"
    row = winner_rows[0]
    assert row["strategy"] == "orb_reclaim"
    assert row["strategy_permission_status"] == "SHADOW_ONLY"
    assert row["strategy_permission_blocked"] is True
    assert row["reject_code"] == "STRATEGY_NOT_PAPER_ELIGIBLE"


def test_blocked_trade_reason_names_strategy_and_status(config, fresh_market_state):
    engine = _gated_engine(config, status_map={"orb_reclaim": "RESEARCH_ONLY"})
    decision = engine.evaluate(deepcopy(fresh_market_state), DailyState())
    assert "orb_reclaim" in decision.reason
    assert "RESEARCH_ONLY" in decision.reason


def test_gate_does_not_send_blocked_strategy_to_broker_path(config, fresh_market_state):
    """decision.decision != 'TRADE' is what webhook/runner.py gates broker
    execution on — confirm the blocked case never reports TRADE."""
    engine = _gated_engine(config, status_map={"orb_reclaim": "DISABLED"})
    decision = engine.evaluate(deepcopy(fresh_market_state), DailyState())
    assert decision.decision != "TRADE"


# ── config/settings.py: parsing + validation ────────────────────────────────

def test_validate_config_rejects_invalid_default_status(config):
    bad = dataclasses.replace(
        config, max_staleness_seconds=300, strategy_permission_default_status="MAYBE"
    )
    with pytest.raises(ConfigError, match="strategy_permission_gate.default_status"):
        _validate_config(bad)


def test_validate_config_rejects_invalid_strategy_status_value(config):
    bad = dataclasses.replace(
        config, max_staleness_seconds=300, strategy_status={"orb_reclaim": "PROBABLY"}
    )
    with pytest.raises(ConfigError, match="strategy_permission_gate.strategy_status"):
        _validate_config(bad)


def test_shipped_risk_rules_enables_gate_and_demotes_vwap_hold():
    import yaml
    from pathlib import Path
    rules = yaml.safe_load(Path("risk_rules.yaml").read_text())
    gate = rules["strategy_permission_gate"]
    assert gate["enabled"] is True
    assert gate["default_status"] == "SHADOW_ONLY"
    assert gate["strategy_status"]["vwap_hold"] == "SHADOW_ONLY"
    assert gate["strategy_status"]["orb_reclaim"] == "PAPER_ELIGIBLE"


def test_shipped_risk_rules_loads_via_load_config():
    """The shipped yaml must actually parse through load_config() without error —
    catches a valid-looking-but-unparseable config before it ever reaches a box."""
    from config.settings import load_config
    cfg = load_config("risk_rules.yaml")
    assert cfg.strategy_permission_gate_enabled is True
    assert cfg.strategy_status["vwap_hold"] == "SHADOW_ONLY"
