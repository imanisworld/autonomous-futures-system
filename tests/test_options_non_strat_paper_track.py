from __future__ import annotations

import pytest

import ast
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from alert_ranker.non_strat_coverage import NonStratEvent, OBSERVER_VERSION
from options_manager.config import OptionsManagerConfig
from options_manager.contract_quality import ContractMarketSnapshot
from options_manager.non_strat_paper_track import (
    TRACK_VERSION,
    WEBULL_BLOCK_REASON,
    NonStratPaperPlan,
    prepare_non_strat_paper_candidate,
    register_geometry_rule,
    simulate_non_strat_round_trip,
    unregister_geometry_rule,
)


@pytest.fixture(autouse=True)
def _throwaway_geometry_rule():
    """nst-v0.1 ships with NO registered rules; tests register one and remove it."""
    register_geometry_rule("PDH_RECLAIM_LONG:v1", "tests/throwaway (not a prereg)")
    try:
        yield
    finally:
        unregister_geometry_rule("PDH_RECLAIM_LONG:v1")


NOW = datetime.now(timezone.utc)


def _event(**overrides):
    values = dict(
        observer_id="OPTIONS_NON_STRAT_COVERAGE",
        observer_version=OBSERVER_VERSION,
        symbol="AAPL",
        timeframe="5m",
        session_date=NOW.date().isoformat(),
        bar_start=(NOW - timedelta(minutes=25)).isoformat(),
        bar_close=(NOW - timedelta(minutes=20)).isoformat(),
        family="PDH_RECLAIM_LONG",
        direction="LONG",
        level_name="PDH",
        level_value=99.5,
        trigger_price=100.0,
        vwap=99.7,
        ema20=99.0,
        volume_ratio=1.4,
        spy_trend="bullish",
        qqq_trend="bullish",
        market_aligned=True,
        earliest_sip_visibility=(NOW - timedelta(minutes=4)).isoformat(),
        source_rule="test",
        episode_id="episode-1",
    )
    values.update(overrides)
    return NonStratEvent(**values)


def _snapshot(**overrides):
    values = dict(
        ticker="AAPL",
        contract_symbol="AAPL_TEST_CALL",
        bid=1.00,
        ask=1.10,
        last=1.05,
        volume=500,
        open_interest=2000,
        implied_volatility=0.50,
        delta=0.50,
        theta=-0.03,
        underlying_price=100.0,
        quote_timestamp=NOW - timedelta(minutes=2),
        provider="polygon",
        is_snapshot_complete=True,
    )
    values.update(overrides)
    return ContractMarketSnapshot(**values)


def _plan(**overrides):
    values = dict(
        event=_event(),
        underlying_invalidation=98.0,
        underlying_target=104.0,
        geometry_rule_id="PDH_RECLAIM_LONG:v1",
        source_references=("docs/options-non-strat-coverage-observer.md#frozen-ns-v01-raw-families",),
        contract_strike=100.0,
        contract_expiry=date.today() + timedelta(days=45),
        entry_snapshot=_snapshot(),
        quantity=1,
    )
    values.update(overrides)
    return NonStratPaperPlan(**values)


def _config(**overrides):
    values = dict(
        risk_max_premium=3.0,
        risk_max_contracts=2,
        risk_max_total_premium_dollars=300.0,
        risk_min_dte_days=14,
        quality_max_spread_percent=20.0,
        quality_min_option_volume=100,
        quality_min_open_interest=500,
        quality_max_quote_age_seconds=900,
        broker_boundary_enabled=True,
        broker_boundary_max_contracts=2,
        broker_boundary_max_notional=300.0,
        broker_boundary_max_limit_price=3.0,
        live_options_trading_enabled=False,
    )
    values.update(overrides)
    return OptionsManagerConfig(**values)


def test_valid_candidate_enters_internal_track_but_webull_submit_stays_blocked():
    result = prepare_non_strat_paper_candidate(_plan(), _config())
    assert result.status == "INTERNAL_READY"
    assert result.track_version == TRACK_VERSION
    assert result.ticket_id and result.ticket_id.startswith("nst-")
    assert result.risk_result is not None and result.risk_result.status == "APPROVED"
    assert result.quality_result is not None and result.quality_result.status == "APPROVED"
    assert result.preview_result is not None and result.preview_result.preview_ready is True
    assert result.preview_request is not None
    assert result.preview_request.quantity == 1
    assert result.preview_request.executable is False
    assert result.preview_request.dry_run_only is True
    assert result.webull_submit_allowed is False
    assert result.webull_block_reason == WEBULL_BLOCK_REASON


def test_track_refuses_missing_or_inverted_trade_geometry():
    bad_long = _plan(underlying_invalidation=101.0)
    result = prepare_non_strat_paper_candidate(bad_long, _config())
    assert result.status == "REJECTED"
    assert result.reason == "long_geometry_requires_stop_below_entry_below_target"

    short_event = _event(
        family="PDL_RECLAIM_SHORT",
        direction="SHORT",
        trigger_price=100.0,
        level_name="PDL",
    )
    bad_short = _plan(
        event=short_event,
        underlying_invalidation=98.0,
        underlying_target=96.0,
    )
    result = prepare_non_strat_paper_candidate(bad_short, _config())
    assert result.status == "REJECTED"
    assert result.reason == "short_geometry_requires_target_below_entry_below_stop"


def test_track_is_one_contract_only_while_unproven():
    result = prepare_non_strat_paper_candidate(_plan(quantity=2), _config())
    assert result.status == "REJECTED"
    assert result.reason == "research_track_requires_exactly_one_contract"


def test_forward_paper_requires_geometry_provenance_and_decision_time_quote():
    missing_rule = prepare_non_strat_paper_candidate(
        _plan(geometry_rule_id=""),
        _config(),
    )
    assert missing_rule.status == "DATA_BLOCKED"
    assert missing_rule.reason == "geometry_rule_id_missing"

    # A rule id that nobody pre-registered is refused, whatever it says.
    unregistered = prepare_non_strat_paper_candidate(
        _plan(geometry_rule_id="LOOKS_OFFICIAL:v9"),
        _config(),
    )
    assert unregistered.status == "DATA_BLOCKED"
    assert unregistered.reason == "geometry_rule_not_registered"

    missing_refs = prepare_non_strat_paper_candidate(
        _plan(source_references=()),
        _config(),
    )
    assert missing_refs.status == "DATA_BLOCKED"
    assert missing_refs.reason == "geometry_source_references_missing"

    visibility = datetime.fromisoformat(
        _event().earliest_sip_visibility.replace("Z", "+00:00")
    )
    too_early = prepare_non_strat_paper_candidate(
        _plan(entry_snapshot=_snapshot(quote_timestamp=visibility - timedelta(seconds=1))),
        _config(),
    )
    assert too_early.status == "DATA_BLOCKED"
    assert too_early.reason == "decision_quote_precedes_event_visibility"

    too_late = prepare_non_strat_paper_candidate(
        _plan(entry_snapshot=_snapshot(quote_timestamp=visibility + timedelta(seconds=301))),
        _config(),
    )
    assert too_late.status == "DATA_BLOCKED"
    assert too_late.reason == "decision_quote_too_late_for_forward_paper"


def test_decision_time_underlying_price_controls_entry_and_late_geometry():
    decision_snapshot = _snapshot(underlying_price=101.0)
    ready = prepare_non_strat_paper_candidate(
        _plan(entry_snapshot=decision_snapshot),
        _config(),
    )
    assert ready.status == "INTERNAL_READY"
    assert ready.packet is not None
    assert ready.packet.entry_price == 101.0
    assert ready.packet.signa_bias == "NEUTRAL"
    assert ready.packet.signa_score == 0
    assert ready.packet.signa_grade == "C"
    assert ready.packet.max_premium == decision_snapshot.ask
    assert "PDH_RECLAIM_LONG:v1" in ready.packet.source

    late = prepare_non_strat_paper_candidate(
        _plan(entry_snapshot=_snapshot(underlying_price=102.0)),
        _config(),
    )
    assert late.status == "REJECTED"
    assert late.reason == "decision_price_remaining_rr_below_floor"


def test_missing_exact_contract_evidence_is_data_blocked():
    result = prepare_non_strat_paper_candidate(
        _plan(entry_snapshot=_snapshot(contract_symbol=None)),
        _config(),
    )
    assert result.status == "DATA_BLOCKED"
    assert result.reason == "contract_symbol_missing"


def test_existing_risk_and_contract_quality_gates_still_have_authority():
    # The risk gate runs first and owns the $3 premium cap; a $3.50 contract
    # dies there, before contract quality is ever consulted.
    too_expensive = _plan(entry_snapshot=_snapshot(bid=3.4, ask=3.5))
    result = prepare_non_strat_paper_candidate(too_expensive, _config())
    assert result.status == "REJECTED"
    assert result.reason.startswith("risk_gate:premium_cap")

    too_wide = _plan(entry_snapshot=_snapshot(bid=0.50, ask=1.50))
    result = prepare_non_strat_paper_candidate(too_wide, _config())
    assert result.status == "REJECTED"
    assert "spread_too_wide" in result.reason


def test_internal_round_trip_uses_exact_same_contract():
    prepared = prepare_non_strat_paper_candidate(_plan(), _config())
    exit_snapshot = _snapshot(bid=1.50, ask=1.60, last=1.55)
    result = simulate_non_strat_round_trip(prepared, exit_snapshot, _config())
    assert result.status == "SIMULATED"
    assert result.result is not None
    assert result.result.simulated_entry_price == 1.10
    assert result.result.simulated_exit_price == 1.50
    assert result.result.simulated_contracts == 1
    assert result.result.simulated_net_pnl == pytest.approx(40.0)

    wrong_contract = _snapshot(contract_symbol="AAPL_OTHER_CALL", bid=1.5, ask=1.6)
    rejected = simulate_non_strat_round_trip(prepared, wrong_contract, _config())
    assert rejected.status == "REJECTED"
    assert rejected.reason == "exit_snapshot_contract_mismatch"


def test_ticket_identity_is_deterministic_for_same_episode_and_contract():
    a = prepare_non_strat_paper_candidate(_plan(), _config())
    b = prepare_non_strat_paper_candidate(_plan(), _config())
    assert a.ticket_id == b.ticket_id


def test_module_has_no_webull_submit_or_execution_import():
    path = Path("options_manager/non_strat_paper_track.py")
    source = path.read_text()
    tree = ast.parse(source)
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
    assert not any("webull_sandbox_paper_orders" in module for module in modules)
    assert not any(module == "execution" or module.startswith("execution.") for module in modules)
    assert "submit_sandbox_paper_option_order" not in source

def test_nst_v0_1_ships_with_no_registered_geometry_rules():
    from options_manager import non_strat_paper_track as track

    unregister_geometry_rule("PDH_RECLAIM_LONG:v1")
    assert track.GEOMETRY_RULES == {}
    blocked = prepare_non_strat_paper_candidate(_plan(), _config())
    assert blocked.status == "DATA_BLOCKED"
    assert blocked.reason == "geometry_rule_not_registered"
