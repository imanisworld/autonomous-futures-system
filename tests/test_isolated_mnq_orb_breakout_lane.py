"""Focused proof of the isolated MNQ ORB Breakout inverse forward-paper lane.

Pins the shipped risk_rules.yaml (1.2.0) configuration that isolates the
already-merged inverse lane (PR #364), plus the two runtime invariants the
lane depends on: the inverse transform forces one contract + PaperBroker, and
the inverse/legacy-proof modes stay mutually exclusive.

These assertions are intentionally strict. If any of them fails, the lane is
no longer isolated and forward-paper evidence collected under it is not
attributable to orb_breakout alone.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from config.settings import ConfigError, load_config


def _shipped_rules() -> dict:
    return yaml.safe_load(Path("risk_rules.yaml").read_text())


# ─── Shipped configuration ────────────────────────────────────────────────────

def test_only_mnq_is_allowed():
    cfg = load_config("risk_rules.yaml")
    assert cfg.allowed_instruments == ["MNQ"]
    assert cfg.required_instruments == ["MNQ"]
    assert "MES" not in cfg.allowed_instruments


def test_only_orb_breakout_is_an_enabled_concept():
    cfg = load_config("risk_rules.yaml")
    assert cfg.enabled_concepts == ["orb_breakout"]


def test_only_orb_breakout_is_paper_eligible():
    cfg = load_config("risk_rules.yaml")
    assert cfg.strategy_permission_gate_enabled is True
    assert cfg.strategy_permission_default_status == "SHADOW_ONLY"
    assert cfg.strategy_status.get("orb_breakout") == "PAPER_ELIGIBLE"
    paper_eligible = [
        name for name, status in cfg.strategy_status.items()
        if status == "PAPER_ELIGIBLE"
    ]
    assert paper_eligible == ["orb_breakout"]


def test_max_trades_per_day_is_three():
    cfg = load_config("risk_rules.yaml")
    assert cfg.max_trades_per_day == 3


def test_vwap_hold_and_pdh_reclaim_demotions_preserved_as_governance_records():
    """Their SHADOW_ONLY status is evidence-based and independent of this lane;
    it must survive as an explicit record, not silently vanish into the
    default."""
    status = _shipped_rules()["strategy_permission_gate"]["strategy_status"]
    assert status["vwap_hold"] == "SHADOW_ONLY"
    assert status["pdh_reclaim"] == "SHADOW_ONLY"


def test_risk_rules_version_bumped():
    assert _shipped_rules()["version"] == "1.2.0"


# ─── The isolation guarantee ──────────────────────────────────────────────────

def test_higher_ranked_non_orb_candidate_cannot_suppress_the_lane():
    """The load-bearing isolation proof.

    DecisionEngine ranks candidates FIRST and applies the permission gate only
    to the winner, so a SHADOW_ONLY strategy that outranks orb_breakout would
    win selection, be blocked as STRATEGY_NOT_PAPER_ELIGIBLE, and produce
    NO_TRADE — silently suppressing the orb_breakout candidate rather than
    falling through to it (PR #373 confirmed exactly this: 7 of 33 MES 1-2-2
    candidates were preempted by a shadow-only vwap_hold).

    Demoting those strategies in the permission gate is therefore NOT
    sufficient on its own. This test pins the actual mechanism that prevents
    it: no non-ORB strategy is an enabled concept at all, so none can ever
    enter ranking and preempt the lane.
    """
    cfg = load_config("risk_rules.yaml")
    shadow_but_enabled = [
        name for name in cfg.enabled_concepts
        if cfg.strategy_status.get(name, cfg.strategy_permission_default_status)
        != "PAPER_ELIGIBLE"
    ]
    assert shadow_but_enabled == [], (
        "these concepts are enabled but not paper-eligible, so they can win "
        f"ranking and suppress orb_breakout: {shadow_but_enabled}"
    )
    # And specifically, the strategy that caused the PR #373 preemption:
    assert "vwap_hold" not in cfg.enabled_concepts


# ─── Inverse-lane runtime invariants ──────────────────────────────────────────

def test_paper_sim_forces_one_contract_and_internal_paper_broker():
    from context.mnq_orb_breakout_inverse_paper import CONTRACTS, evaluate, mirror_order
    from execution.broker_interface import BracketOrder

    class _Cfg:
        mnq_orb_breakout_inverse_mode = "paper_sim"

    decision = evaluate(_Cfg())
    assert decision.mode == "paper_sim"
    assert decision.apply_override is True
    assert decision.force_paper_broker is True
    assert decision.contracts == 1
    assert CONTRACTS == 1

    source = BracketOrder(
        instrument="MNQ",
        direction="LONG",
        entry=20000.0,
        stop=19980.0,
        target=20040.0,
        rr_ratio=2.0,
        contracts=7,          # deliberately not 1
        strategy="orb_breakout",
    )
    mirrored = mirror_order(source)
    assert mirrored.contracts == 1, "inverse transform must force exactly one contract"
    assert mirrored.direction == "SHORT", "inverse transform must flip direction"


def test_inverse_transform_rejects_non_mnq_orb_breakout_orders():
    from context.mnq_orb_breakout_inverse_paper import is_candidate, mirror_order
    from execution.broker_interface import BracketOrder

    assert is_candidate("MNQ", "orb_breakout") is True
    assert is_candidate("MES", "orb_breakout") is False
    assert is_candidate("MNQ", "orb_reclaim") is False

    foreign = BracketOrder(
        instrument="MES",
        direction="LONG",
        entry=5000.0,
        stop=4990.0,
        target=5020.0,
        rr_ratio=2.0,
        contracts=1,
        strategy="orb_breakout",
    )
    with pytest.raises(ValueError):
        mirror_order(foreign)


def test_inverse_and_legacy_proof_modes_are_mutually_exclusive():
    """Their execution semantics conflict (legacy = market entry + runner exit;
    inverse = mirrored static bracket + IOC), so config validation must reject
    both being active at once."""
    import dataclasses
    from config.settings import _validate_config

    cfg = load_config("risk_rules.yaml")

    both_active = dataclasses.replace(
        cfg,
        mnq_orb_breakout_inverse_mode="paper_sim",
        mnq_orb_breakout_proof_mode="paper_sim",
    )
    with pytest.raises(ConfigError, match="cannot both be active"):
        _validate_config(both_active)

    # The intended lane posture validates cleanly.
    lane = dataclasses.replace(
        cfg,
        mnq_orb_breakout_inverse_mode="paper_sim",
        mnq_orb_breakout_proof_mode="observe_only",
    )
    _validate_config(lane)


def test_inverse_mode_has_no_external_broker_value():
    """paper_only by construction — 'tradovate_demo'/'live' are not valid."""
    from context.mnq_orb_breakout_inverse_paper import VALID_MODES

    assert set(VALID_MODES) == {"observe_only", "paper_sim"}
    assert "tradovate_demo" not in VALID_MODES
    assert "live" not in VALID_MODES
