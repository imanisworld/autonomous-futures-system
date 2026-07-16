"""
tests/test_mnq_orb_breakout_proof.py

Strategy-restoration candidate #2 (2026-07-13/14): MNQ orb_breakout proof mode.
Scoped narrowly to MNQ + orb_breakout only — see
context/mnq_orb_breakout_proof.py and docs/orb-breakout-entry-study-
2026-07-11.md for the evidence trail this is based on.

Mirrors tests/test_mnq_orb_reclaim_proof.py's coverage exactly, adapted to
orb_breakout's own candidate shape (orb_status="above" + volume confirmation,
not "reclaimed_high"), plus explicit no-cross-contamination proofs between
the two independent proof lanes.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from config.settings import ConfigError, SystemConfig, _validate_config
from context.mnq_orb_breakout_proof import (
    VALID_MODES,
    campaign_already_attempted,
    evaluate_mnq_orb_breakout_proof,
    is_mnq_orb_breakout_candidate,
    mnq_orb_breakout_proof_mode,
    record_campaign_attempt,
)
from execution.paper_broker import PaperBroker
from tests.test_e2e_scenarios import _base_config, _base_payload
from webhook.runner import process_alert


def _config_with_breakout(tmp_path, **overrides):
    """_base_config's enabled_concepts fixture list predates this lane and
    doesn't include orb_breakout -- production's real risk_rules.yaml does
    (strategy.enabled_concepts), this only patches the TEST fixture. Also
    applies the already-validated, already-deployed MNQ orb_breakout stop
    offset (orb_stop_ticks: {MNQ: 48} in risk_rules.yaml -- see the 622-day
    sweep in that file's comments) instead of the test fixture's unset
    default (falls back to the legacy 8-tick offset in signal_engine.py).
    The narrow 8-tick stop is exactly what makes the target sit too close to
    entry, which is why an orb_breakout candidate on this same base payload
    fails ENTRY_DETACHED_FROM_PRICE even before the proof mode is involved --
    not a bug in the proof-mode wiring, a correct rejection of an
    unvalidated stop width. 48 ticks is what production actually runs."""
    base = _base_config(tmp_path)
    concepts = list(base.enabled_concepts) + ["orb_breakout"]
    return replace(base, enabled_concepts=concepts, orb_stop_ticks={"MNQ": 48}, **overrides)


def _breakout_payload(**overrides):
    """orb_status='above' + volume confirmation (relative >= 1.2) fires
    _try_orb_breakout in strategy/signal_engine.py, distinct from the
    default fixture's 'reclaimed_high' (orb_reclaim)."""
    base = dict(orb_status="above", volume=5000, avg_volume=3800)
    base.update(overrides)
    return _base_payload(**base)


# ─── Pure module: mode resolution ────────────────────────────────────────────

def test_default_mode_is_observe_only(tmp_path):
    cfg = _base_config(tmp_path)
    assert cfg.mnq_orb_breakout_proof_mode == "observe_only"
    assert mnq_orb_breakout_proof_mode(cfg) == "observe_only"


@pytest.mark.parametrize("mode", VALID_MODES)
def test_valid_modes_pass_through(tmp_path, mode):
    cfg = replace(_base_config(tmp_path), mnq_orb_breakout_proof_mode=mode)
    assert mnq_orb_breakout_proof_mode(cfg) == mode


def test_live_is_never_a_valid_value_and_fails_closed_to_observe_only(tmp_path):
    cfg = replace(_base_config(tmp_path), mnq_orb_breakout_proof_mode="live")
    assert mnq_orb_breakout_proof_mode(cfg) == "observe_only"


def test_garbage_value_fails_closed_to_observe_only(tmp_path):
    cfg = replace(_base_config(tmp_path), mnq_orb_breakout_proof_mode="not_a_real_mode")
    assert mnq_orb_breakout_proof_mode(cfg) == "observe_only"


def test_config_validation_rejects_live_at_load_time(tmp_path):
    cfg = replace(_base_config(tmp_path), mnq_orb_breakout_proof_mode="live", max_staleness_seconds=60)
    with pytest.raises(ConfigError, match="MNQ_ORB_BREAKOUT_PROOF_MODE"):
        _validate_config(cfg)


def test_config_validation_rejects_unknown_value_at_load_time(tmp_path):
    cfg = replace(_base_config(tmp_path), mnq_orb_breakout_proof_mode="paper_sim_typo", max_staleness_seconds=60)
    with pytest.raises(ConfigError, match="MNQ_ORB_BREAKOUT_PROOF_MODE"):
        _validate_config(cfg)


def test_config_validation_accepts_every_valid_mode(tmp_path):
    for mode in VALID_MODES:
        _validate_config(replace(_base_config(tmp_path), mnq_orb_breakout_proof_mode=mode, max_staleness_seconds=60))


# ─── Pure module: candidate scoping ───────────────────────────────────────────

@pytest.mark.parametrize(
    "instrument,strategy,expected",
    [
        ("MNQ", "orb_breakout", True),
        ("MNQ1!", "orb_breakout", True),
        ("MNQ", "orb_reclaim", False),
        ("MNQ", "range_signal", False),
        ("MES", "orb_breakout", False),
        (None, "orb_breakout", False),
        ("MNQ", None, False),
    ],
)
def test_is_mnq_orb_breakout_candidate(instrument, strategy, expected):
    assert is_mnq_orb_breakout_candidate(instrument, strategy) is expected


# ─── Pure module: campaign dedupe ────────────────────────────────────────────

def test_campaign_not_attempted_when_file_missing(tmp_path):
    assert campaign_already_attempted(
        str(tmp_path), orb_high=100.0, orb_low=90.0, direction="LONG"
    ) is False


def test_campaign_dedupe_round_trip(tmp_path):
    log_dir = str(tmp_path)
    d = date(2026, 7, 14)
    assert not campaign_already_attempted(
        log_dir, orb_high=100.0, orb_low=90.0, direction="LONG", for_date=d
    )
    record_campaign_attempt(log_dir, orb_high=100.0, orb_low=90.0, direction="LONG", for_date=d)
    assert campaign_already_attempted(
        log_dir, orb_high=100.0, orb_low=90.0, direction="LONG", for_date=d
    )
    assert not campaign_already_attempted(
        log_dir, orb_high=100.0, orb_low=90.0, direction="SHORT", for_date=d
    )


def test_campaign_file_is_fail_soft_on_corrupt_json(tmp_path):
    log_dir = str(tmp_path)
    d = date(2026, 7, 14)
    path = Path(log_dir) / f"mnq_orb_breakout_proof_campaigns_{d.isoformat()}.json"
    path.write_text("{not valid json")
    assert campaign_already_attempted(
        log_dir, orb_high=100.0, orb_low=90.0, direction="LONG", for_date=d
    ) is False
    record_campaign_attempt(log_dir, orb_high=100.0, orb_low=90.0, direction="LONG", for_date=d)
    assert campaign_already_attempted(
        log_dir, orb_high=100.0, orb_low=90.0, direction="LONG", for_date=d
    )


def test_campaign_files_are_independent_between_the_two_proof_lanes(tmp_path):
    """Distinct filenames (mnq_orb_breakout_proof_campaigns_* vs
    mnq_orb_reclaim_proof_campaigns_*) -- recording one never dedupes the
    other, proving the two lanes cannot cross-suppress each other."""
    from context.mnq_orb_reclaim_proof import (
        campaign_already_attempted as reclaim_attempted,
        record_campaign_attempt as record_reclaim,
    )
    log_dir = str(tmp_path)
    d = date(2026, 7, 14)
    record_campaign_attempt(log_dir, orb_high=100.0, orb_low=90.0, direction="LONG", for_date=d)
    assert not reclaim_attempted(log_dir, orb_high=100.0, orb_low=90.0, direction="LONG", for_date=d)
    record_reclaim(log_dir, orb_high=200.0, orb_low=190.0, direction="SHORT", for_date=d)
    assert not campaign_already_attempted(log_dir, orb_high=200.0, orb_low=190.0, direction="SHORT", for_date=d)


# ─── Pure module: evaluate_mnq_orb_breakout_proof state machine ──────────────

def test_observe_only_never_suppresses_or_overrides(tmp_path):
    cfg = replace(_base_config(tmp_path), mnq_orb_breakout_proof_mode="observe_only")
    decision = evaluate_mnq_orb_breakout_proof(
        cfg=cfg, log_dir=str(tmp_path), orb_high=100.0, orb_low=90.0, direction="LONG"
    )
    assert decision.suppress is False
    assert decision.apply_override is False
    assert decision.force_market_entry is False
    assert decision.force_runner_exit is False
    assert decision.force_paper_broker is False


def test_paper_sim_first_attempt_applies_override(tmp_path):
    cfg = replace(_base_config(tmp_path), mnq_orb_breakout_proof_mode="paper_sim")
    decision = evaluate_mnq_orb_breakout_proof(
        cfg=cfg, log_dir=str(tmp_path), orb_high=100.0, orb_low=90.0, direction="LONG"
    )
    assert decision.suppress is False
    assert decision.apply_override is True
    assert decision.force_market_entry is True
    assert decision.force_runner_exit is True
    assert decision.force_paper_broker is True


def test_tradovate_demo_first_attempt_applies_override_without_forcing_paper_broker(tmp_path):
    cfg = replace(_base_config(tmp_path), mnq_orb_breakout_proof_mode="tradovate_demo")
    decision = evaluate_mnq_orb_breakout_proof(
        cfg=cfg, log_dir=str(tmp_path), orb_high=100.0, orb_low=90.0, direction="LONG"
    )
    assert decision.apply_override is True
    assert decision.force_paper_broker is False


def test_duplicate_campaign_suppresses_under_active_mode(tmp_path):
    cfg = replace(_base_config(tmp_path), mnq_orb_breakout_proof_mode="paper_sim")
    log_dir = str(tmp_path)
    record_campaign_attempt(log_dir, orb_high=100.0, orb_low=90.0, direction="LONG")
    decision = evaluate_mnq_orb_breakout_proof(
        cfg=cfg, log_dir=log_dir, orb_high=100.0, orb_low=90.0, direction="LONG"
    )
    assert decision.suppress is True
    assert decision.apply_override is False
    assert decision.duplicate_campaign is True


# ─── Runner integration ───────────────────────────────────────────────────────

def test_observe_only_does_not_change_existing_orb_breakout_behavior(tmp_path):
    """Regression lock, mirroring the orb_reclaim lane's own caught regression:
    observe_only must be a pure audit no-op, never redirecting the decision."""
    today = date(2026, 5, 23)
    cfg = _config_with_breakout(tmp_path)
    assert cfg.mnq_orb_breakout_proof_mode == "observe_only"
    result = process_alert(
        _breakout_payload(timestamp="2026-05-23T15:00:00+00:00"),
        config=cfg, log_dir=cfg.log_dir, for_date=today,
    )
    assert result["decision"] == "TRADE", f"fixture did not produce a baseline orb_breakout TRADE: {result}"


def test_observe_only_audit_recorded_but_trade_proceeds(tmp_path):
    import json

    today = date(2026, 5, 23)
    cfg = _config_with_breakout(tmp_path)
    result = process_alert(
        _breakout_payload(timestamp="2026-05-23T15:00:00+00:00"),
        config=cfg, log_dir=cfg.log_dir, for_date=today,
    )
    assert result["decision"] == "TRADE"
    journal_path = next(Path(cfg.log_dir).glob("journal_*.jsonl"))
    rows = [json.loads(line) for line in journal_path.read_text().splitlines()]
    trade_intent = next(r for r in rows if r.get("decision") == "TRADE_INTENT")
    audit = trade_intent["mnq_orb_breakout_proof_audit"]
    assert audit["proof_mode"] == "observe_only"
    assert audit["apply_override"] is False
    assert "mnq_orb_reclaim_proof_audit" not in trade_intent


def test_paper_sim_forces_paper_broker_with_runner_mode_for_mnq_orb_breakout(tmp_path, monkeypatch):
    captured = {}
    real_init = PaperBroker.__init__

    def spy_init(self, *args, **kwargs):
        captured.update(kwargs)
        return real_init(self, *args, **kwargs)

    monkeypatch.setattr(PaperBroker, "__init__", spy_init)

    today = date(2026, 5, 23)
    cfg = replace(_config_with_breakout(tmp_path), mnq_orb_breakout_proof_mode="paper_sim")
    result = process_alert(
        _breakout_payload(timestamp="2026-05-23T15:00:00+00:00"),
        config=cfg, log_dir=cfg.log_dir, for_date=today,
    )
    assert result["decision"] == "TRADE"
    assert captured.get("runner_mode") is True
    assert captured.get("entry_fill_model") == "market"
    assert result["fill"]["paper_order_id"].startswith("PAPER-")

    import json
    journal_path = next(Path(cfg.log_dir).glob("journal_*.jsonl"))
    rows = [json.loads(line) for line in journal_path.read_text().splitlines()]
    confirmed = next(r for r in rows if r.get("decision") == "TRADE")
    assert confirmed["paper_order_id"] == result["fill"]["paper_order_id"]
    assert confirmed["mnq_orb_breakout_proof_audit"]["force_paper_broker"] is True


def test_paper_sim_open_position_never_enters_tradovate_resolution(tmp_path, monkeypatch):
    """Proves the same PR #281 lifecycle-isolation fix applies to this lane
    too -- a paper orb_breakout position must never be reconstructed as a
    real TradovateBroker on a later bar's resolution."""
    import json
    import execution.tradovate_broker as tradovate_module

    monkeypatch.setenv("BROKER", "tradovate")
    monkeypatch.setenv("TRADOVATE_ENV", "demo")
    today = date(2026, 5, 23)
    cfg = replace(
        _config_with_breakout(tmp_path),
        paper_mode=False,
        mnq_orb_breakout_proof_mode="paper_sim",
    )
    opened = process_alert(
        _breakout_payload(timestamp="2026-05-23T15:00:00+00:00"),
        config=cfg, log_dir=cfg.log_dir, for_date=today,
    )
    assert opened["decision"] == "TRADE"
    paper_order_id = opened["fill"]["paper_order_id"]

    class _TradovateMustNotBeConstructed:
        def __init__(self, *args, **kwargs):
            raise AssertionError("paper_sim resolution reached Tradovate")

    monkeypatch.setattr(tradovate_module, "TradovateBroker", _TradovateMustNotBeConstructed)
    resolved = process_alert(
        _breakout_payload(
            timestamp="2026-05-23T15:15:00+00:00",
            high=19510.0,
            low=19000.0,
            close=19400.0,
        ),
        config=cfg, log_dir=cfg.log_dir, for_date=today,
    )
    assert resolved["resolution"] == "LOSS"

    journal_path = next(Path(cfg.log_dir).glob("journal_*.jsonl"))
    rows = [json.loads(line) for line in journal_path.read_text().splitlines()]
    outcome = next(r for r in rows if r.get("type") == "OUTCOME")
    assert outcome["outcome"]["paper_order_id"] == paper_order_id


def test_paper_sim_does_not_affect_orb_reclaim_on_mnq(tmp_path):
    """Scope proof, mirrored from the orb_reclaim lane's own scope test but
    inverted: activating orb_breakout paper_sim must not touch a genuine
    orb_reclaim candidate on the same instrument."""
    import json

    today = date(2026, 5, 23)
    cfg = replace(_config_with_breakout(tmp_path), mnq_orb_breakout_proof_mode="paper_sim")
    result = process_alert(
        _base_payload(timestamp="2026-05-23T15:00:00+00:00"),  # default fixture -> orb_reclaim
        config=cfg, log_dir=cfg.log_dir, for_date=today,
    )
    assert result["decision"] == "TRADE"
    journal_path = next(Path(cfg.log_dir).glob("journal_*.jsonl"))
    rows = [json.loads(line) for line in journal_path.read_text().splitlines()]
    trade_intent = next(r for r in rows if r.get("decision") == "TRADE_INTENT")
    assert trade_intent["setup"]["strategy"] == "orb_reclaim"
    assert "mnq_orb_breakout_proof_audit" not in trade_intent


def test_both_proof_lanes_active_simultaneously_stay_independent(tmp_path):
    """Both MNQ_ORB_RECLAIM_PROOF_MODE and MNQ_ORB_BREAKOUT_PROOF_MODE set to
    paper_sim at once -- each strategy gets only its OWN audit, never both,
    since a decision can only match one strategy's candidate check."""
    import json

    today = date(2026, 5, 23)
    cfg = replace(
        _config_with_breakout(tmp_path),
        mnq_orb_reclaim_proof_mode="paper_sim",
        mnq_orb_breakout_proof_mode="paper_sim",
    )
    breakout_result = process_alert(
        _breakout_payload(timestamp="2026-05-23T15:00:00+00:00"),
        config=cfg, log_dir=cfg.log_dir, for_date=today,
    )
    assert breakout_result["decision"] == "TRADE"
    journal_path = next(Path(cfg.log_dir).glob("journal_*.jsonl"))
    rows = [json.loads(line) for line in journal_path.read_text().splitlines()]
    trade_intent = next(r for r in rows if r.get("decision") == "TRADE_INTENT")
    assert "mnq_orb_breakout_proof_audit" in trade_intent
    assert "mnq_orb_reclaim_proof_audit" not in trade_intent


def test_generic_range_bound_still_blocked_with_paper_sim_active(tmp_path):
    today = date(2026, 5, 23)
    cfg = replace(_config_with_breakout(tmp_path), mnq_orb_breakout_proof_mode="paper_sim")
    result = process_alert(
        _breakout_payload(
            timestamp="2026-05-23T15:00:00+00:00",
            market_condition="RANGE_BOUND",
        ),
        config=cfg, log_dir=cfg.log_dir, for_date=today,
    )
    assert result["decision"] == "NO_TRADE"
    assert "MARKET_CONDITION_NOT_TRENDING" in result["failed_gates"]


def test_duplicate_campaign_never_reaches_risk_or_broker(tmp_path, monkeypatch):
    """Pre-seeds the campaign file directly (same technique the pure-module
    test above already uses) rather than chaining two live alerts -- a
    second alert's own open-position resolution/re-evaluation timing is an
    orthogonal concern already covered by the runner's own position-lifecycle
    tests, not something this proof-mode duplicate check needs to reproduce.
    This isolates exactly the claim in the test name: risk/broker are never
    reached for a pre-existing campaign, proven by arming both to raise."""
    import webhook.runner as runner_module

    today = date(2026, 5, 23)
    cfg = replace(_config_with_breakout(tmp_path), mnq_orb_breakout_proof_mode="paper_sim")
    record_campaign_attempt(cfg.log_dir, orb_high=19498.0, orb_low=19462.0, direction="LONG", for_date=today)

    class _RiskMustNotBeConstructed:
        def __init__(self, *args, **kwargs):
            raise AssertionError("duplicate campaign must not reach risk")

    def _broker_must_not_execute(*args, **kwargs):
        raise AssertionError("duplicate campaign must not reach the broker")

    monkeypatch.setattr(runner_module, "RiskEngine", _RiskMustNotBeConstructed)
    monkeypatch.setattr(PaperBroker, "execute_bracket", _broker_must_not_execute)

    result = process_alert(
        _breakout_payload(timestamp="2026-05-23T15:00:00+00:00"),
        config=cfg, log_dir=cfg.log_dir, for_date=today,
    )
    assert result["decision"] == "NO_TRADE"
    assert "MNQ_ORB_BREAKOUT_PROOF_DUPLICATE" in result["failed_gates"]


def test_confirmed_trade_still_requires_paper_order_id(tmp_path, monkeypatch):
    from execution.broker_interface import Fill

    def unidentifiable_open(self, order, market_price=None):
        return Fill(
            instrument=order.instrument, direction=order.direction,
            contracts=order.contracts, entry_price=order.entry,
            exit_price=None, exit_reason=None, result="OPEN",
            pnl_ticks=None, pnl_dollars=None,
        )

    monkeypatch.setattr(PaperBroker, "execute_bracket", unidentifiable_open)
    today = date(2026, 5, 23)
    cfg = replace(_config_with_breakout(tmp_path), mnq_orb_breakout_proof_mode="paper_sim")
    result = process_alert(
        _breakout_payload(timestamp="2026-05-23T15:00:00+00:00"),
        config=cfg, log_dir=cfg.log_dir, for_date=today,
    )
    assert result["decision"] == "BLOCKED_ORDER_CONFIRMATION_MISSING"


# ─── Detached-candidate carve-out (2026-07-14, first natural candidate) ──────
# The lane's first natural live candidate (2026-07-14 11:30Z) died at
# ENTRY_DETACHED_FROM_PRICE — entry 29603.5 vs live 29641 — proving the lane
# as first built could never capture its own target defect (the decision
# became NO_TRADE before the proof hook, which requires TRADE). These tests
# lock the scoped carve-out + the honest live-price paper fill that fix it.

def test_detached_candidate_still_rejected_in_observe_only(tmp_path):
    """Zero-behavior-change regression lock: observe_only keeps the
    entry-sanity guard exactly as deployed — detached candidates die at
    ENTRY_DETACHED_FROM_PRICE."""
    today = date(2026, 5, 23)
    cfg = _config_with_breakout(tmp_path)  # default observe_only
    result = process_alert(
        _breakout_payload(timestamp="2026-05-23T15:00:00+00:00", close=19540.0, high=19545.0),
        config=cfg, log_dir=cfg.log_dir, for_date=today,
    )
    assert result["decision"] == "NO_TRADE"
    assert "ENTRY_DETACHED_FROM_PRICE" in (result.get("failed_gates") or [])


def test_detached_candidate_trades_in_paper_sim_at_live_price(tmp_path):
    """paper_sim: the detached candidate reaches TRADE and the paper fill is
    the LIVE close (+ adverse slippage), never the stale anchor — mirroring
    what Tradovate's force_market_entry Market order would actually do."""
    import json

    today = date(2026, 5, 23)
    cfg = replace(_config_with_breakout(tmp_path), mnq_orb_breakout_proof_mode="paper_sim")
    live_close = 19540.0
    result = process_alert(
        _breakout_payload(timestamp="2026-05-23T15:00:00+00:00", close=live_close, high=19545.0),
        config=cfg, log_dir=cfg.log_dir, for_date=today,
    )
    assert result["decision"] == "TRADE"
    tick = 0.25
    expected_fill = live_close + float(getattr(cfg, "fill_slippage_ticks", 0.0) or 0.0) * tick
    assert result["fill"]["entry"] == pytest.approx(expected_fill)
    # the anchored plan stays in the audit for packet review
    journal_path = next(Path(cfg.log_dir).glob("journal_*.jsonl"))
    rows = [json.loads(line) for line in journal_path.read_text().splitlines()]
    confirmed = next(r for r in rows if r.get("decision") == "TRADE")
    assert confirmed["mnq_orb_breakout_proof_audit"]["would_be_setup"]["entry"] == 19498.5


def test_detached_orb_reclaim_is_not_carved_out(tmp_path):
    """Scope proof: the carve-out never applies to orb_reclaim, even with its
    own proof lane in paper_sim — its detachment question belongs to the
    entry-refresh shadow lane, not this one."""
    today = date(2026, 5, 23)
    cfg = replace(
        _config_with_breakout(tmp_path),
        mnq_orb_reclaim_proof_mode="paper_sim",
        mnq_orb_breakout_proof_mode="paper_sim",
    )
    # default fixture payload -> orb_reclaim; close far above its bracket
    result = process_alert(
        _base_payload(timestamp="2026-05-23T15:00:00+00:00", close=19620.0, high=19625.0),
        config=cfg, log_dir=cfg.log_dir, for_date=today,
    )
    assert result["decision"] == "NO_TRADE"
    assert "ENTRY_DETACHED_FROM_PRICE" in (result.get("failed_gates") or [])
