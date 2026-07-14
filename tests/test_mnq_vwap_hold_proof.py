"""
tests/test_mnq_vwap_hold_proof.py

Strategy-restoration candidate #3 (2026-07-14): MNQ vwap_hold proof mode.
Scoped narrowly to MNQ + vwap_hold + new_york session only — see
context/mnq_vwap_hold_proof.py and docs/strategy-matrix-tranche1-2026-07-14.md
for the evidence trail this is based on.

Mirrors tests/test_mnq_orb_breakout_proof.py's coverage, adapted to this
lane's two structural differences:
  1. vwap_hold is SHADOW_ONLY in the strategy permission gate, so paper_sim
     opens a narrow permission-gate exception in strategy/signal_engine.py
     (MNQ + vwap_hold + new_york + paper_sim ONLY).
  2. tradovate_demo deliberately does NOT open the gate — IOC/static on demo
     is the exact configuration proven negative, so it behaves like
     observe_only (documented deviation from the orb lanes' mode contract).
"""
from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from config.settings import ConfigError, _validate_config
from context.mnq_vwap_hold_proof import (
    VALID_MODES,
    campaign_already_attempted,
    evaluate_mnq_vwap_hold_proof,
    is_mnq_vwap_hold_candidate,
    mnq_vwap_hold_proof_mode,
    permission_gate_exception,
    record_campaign_attempt,
)
from execution.paper_broker import PaperBroker
from tests.test_e2e_scenarios import _base_config, _base_payload
from webhook.runner import process_alert


def _vwap_hold_payload(**overrides):
    """Fires _try_vwap_hold in strategy/signal_engine.py: close below VWAP
    (state_builder derives holding=True + price_vs_vwap='below'), downtrend,
    two_down Strat bar, ORB inert ('inside', boundaries clear of the close),
    PDH/PDL far from price so nothing earlier in the priority list fires.
    VWAP sits close enough to the close (19510 vs 19505.25) that the
    [target, stop] bracket still straddles the live close — the same
    bracket-straddle constraint production enforces."""
    base = dict(
        vwap=19510.0,
        trend_direction="DOWN",
        current_bar_type="two_down",
        previous_bar_type="two_down",
        two_bars_back_type="two_down",
        orb_status="inside",
        orb_high=19560.0,
        orb_low=19470.0,
        previous_day_high=19700.0,
        previous_day_low=19300.0,
        previous_day_close=19600.0,
    )
    base.update(overrides)
    return _base_payload(**base)


def _gate_cfg(tmp_path, **overrides):
    """Production-mirroring permission-gate posture for this lane: the gate is
    ON and vwap_hold specifically is SHADOW_ONLY (its real risk_rules.yaml
    status since 2026-06-26), while other strategies stay eligible so
    unrelated fixtures behave normally."""
    return replace(
        _base_config(tmp_path),
        strategy_permission_gate_enabled=True,
        strategy_permission_default_status="PAPER_ELIGIBLE",
        strategy_status={"vwap_hold": "SHADOW_ONLY"},
        **overrides,
    )


# ─── Pure module: mode resolution ────────────────────────────────────────────

def test_default_mode_is_observe_only(tmp_path):
    cfg = _base_config(tmp_path)
    assert cfg.mnq_vwap_hold_proof_mode == "observe_only"
    assert mnq_vwap_hold_proof_mode(cfg) == "observe_only"


@pytest.mark.parametrize("mode", VALID_MODES)
def test_valid_modes_pass_through(tmp_path, mode):
    cfg = replace(_base_config(tmp_path), mnq_vwap_hold_proof_mode=mode)
    assert mnq_vwap_hold_proof_mode(cfg) == mode


def test_live_is_never_a_valid_value_and_fails_closed_to_observe_only(tmp_path):
    cfg = replace(_base_config(tmp_path), mnq_vwap_hold_proof_mode="live")
    assert mnq_vwap_hold_proof_mode(cfg) == "observe_only"


def test_garbage_value_fails_closed_to_observe_only(tmp_path):
    cfg = replace(_base_config(tmp_path), mnq_vwap_hold_proof_mode="not_a_real_mode")
    assert mnq_vwap_hold_proof_mode(cfg) == "observe_only"


def test_config_validation_rejects_live_at_load_time(tmp_path):
    cfg = replace(_base_config(tmp_path), mnq_vwap_hold_proof_mode="live", max_staleness_seconds=60)
    with pytest.raises(ConfigError, match="MNQ_VWAP_HOLD_PROOF_MODE"):
        _validate_config(cfg)


def test_config_validation_rejects_unknown_value_at_load_time(tmp_path):
    cfg = replace(_base_config(tmp_path), mnq_vwap_hold_proof_mode="paper_sim_typo", max_staleness_seconds=60)
    with pytest.raises(ConfigError, match="MNQ_VWAP_HOLD_PROOF_MODE"):
        _validate_config(cfg)


def test_config_validation_accepts_every_valid_mode(tmp_path):
    for mode in VALID_MODES:
        _validate_config(replace(_base_config(tmp_path), mnq_vwap_hold_proof_mode=mode, max_staleness_seconds=60))


# ─── Pure module: candidate + permission-exception scoping ───────────────────

@pytest.mark.parametrize(
    "instrument,strategy,expected",
    [
        ("MNQ", "vwap_hold", True),
        ("MNQ1!", "vwap_hold", True),
        ("MNQ", "vwap_reclaim", False),
        ("MNQ", "orb_breakout", False),
        ("MES", "vwap_hold", False),
        (None, "vwap_hold", False),
        ("MNQ", None, False),
    ],
)
def test_is_mnq_vwap_hold_candidate(instrument, strategy, expected):
    assert is_mnq_vwap_hold_candidate(instrument, strategy) is expected


@pytest.mark.parametrize(
    "mode,instrument,strategy,session,expected",
    [
        ("paper_sim", "MNQ", "vwap_hold", "new_york", True),
        ("paper_sim", "MNQ1!", "vwap_hold", "new_york", True),
        # every other mode keeps the gate closed — including tradovate_demo
        ("observe_only", "MNQ", "vwap_hold", "new_york", False),
        ("tradovate_demo", "MNQ", "vwap_hold", "new_york", False),
        # session scoping
        ("paper_sim", "MNQ", "vwap_hold", "london", False),
        ("paper_sim", "MNQ", "vwap_hold", "asian", False),
        ("paper_sim", "MNQ", "vwap_hold", None, False),
        # instrument/strategy scoping
        ("paper_sim", "MES", "vwap_hold", "new_york", False),
        ("paper_sim", "MNQ", "vwap_reclaim", "new_york", False),
        ("paper_sim", "MNQ", "orb_reclaim", "new_york", False),
    ],
)
def test_permission_gate_exception_truth_table(tmp_path, mode, instrument, strategy, session, expected):
    cfg = replace(_base_config(tmp_path), mnq_vwap_hold_proof_mode=mode)
    assert permission_gate_exception(instrument, strategy, session, cfg) is expected


# ─── Pure module: campaign dedupe ────────────────────────────────────────────

def test_fresh_day_has_no_campaign(tmp_path):
    assert campaign_already_attempted(str(tmp_path), direction="SHORT") is False


def test_record_then_detect_campaign(tmp_path):
    record_campaign_attempt(str(tmp_path), direction="SHORT")
    assert campaign_already_attempted(str(tmp_path), direction="SHORT") is True
    assert campaign_already_attempted(str(tmp_path), direction="LONG") is False


def test_campaigns_are_per_date(tmp_path):
    d1, d2 = date(2026, 7, 14), date(2026, 7, 15)
    record_campaign_attempt(str(tmp_path), direction="SHORT", for_date=d1)
    assert campaign_already_attempted(str(tmp_path), direction="SHORT", for_date=d1) is True
    assert campaign_already_attempted(str(tmp_path), direction="SHORT", for_date=d2) is False


def test_corrupt_campaign_file_fails_soft(tmp_path):
    (tmp_path / f"mnq_vwap_hold_proof_campaigns_{date.today().isoformat()}.json").write_text("{corrupt")
    assert campaign_already_attempted(str(tmp_path), direction="SHORT") is False
    record_campaign_attempt(str(tmp_path), direction="SHORT")
    assert campaign_already_attempted(str(tmp_path), direction="SHORT") is True


def test_campaign_file_is_independent_of_orb_lanes(tmp_path):
    from context.mnq_orb_breakout_proof import record_campaign_attempt as record_breakout
    from context.mnq_orb_reclaim_proof import record_campaign_attempt as record_reclaim

    record_breakout(str(tmp_path), orb_high=1.0, orb_low=0.0, direction="SHORT")
    record_reclaim(str(tmp_path), orb_high=1.0, orb_low=0.0, direction="SHORT")
    assert campaign_already_attempted(str(tmp_path), direction="SHORT") is False


# ─── Pure module: evaluate ───────────────────────────────────────────────────

def test_evaluate_observe_only_is_a_noop(tmp_path):
    cfg = _base_config(tmp_path)
    d = evaluate_mnq_vwap_hold_proof(
        cfg=cfg, log_dir=str(tmp_path), session="new_york", direction="SHORT"
    )
    assert (d.suppress, d.apply_override, d.force_paper_broker) == (False, False, False)


def test_evaluate_tradovate_demo_is_a_noop_documented_deviation(tmp_path):
    """Unlike the orb lanes, tradovate_demo must NOT arm overrides here —
    IOC/static on Tradovate demo is the proven-negative configuration."""
    cfg = replace(_base_config(tmp_path), mnq_vwap_hold_proof_mode="tradovate_demo")
    d = evaluate_mnq_vwap_hold_proof(
        cfg=cfg, log_dir=str(tmp_path), session="new_york", direction="SHORT"
    )
    assert (d.suppress, d.apply_override) == (False, False)
    assert "tradovate_demo" in d.reason


def test_evaluate_paper_sim_out_of_session_is_a_noop(tmp_path):
    cfg = replace(_base_config(tmp_path), mnq_vwap_hold_proof_mode="paper_sim")
    d = evaluate_mnq_vwap_hold_proof(
        cfg=cfg, log_dir=str(tmp_path), session="london", direction="SHORT"
    )
    assert (d.suppress, d.apply_override) == (False, False)
    assert d.session_in_scope is False


def test_evaluate_paper_sim_first_attempt_applies_full_override(tmp_path):
    cfg = replace(_base_config(tmp_path), mnq_vwap_hold_proof_mode="paper_sim")
    d = evaluate_mnq_vwap_hold_proof(
        cfg=cfg, log_dir=str(tmp_path), session="new_york", direction="SHORT"
    )
    assert d.apply_override is True
    assert d.suppress is False
    assert d.force_market_entry is True
    assert d.force_runner_exit is True
    assert d.force_paper_broker is True


def test_evaluate_paper_sim_duplicate_campaign_suppresses(tmp_path):
    cfg = replace(_base_config(tmp_path), mnq_vwap_hold_proof_mode="paper_sim")
    record_campaign_attempt(str(tmp_path), direction="SHORT")
    d = evaluate_mnq_vwap_hold_proof(
        cfg=cfg, log_dir=str(tmp_path), session="new_york", direction="SHORT"
    )
    assert d.suppress is True
    assert d.duplicate_campaign is True
    assert d.apply_override is False


# ─── Runner integration ──────────────────────────────────────────────────────

def test_observe_only_leaves_permission_block_untouched(tmp_path):
    """Zero-behavior-change regression lock: with the production gate posture
    and the default observe_only mode, a qualifying vwap_hold candidate keeps
    dying at STRATEGY_NOT_PAPER_ELIGIBLE — the blocked row itself is the
    observation evidence for the lane's bounded gate."""
    import json

    today = date(2026, 5, 23)
    cfg = _gate_cfg(tmp_path)
    result = process_alert(
        _vwap_hold_payload(timestamp="2026-05-23T15:00:00+00:00"),
        config=cfg, log_dir=cfg.log_dir, for_date=today,
    )
    assert result["decision"] == "NO_TRADE"
    assert "STRATEGY_NOT_PAPER_ELIGIBLE" in (result.get("failed_gates") or [])
    journal_path = next(Path(cfg.log_dir).glob("journal_*.jsonl"))
    rows = [json.loads(line) for line in journal_path.read_text().splitlines()]
    blocked = next(r for r in rows if "STRATEGY_NOT_PAPER_ELIGIBLE" in (r.get("failed_gates") or []))
    assert blocked["setup"]["strategy"] == "vwap_hold"


def test_tradovate_demo_keeps_permission_gate_closed(tmp_path):
    today = date(2026, 5, 23)
    cfg = _gate_cfg(tmp_path, mnq_vwap_hold_proof_mode="tradovate_demo")
    result = process_alert(
        _vwap_hold_payload(timestamp="2026-05-23T15:00:00+00:00"),
        config=cfg, log_dir=cfg.log_dir, for_date=today,
    )
    assert result["decision"] == "NO_TRADE"
    assert "STRATEGY_NOT_PAPER_ELIGIBLE" in (result.get("failed_gates") or [])


def test_paper_sim_outside_ny_session_stays_blocked(tmp_path):
    """12:00 UTC = 08:00 ET = london — the exception must not open."""
    today = date(2026, 5, 23)
    cfg = _gate_cfg(tmp_path, mnq_vwap_hold_proof_mode="paper_sim")
    result = process_alert(
        _vwap_hold_payload(timestamp="2026-05-23T12:00:00+00:00"),
        config=cfg, log_dir=cfg.log_dir, for_date=today,
    )
    assert result["decision"] == "NO_TRADE"
    assert "STRATEGY_NOT_PAPER_ELIGIBLE" in (result.get("failed_gates") or [])


def test_paper_sim_ny_session_opens_gate_and_forces_paper_broker(tmp_path, monkeypatch):
    import json

    captured = {}
    real_init = PaperBroker.__init__

    def spy_init(self, *args, **kwargs):
        captured.update(kwargs)
        return real_init(self, *args, **kwargs)

    monkeypatch.setattr(PaperBroker, "__init__", spy_init)

    today = date(2026, 5, 23)
    cfg = _gate_cfg(tmp_path, mnq_vwap_hold_proof_mode="paper_sim")
    result = process_alert(
        _vwap_hold_payload(timestamp="2026-05-23T15:00:00+00:00"),
        config=cfg, log_dir=cfg.log_dir, for_date=today,
    )
    assert result["decision"] == "TRADE"
    assert captured.get("runner_mode") is True
    assert captured.get("entry_fill_model") == "market"
    assert result["fill"]["paper_order_id"].startswith("PAPER-")

    journal_path = next(Path(cfg.log_dir).glob("journal_*.jsonl"))
    rows = [json.loads(line) for line in journal_path.read_text().splitlines()]
    confirmed = next(r for r in rows if r.get("decision") == "TRADE")
    assert confirmed["setup"]["strategy"] == "vwap_hold"
    assert confirmed["paper_order_id"] == result["fill"]["paper_order_id"]
    audit = confirmed["mnq_vwap_hold_proof_audit"]
    assert audit["force_paper_broker"] is True
    assert audit["force_market_entry"] is True
    assert audit["force_runner_exit"] is True
    assert audit["session_in_scope"] is True


def test_paper_sim_works_without_permission_gate_too(tmp_path):
    """The test fixture default (gate disabled) — the proof hook still applies
    its override; the lane does not DEPEND on the gate being enabled."""
    import json

    today = date(2026, 5, 23)
    cfg = replace(_base_config(tmp_path), mnq_vwap_hold_proof_mode="paper_sim")
    result = process_alert(
        _vwap_hold_payload(timestamp="2026-05-23T15:00:00+00:00"),
        config=cfg, log_dir=cfg.log_dir, for_date=today,
    )
    assert result["decision"] == "TRADE"
    journal_path = next(Path(cfg.log_dir).glob("journal_*.jsonl"))
    rows = [json.loads(line) for line in journal_path.read_text().splitlines()]
    confirmed = next(r for r in rows if r.get("decision") == "TRADE")
    assert confirmed["mnq_vwap_hold_proof_audit"]["apply_override"] is True


def test_duplicate_campaign_is_suppressed_before_risk_and_broker(tmp_path, monkeypatch):
    """Critical for THIS lane: a duplicate flows through an OPEN permission
    gate, so without suppression it would reach the normal IOC/static path.
    Pre-seed the campaign file, then prove risk and broker are never touched."""
    import risk.risk_engine as risk_module

    today = date(2026, 5, 23)
    cfg = _gate_cfg(tmp_path, mnq_vwap_hold_proof_mode="paper_sim")
    record_campaign_attempt(cfg.log_dir, direction="SHORT", for_date=today)

    def _risk_must_not_run(*args, **kwargs):
        raise AssertionError("risk evaluation reached for a suppressed duplicate")

    monkeypatch.setattr(risk_module.RiskEngine, "validate", _risk_must_not_run)
    result = process_alert(
        _vwap_hold_payload(timestamp="2026-05-23T15:00:00+00:00"),
        config=cfg, log_dir=cfg.log_dir, for_date=today,
    )
    assert result["decision"] == "NO_TRADE"
    assert "MNQ_VWAP_HOLD_PROOF_DUPLICATE" in (result.get("failed_gates") or [])
    assert result.get("fill") is None


def test_generic_gates_still_apply_in_paper_sim(tmp_path):
    """The permission exception opens ONLY the permission gate — a RANGE_BOUND
    market is still rejected by the market-condition gate exactly as before."""
    today = date(2026, 5, 23)
    cfg = _gate_cfg(tmp_path, mnq_vwap_hold_proof_mode="paper_sim")
    result = process_alert(
        _vwap_hold_payload(
            timestamp="2026-05-23T15:00:00+00:00", market_condition="RANGE_BOUND"
        ),
        config=cfg, log_dir=cfg.log_dir, for_date=today,
    )
    assert result["decision"] == "NO_TRADE"


def test_paper_sim_open_position_never_enters_tradovate_resolution(tmp_path, monkeypatch):
    """The PR #281 lifecycle-isolation defect class, proven closed for this
    lane too: a paper vwap_hold position must never be reconstructed as a
    real TradovateBroker on a later bar's resolution. This is the exact test
    shape that caught the real journal_logger.py carry-forward bug when the
    orb_breakout lane was built."""
    import json
    import execution.tradovate_broker as tradovate_module

    monkeypatch.setenv("BROKER", "tradovate")
    monkeypatch.setenv("TRADOVATE_ENV", "demo")
    today = date(2026, 5, 23)
    cfg = _gate_cfg(tmp_path, paper_mode=False, mnq_vwap_hold_proof_mode="paper_sim")
    opened = process_alert(
        _vwap_hold_payload(timestamp="2026-05-23T15:00:00+00:00"),
        config=cfg, log_dir=cfg.log_dir, for_date=today,
    )
    assert opened["decision"] == "TRADE"
    paper_order_id = opened["fill"]["paper_order_id"]

    class _TradovateMustNotBeConstructed:
        def __init__(self, *args, **kwargs):
            raise AssertionError("paper_sim resolution reached Tradovate")

    monkeypatch.setattr(tradovate_module, "TradovateBroker", _TradovateMustNotBeConstructed)
    resolved = process_alert(
        _vwap_hold_payload(
            timestamp="2026-05-23T15:15:00+00:00",
            high=19560.0,
            low=19470.0,
            close=19530.0,
        ),
        config=cfg, log_dir=cfg.log_dir, for_date=today,
    )
    assert resolved["resolution"] in {"WIN", "LOSS", "BREAKEVEN"}

    journal_path = next(Path(cfg.log_dir).glob("journal_*.jsonl"))
    rows = [json.loads(line) for line in journal_path.read_text().splitlines()]
    outcome = next(r for r in rows if r.get("type") == "OUTCOME")
    assert outcome["outcome"]["paper_order_id"] == paper_order_id


def test_paper_sim_does_not_affect_orb_reclaim_on_mnq(tmp_path):
    """Scope proof: activating vwap_hold paper_sim must not touch a genuine
    orb_reclaim candidate on the same instrument."""
    import json

    today = date(2026, 5, 23)
    cfg = replace(_base_config(tmp_path), mnq_vwap_hold_proof_mode="paper_sim")
    result = process_alert(
        _base_payload(timestamp="2026-05-23T15:00:00+00:00"),  # default fixture -> orb_reclaim
        config=cfg, log_dir=cfg.log_dir, for_date=today,
    )
    assert result["decision"] == "TRADE"
    journal_path = next(Path(cfg.log_dir).glob("journal_*.jsonl"))
    rows = [json.loads(line) for line in journal_path.read_text().splitlines()]
    trade_intent = next(r for r in rows if r.get("decision") == "TRADE_INTENT")
    assert trade_intent["setup"]["strategy"] == "orb_reclaim"
    assert "mnq_vwap_hold_proof_audit" not in trade_intent


def test_all_three_proof_lanes_active_stay_independent(tmp_path):
    """MNQ_ORB_RECLAIM/ORB_BREAKOUT/VWAP_HOLD proof modes all paper_sim at
    once — each strategy gets only its OWN audit key, never another lane's."""
    import json

    today = date(2026, 5, 23)
    cfg = replace(
        _base_config(tmp_path),
        mnq_orb_reclaim_proof_mode="paper_sim",
        mnq_orb_breakout_proof_mode="paper_sim",
        mnq_vwap_hold_proof_mode="paper_sim",
    )
    result = process_alert(
        _vwap_hold_payload(timestamp="2026-05-23T15:00:00+00:00"),
        config=cfg, log_dir=cfg.log_dir, for_date=today,
    )
    assert result["decision"] == "TRADE"
    journal_path = next(Path(cfg.log_dir).glob("journal_*.jsonl"))
    rows = [json.loads(line) for line in journal_path.read_text().splitlines()]
    confirmed = next(r for r in rows if r.get("decision") == "TRADE")
    assert confirmed["setup"]["strategy"] == "vwap_hold"
    assert "mnq_vwap_hold_proof_audit" in confirmed
    assert "mnq_orb_reclaim_proof_audit" not in confirmed
    assert "mnq_orb_breakout_proof_audit" not in confirmed
