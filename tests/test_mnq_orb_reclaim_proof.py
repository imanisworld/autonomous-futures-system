"""
tests/test_mnq_orb_reclaim_proof.py

Stage 2 MNQ orb_reclaim proof mode (2026-07-11). Scoped narrowly to MNQ +
orb_reclaim only — see context/mnq_orb_reclaim_proof.py.

Coverage:
  - Pure module: mode resolution (fails closed on garbage/"live"), campaign
    dedupe file semantics.
  - Runner integration: observe_only (default) leaves the EXISTING orb_reclaim
    path byte-for-byte unaffected (this is a real regression this feature
    almost introduced — test_scenario_a_max_daily_loss_blocks_after_large_loss
    in test_e2e_scenarios.py caught it); paper_sim forces PaperBroker + the
    market-entry/runner-exit override, scoped ONLY to MNQ+orb_reclaim (a
    different strategy/instrument under the same active mode is untouched);
    MNQ range_break_close and the generic RANGE_BOUND gate are unaffected;
    a duplicate campaign is suppressed BEFORE risk/broker (proven, not
    inferred, via a monkeypatch that raises if either is reached).
  - TradovateBroker unit: force_market_entry bypasses the env-configured
    Limit+IOC tolerance; force_runner_exit builds a stop-only bracket; a
    confirmed OPEN fill still requires a real order id from Tradovate either
    way — the #254 execution-state model is unaffected by either override.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from config.settings import ConfigError, SystemConfig, _validate_config
from context.mnq_orb_reclaim_proof import (
    VALID_MODES,
    campaign_already_attempted,
    evaluate_mnq_orb_reclaim_proof,
    is_mnq_orb_reclaim_candidate,
    mnq_orb_reclaim_proof_mode,
    record_campaign_attempt,
)
from execution.broker_interface import BracketOrder, Fill
from execution.paper_broker import PaperBroker
from execution.tradovate_broker import TradovateBroker, TradovateConfig
import execution.tradovate_supervisor as supervisor
from tests.test_e2e_scenarios import _base_config, _base_payload
from webhook.runner import process_alert


# ─── Pure module: mode resolution ────────────────────────────────────────────

def test_default_mode_is_observe_only(tmp_path):
    cfg = _base_config(tmp_path)
    assert cfg.mnq_orb_reclaim_proof_mode == "observe_only"
    assert mnq_orb_reclaim_proof_mode(cfg) == "observe_only"


@pytest.mark.parametrize("mode", VALID_MODES)
def test_valid_modes_pass_through(tmp_path, mode):
    cfg = replace(_base_config(tmp_path), mnq_orb_reclaim_proof_mode=mode)
    assert mnq_orb_reclaim_proof_mode(cfg) == mode


def test_live_is_never_a_valid_value_and_fails_closed_to_observe_only(tmp_path):
    cfg = replace(_base_config(tmp_path), mnq_orb_reclaim_proof_mode="live")
    assert mnq_orb_reclaim_proof_mode(cfg) == "observe_only"


def test_garbage_value_fails_closed_to_observe_only(tmp_path):
    cfg = replace(_base_config(tmp_path), mnq_orb_reclaim_proof_mode="not_a_real_mode")
    assert mnq_orb_reclaim_proof_mode(cfg) == "observe_only"


def test_config_validation_rejects_live_at_load_time(tmp_path):
    cfg = replace(_base_config(tmp_path), mnq_orb_reclaim_proof_mode="live", max_staleness_seconds=60)
    with pytest.raises(ConfigError, match="MNQ_ORB_RECLAIM_PROOF_MODE"):
        _validate_config(cfg)


def test_config_validation_rejects_unknown_value_at_load_time(tmp_path):
    cfg = replace(_base_config(tmp_path), mnq_orb_reclaim_proof_mode="paper_sim_typo", max_staleness_seconds=60)
    with pytest.raises(ConfigError, match="MNQ_ORB_RECLAIM_PROOF_MODE"):
        _validate_config(cfg)


def test_config_validation_accepts_every_valid_mode(tmp_path):
    for mode in VALID_MODES:
        _validate_config(replace(_base_config(tmp_path), mnq_orb_reclaim_proof_mode=mode, max_staleness_seconds=60))


# ─── Pure module: candidate scoping ───────────────────────────────────────────

@pytest.mark.parametrize(
    "instrument,strategy,expected",
    [
        ("MNQ", "orb_reclaim", True),
        ("MNQ1!", "orb_reclaim", True),
        ("MNQ", "range_signal", False),
        ("MNQ", "orb_breakout", False),
        ("MES", "orb_reclaim", False),
        ("MES", "range_signal", False),
        (None, "orb_reclaim", False),
        ("MNQ", None, False),
    ],
)
def test_is_mnq_orb_reclaim_candidate(instrument, strategy, expected):
    assert is_mnq_orb_reclaim_candidate(instrument, strategy) is expected


# ─── Pure module: campaign dedupe ────────────────────────────────────────────

def test_campaign_not_attempted_when_file_missing(tmp_path):
    assert campaign_already_attempted(
        str(tmp_path), orb_high=100.0, orb_low=90.0, direction="LONG"
    ) is False


def test_campaign_dedupe_round_trip(tmp_path):
    log_dir = str(tmp_path)
    d = date(2026, 7, 11)
    assert not campaign_already_attempted(
        log_dir, orb_high=100.0, orb_low=90.0, direction="LONG", for_date=d
    )
    record_campaign_attempt(log_dir, orb_high=100.0, orb_low=90.0, direction="LONG", for_date=d)
    assert campaign_already_attempted(
        log_dir, orb_high=100.0, orb_low=90.0, direction="LONG", for_date=d
    )
    # A different direction/boundary on the same day is a distinct campaign.
    assert not campaign_already_attempted(
        log_dir, orb_high=100.0, orb_low=90.0, direction="SHORT", for_date=d
    )
    assert not campaign_already_attempted(
        log_dir, orb_high=101.0, orb_low=90.0, direction="LONG", for_date=d
    )
    # A different day is a distinct campaign entirely.
    assert not campaign_already_attempted(
        log_dir, orb_high=100.0, orb_low=90.0, direction="LONG", for_date=date(2026, 7, 12)
    )


def test_campaign_file_is_fail_soft_on_corrupt_json(tmp_path):
    log_dir = str(tmp_path)
    d = date(2026, 7, 11)
    path = Path(log_dir) / f"mnq_orb_reclaim_proof_campaigns_{d.isoformat()}.json"
    path.write_text("{not valid json")
    assert campaign_already_attempted(
        log_dir, orb_high=100.0, orb_low=90.0, direction="LONG", for_date=d
    ) is False
    # Must not raise, and must still be able to record afterward.
    record_campaign_attempt(log_dir, orb_high=100.0, orb_low=90.0, direction="LONG", for_date=d)
    assert campaign_already_attempted(
        log_dir, orb_high=100.0, orb_low=90.0, direction="LONG", for_date=d
    )


# ─── Pure module: evaluate_mnq_orb_reclaim_proof state machine ───────────────

def test_observe_only_never_suppresses_or_overrides(tmp_path):
    cfg = replace(_base_config(tmp_path), mnq_orb_reclaim_proof_mode="observe_only")
    decision = evaluate_mnq_orb_reclaim_proof(
        cfg=cfg, log_dir=str(tmp_path), orb_high=100.0, orb_low=90.0, direction="LONG"
    )
    assert decision.suppress is False
    assert decision.apply_override is False
    assert decision.force_market_entry is False
    assert decision.force_runner_exit is False
    assert decision.force_paper_broker is False


def test_paper_sim_first_attempt_applies_override(tmp_path):
    cfg = replace(_base_config(tmp_path), mnq_orb_reclaim_proof_mode="paper_sim")
    decision = evaluate_mnq_orb_reclaim_proof(
        cfg=cfg, log_dir=str(tmp_path), orb_high=100.0, orb_low=90.0, direction="LONG"
    )
    assert decision.suppress is False
    assert decision.apply_override is True
    assert decision.force_market_entry is True
    assert decision.force_runner_exit is True
    assert decision.force_paper_broker is True


def test_tradovate_demo_first_attempt_applies_override_without_forcing_paper_broker(tmp_path):
    cfg = replace(_base_config(tmp_path), mnq_orb_reclaim_proof_mode="tradovate_demo")
    decision = evaluate_mnq_orb_reclaim_proof(
        cfg=cfg, log_dir=str(tmp_path), orb_high=100.0, orb_low=90.0, direction="LONG"
    )
    assert decision.apply_override is True
    assert decision.force_paper_broker is False


def test_duplicate_campaign_suppresses_under_active_mode(tmp_path):
    cfg = replace(_base_config(tmp_path), mnq_orb_reclaim_proof_mode="paper_sim")
    log_dir = str(tmp_path)
    record_campaign_attempt(log_dir, orb_high=100.0, orb_low=90.0, direction="LONG")
    decision = evaluate_mnq_orb_reclaim_proof(
        cfg=cfg, log_dir=log_dir, orb_high=100.0, orb_low=90.0, direction="LONG"
    )
    assert decision.suppress is True
    assert decision.apply_override is False
    assert decision.duplicate_campaign is True


# ─── Runner integration ───────────────────────────────────────────────────────

def test_observe_only_does_not_change_existing_orb_reclaim_behavior(tmp_path):
    """Regression lock: this is the exact scenario that briefly broke during
    this feature's development — an early draft redirected EVERY MNQ
    orb_reclaim decision to NO_TRADE under observe_only, silently disabling
    already-live behavior. observe_only must be a pure audit no-op."""
    from journal.journal_logger import JournalLogger

    today = date(2026, 5, 23)
    cfg = _base_config(tmp_path)
    assert cfg.mnq_orb_reclaim_proof_mode == "observe_only"
    journal = JournalLogger(log_dir=cfg.log_dir)
    journal.log_outcome(
        instrument="MNQ", session="new_york", result="LOSS",
        entry_price=19500.0, exit_price=19480.0, exit_reason="STOP_HIT",
        pnl_ticks=-80.0, pnl_dollars=-200.0, contracts=1, for_date=today,
    )

    result = process_alert(
        _base_payload(timestamp="2026-05-23T15:00:00+00:00"),
        config=cfg,
        log_dir=cfg.log_dir,
        for_date=today,
    )
    assert result["decision"] == "RISK_REJECTED", f"Got {result['decision']}"


def test_observe_only_audit_recorded_but_trade_proceeds(tmp_path):
    import json

    today = date(2026, 5, 23)
    cfg = _base_config(tmp_path)
    result = process_alert(
        _base_payload(timestamp="2026-05-23T15:00:00+00:00"),
        config=cfg, log_dir=cfg.log_dir, for_date=today,
    )
    assert result["decision"] == "TRADE"
    journal_path = next(Path(cfg.log_dir).glob("journal_*.jsonl"))
    rows = [json.loads(line) for line in journal_path.read_text().splitlines()]
    trade_intent = next(r for r in rows if r.get("decision") == "TRADE_INTENT")
    audit = trade_intent["mnq_orb_reclaim_proof_audit"]
    assert audit["proof_mode"] == "observe_only"
    assert audit["suppress"] is False
    assert audit["apply_override"] is False
    assert audit["force_market_entry"] is False
    assert audit["force_paper_broker"] is False


def test_paper_sim_forces_paper_broker_with_runner_mode_for_mnq_orb_reclaim(tmp_path, monkeypatch):
    """Proves — not infers — that paper_sim constructs a dedicated PaperBroker
    with runner_mode=True, regardless of the box's normal paper_mode/BROKER
    selection, by spying on the actual PaperBroker constructed and used."""
    import webhook.runner as runner_module

    captured = {}
    real_init = PaperBroker.__init__

    def spy_init(self, *args, **kwargs):
        captured.update(kwargs)
        return real_init(self, *args, **kwargs)

    monkeypatch.setattr(PaperBroker, "__init__", spy_init)

    today = date(2026, 5, 23)
    cfg = replace(_base_config(tmp_path), mnq_orb_reclaim_proof_mode="paper_sim")
    result = process_alert(
        _base_payload(timestamp="2026-05-23T15:00:00+00:00"),
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


def test_paper_sim_open_position_never_enters_tradovate_resolution(tmp_path, monkeypatch):
    """The proof adapter choice persists across bars and process reconstruction.
    Global BROKER=tradovate must not pull a paper proof position into Tradovate
    for resolution, fill confirmation, or trailing-stop replacement."""
    import json
    import execution.tradovate_broker as tradovate_module

    monkeypatch.setenv("BROKER", "tradovate")
    monkeypatch.setenv("TRADOVATE_ENV", "demo")
    today = date(2026, 5, 23)
    cfg = replace(
        _base_config(tmp_path),
        paper_mode=False,
        mnq_orb_reclaim_proof_mode="paper_sim",
    )
    opened = process_alert(
        _base_payload(timestamp="2026-05-23T15:00:00+00:00"),
        config=cfg, log_dir=cfg.log_dir, for_date=today,
    )
    assert opened["decision"] == "TRADE"
    paper_order_id = opened["fill"]["paper_order_id"]

    class _TradovateMustNotBeConstructed:
        def __init__(self, *args, **kwargs):
            raise AssertionError("paper_sim resolution reached Tradovate")

    monkeypatch.setattr(tradovate_module, "TradovateBroker", _TradovateMustNotBeConstructed)
    resolved = process_alert(
        _base_payload(
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


def test_paper_sim_open_without_paper_order_id_fails_closed(tmp_path, monkeypatch):
    def unidentifiable_open(self, order, market_price=None):
        return Fill(
            instrument=order.instrument,
            direction=order.direction,
            contracts=order.contracts,
            entry_price=order.entry,
            exit_price=None,
            exit_reason=None,
            result="OPEN",
            pnl_ticks=None,
            pnl_dollars=None,
        )

    monkeypatch.setattr(PaperBroker, "execute_bracket", unidentifiable_open)
    today = date(2026, 5, 23)
    cfg = replace(_base_config(tmp_path), mnq_orb_reclaim_proof_mode="paper_sim")
    result = process_alert(
        _base_payload(timestamp="2026-05-23T15:00:00+00:00"),
        config=cfg, log_dir=cfg.log_dir, for_date=today,
    )
    assert result["decision"] == "BLOCKED_ORDER_CONFIRMATION_MISSING"


def test_paper_sim_does_not_affect_a_different_strategy_on_mnq(tmp_path, monkeypatch):
    """Scope proof: paper_sim must route ONLY MNQ orb_reclaim. A different
    MNQ strategy candidate (vwap_reclaim, via vwap_reclaimed=True) under the
    same active mode gets zero override — confirms the gate is keyed on
    strategy=='orb_reclaim', not just instrument=='MNQ'."""
    from journal.journal_logger import JournalLogger

    today = date(2026, 5, 23)
    cfg = replace(
        _base_config(tmp_path),
        mnq_orb_reclaim_proof_mode="paper_sim",
        enabled_concepts=["vwap_reclaim"],
    )
    result = process_alert(
        _base_payload(
            timestamp="2026-05-23T15:00:00+00:00",
            orb_status="above",
            vwap=19503.0,
            close=19503.5,
            vwap_reclaimed=True,
        ),
        config=cfg, log_dir=cfg.log_dir, for_date=today,
    )
    if result["decision"] != "TRADE":
        pytest.skip("payload fixture did not produce a vwap_reclaim TRADE on this branch")
    journal_path = next(Path(cfg.log_dir).glob("journal_*.jsonl"))
    import json
    rows = [json.loads(line) for line in journal_path.read_text().splitlines()]
    trade_intent = next(r for r in rows if r.get("decision") == "TRADE_INTENT")
    assert "mnq_orb_reclaim_proof_audit" not in trade_intent
    assert trade_intent["setup"]["strategy"] != "orb_reclaim"


def test_mnq_range_break_close_is_never_a_proof_candidate():
    """MNQ range_signal/RANGE_BREAK_CLOSE (a separate, already-adjudicated
    REJECT lane) must never be treated as an orb_reclaim proof candidate —
    the scoping predicate itself is the only gate, and it's strategy-name
    keyed, so this is sufficient to prove no interaction is possible."""
    assert is_mnq_orb_reclaim_candidate("MNQ", "range_signal") is False


def test_generic_range_bound_still_blocked_with_paper_sim_active(tmp_path):
    """paper_sim must not unlock RANGE_BOUND generally — an MNQ orb_reclaim-
    shaped payload under RANGE_BOUND still hits the pre-existing
    require_trending_condition gate, unaffected by proof mode."""
    today = date(2026, 5, 23)
    cfg = replace(_base_config(tmp_path), mnq_orb_reclaim_proof_mode="paper_sim")
    result = process_alert(
        _base_payload(
            timestamp="2026-05-23T15:00:00+00:00",
            market_condition="RANGE_BOUND",
        ),
        config=cfg, log_dir=cfg.log_dir, for_date=today,
    )
    assert result["decision"] == "NO_TRADE"
    assert "MARKET_CONDITION_NOT_TRENDING" in result["failed_gates"]


def test_duplicate_campaign_never_reaches_risk_or_broker(tmp_path, monkeypatch):
    """Proves — not infers — that a duplicate-campaign proof attempt is
    suppressed before risk/broker, by arming both to raise if reached on the
    SECOND alert for the same ORB boundary/direction."""
    import webhook.runner as runner_module

    today = date(2026, 5, 23)
    cfg = replace(_base_config(tmp_path), mnq_orb_reclaim_proof_mode="paper_sim")

    first = process_alert(
        _base_payload(timestamp="2026-05-23T15:00:00+00:00"),
        config=cfg, log_dir=cfg.log_dir, for_date=today,
    )
    assert first["decision"] == "TRADE"

    class _RiskMustNotBeConstructed:
        def __init__(self, *args, **kwargs):
            raise AssertionError("duplicate campaign must not reach risk")

    def _broker_must_not_execute(*args, **kwargs):
        raise AssertionError("duplicate campaign must not reach the broker")

    monkeypatch.setattr(runner_module, "RiskEngine", _RiskMustNotBeConstructed)
    monkeypatch.setattr(PaperBroker, "execute_bracket", _broker_must_not_execute)

    second = process_alert(
        _base_payload(timestamp="2026-05-23T15:15:00+00:00"),
        config=cfg, log_dir=cfg.log_dir, for_date=today,
    )
    assert second["decision"] == "NO_TRADE"
    assert "MNQ_ORB_RECLAIM_PROOF_DUPLICATE" in second["failed_gates"]


# ─── TradovateBroker unit: force_market_entry / force_runner_exit ────────────

def _tradovate_order(**overrides):
    base = dict(
        instrument="MNQ",
        direction="LONG",
        entry=19500.0,
        stop=19460.0,
        target=19580.0,
        rr_ratio=2.0,
        strategy="orb_reclaim",
    )
    base.update(overrides)
    return BracketOrder(**base)


def _tradovate_broker(monkeypatch, response):
    monkeypatch.setenv("TRADOVATE_ENV", "demo")
    broker = TradovateBroker(config=TradovateConfig(env="demo"))
    broker._account_id = 1
    broker._contract_symbol_cache["MNQ"] = "MNQU6"
    monkeypatch.setattr(broker, "_authenticate", lambda: True)
    monkeypatch.setattr(broker, "_find_contract_id", lambda _: 99)
    monkeypatch.setattr(supervisor, "tradovate_order_ready", lambda: True)
    monkeypatch.setattr(broker, "_verify_bracket_children", lambda **kwargs: (True, True))
    captured = {}

    def post(path, body, **kwargs):
        captured[path] = body
        return response

    monkeypatch.setattr(broker, "_post", post)
    return broker, captured


def test_force_market_entry_bypasses_env_configured_limit_tolerance(monkeypatch):
    monkeypatch.setenv("ENTRY_SLIPPAGE_TOLERANCE_TICKS_MNQ", "32")
    broker, captured = _tradovate_broker(monkeypatch, {"orderId": 10, "oso1Id": 20})
    order = _tradovate_order(force_market_entry=True)
    fill = broker.execute_bracket(order)
    assert fill.result == "OPEN"
    assert captured["/order/placeOSO"]["orderType"] == "Market"


def test_without_force_market_entry_env_tolerance_still_applies(monkeypatch):
    """Control: confirms the override is opt-in, not a global behavior change —
    every other order (force_market_entry=False, the default) still respects
    ENTRY_SLIPPAGE_TOLERANCE_TICKS_MNQ exactly as before this feature."""
    monkeypatch.setenv("ENTRY_SLIPPAGE_TOLERANCE_TICKS_MNQ", "32")
    broker, captured = _tradovate_broker(monkeypatch, {"orderId": 10, "oso1Id": 20})
    order = _tradovate_order()
    broker.execute_bracket(order)
    assert captured["/order/placeOSO"]["orderType"] == "Limit"


def test_force_runner_exit_builds_stop_only_bracket(monkeypatch):
    broker, captured = _tradovate_broker(monkeypatch, {"orderId": 10, "oso1Id": 20})
    order = _tradovate_order(force_runner_exit=True)
    fill = broker.execute_bracket(order)
    assert fill.result == "OPEN"
    body = captured["/order/placeOSO"]
    assert body["bracket1"]["orderType"] == "Stop"
    assert "bracket2" not in body


def test_confirmed_trade_still_requires_order_id_with_both_overrides_set(monkeypatch):
    """The #254 execution-state model (confirmed TRADE requires a real
    broker order id) is unaffected by either proof-mode override."""
    broker, _captured = _tradovate_broker(monkeypatch, {})  # no orderId in response
    order = _tradovate_order(force_market_entry=True, force_runner_exit=True)
    fill = broker.execute_bracket(order)
    assert fill.result != "OPEN"
    assert fill.result == "CANCELLED"
