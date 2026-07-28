"""Tests for ops/system_status_snapshot.py.

Covers: deterministic schema generation, UNKNOWN when source data is missing,
source-of-truth conflict detection, atomic write behavior (including
preserving the last-known-good snapshot on a generation/validation failure),
no mutation of trading configuration, trade-chain accounting mismatch -> FAIL,
missing OUTCOME -> anomaly, stale feed -> anomaly, per-lane entry
model/tolerance preserved, and full-snapshot determinism.
"""

from __future__ import annotations

import json
import subprocess
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from ops.system_status_snapshot import (
    ALLOWED_CLASSIFICATIONS,
    CONTRACTS_BASIS_JOURNAL,
    CONTRACTS_BASIS_STATIC,
    REQUIRED_DECISION_MINUTES_BY_CONCEPT,
    UNKNOWN,
    _effective_entry_model,
    _required_decision_minutes,
    _runtime_state_for,
    active_env_gated_flags,
    build_env_gated_lanes,
    build_feed_liveness,
    build_runtime_lanes,
    build_strategy_evidence,
    build_system_status_snapshot,
    build_trade_chain_health,
    classify_no_trade_liveness,
    last_reopen,
    market_open,
    parse_strategy_inventory,
    permission_status_lookup,
    resolve_broker,
    resolve_effective_contracts,
    resolve_execution_mode_and_tolerance,
    validate_snapshot_schema,
    write_snapshot_atomic,
)

_INVENTORY_MD = """\
# STRATEGY INVENTORY
*Last updated: 2026-07-23*

## Master Table

| Strategy | Rules | Detector | Replay parity | Honest fills | Walk-forward | Slippage | Sample | Verdict |
|---|---|---|---|---|---|---|---|---|
| ORB Reclaim (MES) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ n=305 | **PAPER PROOF** |
| ORB Reclaim (MNQ) | ✅ | ✅ | Partial | ✅ | ❌ insufficient | ✅ | ⚠️ n=253 thin | **PROMISING BUT UNPROVEN** |
| PDH Reclaim | ✅ | ✅ | ✅ | ✅ | ❌ both halves neg | ❌ | ✅ n=67 | **RETIRE** |
| 4HR Re-Trigger (MES) | ✅ | ✅ | ✅ | ✅ | ❌ H2 erases H1 | ❌ | ⚠️ n=75 | **OVERFIT — excluded from runtime** |
| 12HR Miyagi | ✅ | ✅ | ✅ | n/a | n/a | n/a | ⚠️ n=8/10 | **BROKEN FOR CURRENT SYSTEM RISK CONSTRAINTS** |

## Detailed Strategy Profiles
### ORB Reclaim — MES
prose that must not be parsed as a table row -- heading intentionally does NOT
match its table row name "ORB Reclaim (MES)" (mirrors the real doc's older
em-dash profile-heading convention vs the table's parenthetical convention)

### ORB Reclaim (MNQ)
Forward evidence epoch: 2026-07-27T04:19:13Z
"""


# ── parse_strategy_inventory / classification mapping ───────────────────────

def test_parse_strategy_inventory_extracts_rows_and_last_updated():
    parsed = parse_strategy_inventory(_INVENTORY_MD)
    assert parsed["last_updated"] == "2026-07-23"
    assert parsed["rows"]["ORB Reclaim (MES)"] == "PAPER PROOF"
    assert parsed["rows"]["ORB Reclaim (MNQ)"] == "PROMISING BUT UNPROVEN"
    assert parsed["rows"]["PDH Reclaim"] == "RETIRE"
    assert "prose that must not be parsed as a table row" not in parsed["rows"]


def _find(evidence: list[dict], strategy: str, instrument: str | None = None) -> dict:
    for row in evidence:
        if row["strategy"] == strategy and (instrument is None or row["instrument"] == instrument):
            return row
    raise AssertionError(f"no evidence row for strategy={strategy!r} instrument={instrument!r}")


def test_build_strategy_evidence_maps_verdict_and_preserves_raw():
    evidence = build_strategy_evidence(
        _INVENTORY_MD,
        enabled_concepts=["orb_reclaim"],
        instruments=["MES", "MNQ"],
        disabled_concepts_per_instrument={},
    )
    row = _find(evidence, "orb_reclaim", "MES")
    assert row["classification"] == "PROMISING BUT UNPROVEN"
    assert row["classification_raw"] == "PAPER PROOF"
    assert row["classification"] in ALLOWED_CLASSIFICATIONS


def test_build_strategy_evidence_is_inventory_driven_independent_of_enabled_concepts():
    """Every Strategy_Inventory.md row must appear regardless of what's
    currently enabled -- the registry answers "what is tracked", not "what is
    live right now". enabled_concepts here deliberately names NONE of the
    inventory rows."""
    evidence = build_strategy_evidence(
        _INVENTORY_MD,
        enabled_concepts=[],
        instruments=[],
        disabled_concepts_per_instrument={},
    )
    row_names = {row["inventory_row_matched"] for row in evidence}
    assert {"ORB Reclaim (MES)", "ORB Reclaim (MNQ)", "PDH Reclaim", "4HR Re-Trigger (MES)"} <= row_names


def test_build_strategy_evidence_untracked_inventory_row_gets_eligible_none():
    """A row with no runtime-concept mapping at all (research-only, not yet
    wired -- e.g. Miyagi) is neither eligible=True nor eligible=False; it must
    be None, since "excluded" would wrongly imply a decision was made."""
    evidence = build_strategy_evidence(
        _INVENTORY_MD, enabled_concepts=[], instruments=[], disabled_concepts_per_instrument={},
    )
    row = _find(evidence, "12HR Miyagi")
    assert row["eligible"] is None
    assert row["classification"] == "BROKEN"
    assert row["instrument"] is None  # not encoded in this row's own name


def test_build_strategy_evidence_retired_row_normalizes_to_broken():
    evidence = build_strategy_evidence(
        _INVENTORY_MD, enabled_concepts=["pdh_reclaim"], instruments=["MES"], disabled_concepts_per_instrument={},
    )
    row = _find(evidence, "pdh_reclaim", "MES")
    assert row["classification"] == "BROKEN"  # RETIRE normalizes to BROKEN
    assert row["classification_raw"] == "RETIRE"


def test_build_strategy_evidence_shared_row_reports_both_instruments_independently():
    """PDH Reclaim is ONE inventory row shared by both pdh_reclaim/MES and
    pdh_reclaim/MNQ -- both must appear, with per-instrument eligibility, not
    collapse to a single row that silently drops one instrument."""
    evidence = build_strategy_evidence(
        _INVENTORY_MD,
        enabled_concepts=["pdh_reclaim"],
        instruments=["MES", "MNQ"],
        disabled_concepts_per_instrument={"MES": ["pdh_reclaim"]},
    )
    mes_row = _find(evidence, "pdh_reclaim", "MES")
    mnq_row = _find(evidence, "pdh_reclaim", "MNQ")
    assert mes_row["inventory_row_matched"] == mnq_row["inventory_row_matched"] == "PDH Reclaim"
    assert mes_row["eligible"] is False
    assert mes_row["exclusion_reason"] == "disabled_concepts_per_instrument"
    assert mnq_row["eligible"] is True
    assert mnq_row["exclusion_reason"] is None


def test_build_strategy_evidence_unknown_when_no_inventory_row_mapped():
    """orb_rejection has no Strategy_Inventory.md row -- must report UNKNOWN,
    never silently borrow a neighboring row's verdict."""
    evidence = build_strategy_evidence(
        _INVENTORY_MD,
        enabled_concepts=["orb_rejection"],
        instruments=["MES"],
        disabled_concepts_per_instrument={},
    )
    row = _find(evidence, "orb_rejection", "MES")
    assert row["classification"] == UNKNOWN
    assert row["classification_raw"] == UNKNOWN
    assert row["current_blocker"]


def test_build_strategy_evidence_unknown_when_inventory_missing_entirely():
    evidence = build_strategy_evidence(
        None,
        enabled_concepts=["orb_reclaim"],
        instruments=["MES"],
        disabled_concepts_per_instrument={},
    )
    row = _find(evidence, "orb_reclaim", "MES")
    assert row["classification"] == UNKNOWN


def test_build_strategy_evidence_disabled_per_instrument_stays_visible_but_ineligible():
    """A per-instrument exclusion (e.g. MES 4HR retrigger, OVERFIT) must remain
    a VISIBLE row with eligible=False, not disappear from the registry --
    disappearing is exactly the failure mode the operator flagged."""
    evidence = build_strategy_evidence(
        _INVENTORY_MD,
        enabled_concepts=["orb_reclaim"],
        instruments=["MES", "MNQ"],
        disabled_concepts_per_instrument={"MNQ": ["orb_reclaim"]},
    )
    mes_row = _find(evidence, "orb_reclaim", "MES")
    mnq_row = _find(evidence, "orb_reclaim", "MNQ")
    assert mes_row["eligible"] is True
    assert mes_row["exclusion_reason"] is None
    assert mnq_row["eligible"] is False
    assert mnq_row["exclusion_reason"] == "disabled_concepts_per_instrument"
    # still classified even though excluded -- exclusion must not blank the evidence
    assert mnq_row["classification"] == "PROMISING BUT UNPROVEN"


def test_build_strategy_evidence_excluded_overfit_strategy_stays_visible():
    evidence = build_strategy_evidence(
        _INVENTORY_MD,
        enabled_concepts=["strat_4hr_retrigger"],
        instruments=["MES"],
        disabled_concepts_per_instrument={"MES": ["strat_4hr_retrigger"]},
    )
    row = _find(evidence, "strat_4hr_retrigger", "MES")
    assert row["eligible"] is False
    assert row["classification"] == "OVERFIT"


def test_build_strategy_evidence_includes_active_env_gated_lane():
    evidence = build_strategy_evidence(
        _INVENTORY_MD,
        enabled_concepts=[],
        instruments=[],
        disabled_concepts_per_instrument={},
        env_gated_active=[("MNQ_ORB_BREAKOUT_INVERSE_MODE", "MNQ", "paper_sim")],
    )
    row = _find(evidence, "MNQ_ORB_BREAKOUT_INVERSE_MODE", "MNQ")
    assert row["eligible"] is True


def test_top_level_source_of_truth_conflict_is_not_evaluated_not_false():
    """Only ONE canonical classification source is read here
    (Strategy_Inventory.md) -- the top-level rollup must say NOT_EVALUATED,
    never a `False` that would falsely claim a second source was compared and
    found to agree."""
    from ops.system_status_snapshot import build_strategy_evidence as _bse
    evidence = _bse(_INVENTORY_MD, enabled_concepts=["orb_reclaim"], instruments=["MES"], disabled_concepts_per_instrument={})
    assert all(row["source_of_truth_conflict"] == "NOT_EVALUATED" for row in evidence)
    assert all(row["pending_reconciliation"] == "NOT_EVALUATED" for row in evidence)


# ── evidence-epoch extraction (must be the lane's own epoch, never the doc date) ─

def test_evidence_epoch_extracted_from_matching_prose_section():
    from ops.system_status_snapshot import _extract_evidence_epoch

    parsed = parse_strategy_inventory(_INVENTORY_MD)
    epoch = _extract_evidence_epoch(parsed["sections"], "ORB Reclaim (MNQ)")
    assert epoch == "2026-07-27T04:19:13Z"


def test_evidence_epoch_unknown_when_heading_does_not_match_table_row_name():
    """The doc's profile heading for this row uses a different naming
    convention than its table row -- must not fuzzy-match; must not fall back
    to the document's top-level 'Last updated' date either."""
    from ops.system_status_snapshot import _extract_evidence_epoch

    parsed = parse_strategy_inventory(_INVENTORY_MD)
    epoch = _extract_evidence_epoch(parsed["sections"], "ORB Reclaim (MES)")
    assert epoch is None


_ALWAYS_ELIGIBLE = lambda concept: "PAPER_ELIGIBLE"  # noqa: E731 -- test-local stand-in permission lookup


def _lanes(**overrides):
    kwargs = dict(
        enabled_concepts=["orb_reclaim"],
        instruments=["MES"],
        disabled_concepts_per_instrument={},
        broker="tradovate",
        execution_mode="legacy",
        entry_tolerance_ticks_by_root={"MES": 16.0},
        schedule_mode="current",
        contracts_lookup=lambda instrument: 1,
        contracts_basis=CONTRACTS_BASIS_STATIC,
        repo_commit="deadbeef",
        evidence_epoch_lookup=lambda concept, instrument: UNKNOWN,
        permission_status_lookup_fn=_ALWAYS_ELIGIBLE,
    )
    kwargs.update(overrides)
    return build_runtime_lanes(**kwargs)


def test_runtime_lane_evidence_epoch_is_unknown_not_the_document_last_updated_date():
    """Regression guard for the exact bug: evidence_epoch must never silently
    become Strategy_Inventory.md's document-level 'Last updated' date."""
    lanes = _lanes()
    assert lanes[0]["evidence_epoch"] == UNKNOWN
    assert lanes[0]["evidence_epoch"] != "2026-07-23"  # the doc's Last-updated date


def test_runtime_lane_evidence_epoch_uses_provided_lookup():
    lanes = _lanes(evidence_epoch_lookup=lambda concept, instrument: "2026-07-27T04:19:13Z")
    assert lanes[0]["evidence_epoch"] == "2026-07-27T04:19:13Z"


# ── per-lane execution context (not a global instrument default) ────────────

def test_runtime_lane_reports_shared_tradovate_broker_context():
    lanes = _lanes(
        enabled_concepts=["orb_breakout"], instruments=["MNQ"],
        entry_tolerance_ticks_by_root={"MNQ": 32.0},
    )
    assert lanes[0]["broker"] == "tradovate"
    assert lanes[0]["execution_mode"] == "legacy"
    assert lanes[0]["entry_tolerance_ticks"] == 32.0


def test_effective_entry_model_legacy_with_positive_tolerance_is_ioc_limit():
    """execution/tradovate_broker.py's real behavior: "legacy" with the box's
    actual positive tolerance builds a Limit-IOC entry, not a generic order."""
    lanes = _lanes(entry_tolerance_ticks_by_root={"MES": 16.0})
    assert lanes[0]["execution_mode"] == "legacy"
    assert lanes[0]["effective_entry_model"] == "ioc_limit"


def test_effective_entry_model_legacy_with_zero_tolerance_is_market():
    lanes = _lanes(entry_tolerance_ticks_by_root={"MES": 0.0})
    assert lanes[0]["effective_entry_model"] == "market"


def test_effective_entry_model_non_legacy_mode_passes_through():
    lanes = _lanes(execution_mode="marketable_limit", entry_tolerance_ticks_by_root={"MES": 8.0})
    assert lanes[0]["effective_entry_model"] == "marketable_limit"


def test_effective_entry_model_paperbroker_passes_through_unambiguously():
    """PaperBroker has no "legacy" mode at all -- its entry_fill_model values
    are already explicit, so the legacy-ambiguity resolution must not apply."""
    assert _effective_entry_model("legacy", 16.0, "paperbroker") == "legacy"
    assert _effective_entry_model("market", 0.0, "paperbroker") == "market"


# ── broker resolution (BLOCKER: must follow the real BROKER env path) ───────

def test_resolve_broker_defaults_to_paperbroker():
    """webhook/runner.py::_make_broker's real default is "paper" -> PaperBroker,
    not Tradovate -- the snapshot must never assume Tradovate."""
    assert resolve_broker({}) == "paperbroker"
    assert resolve_broker({"BROKER": "paper"}) == "paperbroker"


def test_resolve_broker_tradovate_only_on_exact_env_value():
    assert resolve_broker({"BROKER": "tradovate"}) == "tradovate"
    assert resolve_broker({"BROKER": "Tradovate"}) == "tradovate"  # case-insensitive, matches os.getenv(...).strip().lower()


def test_resolve_execution_mode_and_tolerance_tradovate_unset_tolerance_defaults_to_zero():
    """execution/tradovate_broker.py::_entry_slippage_tolerance_ticks's REAL
    unset-default is 0.0 (Market entry) -- not the PaperBroker-style 16/32
    fallback. Regression guard for the exact bug."""
    mode, tolerance = resolve_execution_mode_and_tolerance({}, ["MES", "MNQ"], broker="tradovate")
    assert mode == "legacy"
    assert tolerance == {"MES": 0.0, "MNQ": 0.0}


def test_resolve_execution_mode_and_tolerance_paperbroker_unset_tolerance_uses_box_defaults():
    """config/settings.py::_entry_tolerance_map's fallback (MES=16/MNQ=32)
    applies to the OFFLINE PaperBroker path, not Tradovate."""
    mode, tolerance = resolve_execution_mode_and_tolerance({}, ["MES", "MNQ"], broker="paperbroker")
    assert mode == "market"
    assert tolerance == {"MES": 16.0, "MNQ": 32.0}


def test_resolve_execution_mode_and_tolerance_respects_explicit_env_tolerance_either_broker():
    mode, tolerance = resolve_execution_mode_and_tolerance(
        {"ENTRY_SLIPPAGE_TOLERANCE_TICKS_MES": "4"}, ["MES"], broker="tradovate",
    )
    assert tolerance == {"MES": 4.0}


def test_resolve_execution_mode_and_tolerance_reads_correct_mode_env_per_broker():
    tradovate_mode, _ = resolve_execution_mode_and_tolerance(
        {"TRADOVATE_ENTRY_EXECUTION_MODE": "ioc_limit", "ENTRY_FILL_MODEL": "stop_market"}, ["MES"], broker="tradovate",
    )
    paperbroker_mode, _ = resolve_execution_mode_and_tolerance(
        {"TRADOVATE_ENTRY_EXECUTION_MODE": "ioc_limit", "ENTRY_FILL_MODEL": "stop_market"}, ["MES"], broker="paperbroker",
    )
    assert tradovate_mode == "ioc_limit"       # TRADOVATE_ENTRY_EXECUTION_MODE governs the Tradovate path
    assert paperbroker_mode == "stop_market"   # ENTRY_FILL_MODEL governs the PaperBroker path -- different env var entirely


def test_build_system_status_snapshot_broker_paper_env_reports_paperbroker_not_tradovate(tmp_path):
    """Required regression case: BROKER=paper (or unset) must never report
    "tradovate" for an ordinary risk_rules lane."""
    repo = _build_repo(tmp_path)
    snapshot = build_system_status_snapshot(repo_root=repo, log_dir="logs", env={"BROKER": "paper"})
    lane = next(lane for lane in snapshot["runtime_lanes"] if lane["gate_source"].startswith("risk_rules"))
    assert lane["broker"] == "paperbroker"
    assert lane["execution_mode"] == "market"  # ENTRY_FILL_MODEL default, not TRADOVATE_ENTRY_EXECUTION_MODE's "legacy"


def test_build_system_status_snapshot_broker_tradovate_env_reports_tradovate(tmp_path):
    repo = _build_repo(tmp_path)
    snapshot = build_system_status_snapshot(repo_root=repo, log_dir="logs", env={"BROKER": "tradovate"})
    lane = next(lane for lane in snapshot["runtime_lanes"] if lane["gate_source"].startswith("risk_rules"))
    assert lane["broker"] == "tradovate"
    assert lane["execution_mode"] == "legacy"


# ── contracts provenance ─────────────────────────────────────────────────────

def test_runtime_lane_reports_contracts_basis():
    lanes = _lanes(contracts_basis=CONTRACTS_BASIS_JOURNAL)
    assert lanes[0]["contracts_basis"] == CONTRACTS_BASIS_JOURNAL
    lanes = _lanes(contracts_basis=CONTRACTS_BASIS_STATIC)
    assert lanes[0]["contracts_basis"] == CONTRACTS_BASIS_STATIC


# ── strategy_permission_gate incorporated into runtime eligibility ──────────

def test_shadow_only_permission_status_yields_shadow_only_runtime_state():
    """A concept enabled in risk_rules.enabled_concepts (and not per-instrument
    excluded) but held at SHADOW_ONLY by strategy_permission_gate must NOT be
    reported paper_forward -- the real DecisionEngine never lets it place an
    order. This is the operator's exact vwap_hold/pdh_reclaim scenario."""
    lanes = _lanes(permission_status_lookup_fn=lambda concept: "SHADOW_ONLY")
    assert lanes[0]["permission_status"] == "SHADOW_ONLY"
    assert lanes[0]["runtime_state"] == "shadow_only"


def test_paper_eligible_permission_status_yields_paper_forward():
    lanes = _lanes(permission_status_lookup_fn=lambda concept: "PAPER_ELIGIBLE")
    assert lanes[0]["runtime_state"] == "paper_forward"


def test_permission_status_lookup_gate_disabled_behaves_as_pre_gate():
    status = permission_status_lookup(
        "vwap_hold", gate_enabled=False, strategy_status={"vwap_hold": "SHADOW_ONLY"}, default_status="SHADOW_ONLY",
    )
    assert status == "GATE_DISABLED"
    assert _runtime_state_for(status, "current") == "paper_forward"


def test_permission_status_lookup_uses_explicit_entry_over_default():
    status = permission_status_lookup(
        "vwap_hold", gate_enabled=True, strategy_status={"vwap_hold": "SHADOW_ONLY"}, default_status="PAPER_ELIGIBLE",
    )
    assert status == "SHADOW_ONLY"


def test_permission_status_lookup_falls_back_to_default_for_unlisted_concept():
    status = permission_status_lookup(
        "some_new_concept", gate_enabled=True, strategy_status={}, default_status="SHADOW_ONLY",
    )
    assert status == "SHADOW_ONLY"


# ── effective contract count (sourced, never a silent default) ──────────────

def test_resolve_effective_contracts_unknown_when_dynamic_sizing_and_no_balance():
    result = resolve_effective_contracts(
        "MES", position_sizing_enabled=True, sizing_rules=[], current_balance=None,
        max_contracts_per_instrument={}, max_contracts_hard_cap=None,
    )
    assert result == UNKNOWN


def test_resolve_effective_contracts_resolves_tier_from_balance():
    sizing_rules = [
        {"min_balance": 0, "max_balance": 2000, "instrument": "MES", "max_contracts": 1},
        {"min_balance": 2000, "max_balance": 4000, "instrument": "MES", "max_contracts": 2},
        {"min_balance": 4000, "max_balance": None, "instrument": "MES", "max_contracts": 3},
    ]
    assert resolve_effective_contracts(
        "MES", position_sizing_enabled=True, sizing_rules=sizing_rules, current_balance=2500,
        max_contracts_per_instrument={}, max_contracts_hard_cap=None,
    ) == 2
    assert resolve_effective_contracts(
        "MES", position_sizing_enabled=True, sizing_rules=sizing_rules, current_balance=50000,
        max_contracts_per_instrument={}, max_contracts_hard_cap=None,
    ) == 3


def test_resolve_effective_contracts_capped_by_hard_cap():
    sizing_rules = [{"min_balance": 0, "max_balance": None, "instrument": "MNQ", "max_contracts": 6}]
    result = resolve_effective_contracts(
        "MNQ", position_sizing_enabled=True, sizing_rules=sizing_rules, current_balance=50000,
        max_contracts_per_instrument={}, max_contracts_hard_cap=2,
    )
    assert result == 2


def test_resolve_effective_contracts_static_when_sizing_disabled():
    result = resolve_effective_contracts(
        "MES", position_sizing_enabled=False, sizing_rules=[], current_balance=None,
        max_contracts_per_instrument={"MES": 1}, max_contracts_hard_cap=None,
    )
    assert result == 1


def test_resolve_effective_contracts_unknown_when_static_and_no_cap_configured():
    """Regression guard for the exact bug: never silently default to 1."""
    result = resolve_effective_contracts(
        "MES", position_sizing_enabled=False, sizing_rules=[], current_balance=None,
        max_contracts_per_instrument={}, max_contracts_hard_cap=None,
    )
    assert result == UNKNOWN


def test_active_env_gated_flags_excludes_observe_only():
    """observe_only is the proof-mode contract's own audit-only value -- it
    must never be reported as an active trading lane."""
    flags = active_env_gated_flags({"MNQ_ORB_BREAKOUT_INVERSE_MODE": "observe_only"})
    assert flags == []


def test_active_env_gated_flags_includes_paper_sim():
    flags = active_env_gated_flags({"MNQ_ORB_BREAKOUT_INVERSE_MODE": "paper_sim"})
    assert flags == [("MNQ_ORB_BREAKOUT_INVERSE_MODE", "MNQ", "paper_sim")]


def test_env_gated_lane_resolves_verified_execution_context_distinct_from_tradovate_path():
    """The exact scenario the operator described: the frozen MNQ inverse ORB
    lane must report ITS OWN PaperBroker/8-tick/1-contract context, not the
    ordinary Tradovate legacy/32-tick lane's values."""
    lanes = build_env_gated_lanes({"MNQ_ORB_BREAKOUT_INVERSE_MODE": "paper_sim"})
    assert len(lanes) == 1
    lane = lanes[0]
    assert lane["broker"] == "PaperBroker"
    assert lane["effective_entry_model"] == "marketable_ioc"
    assert lane["entry_tolerance_ticks"] == 8.0
    assert lane["contracts"] == 1


def test_env_gated_lane_without_verified_source_stays_unknown_not_borrowed():
    """A proof-mode lane this module has NOT verified against its own source
    must report UNKNOWN, never silently inherit the global Tradovate config."""
    lanes = build_env_gated_lanes({"MNQ_ORB_RECLAIM_PROOF_MODE": "paper_sim"})
    assert len(lanes) == 1
    lane = lanes[0]
    assert lane["effective_entry_model"] == UNKNOWN
    assert lane["entry_tolerance_ticks"] == UNKNOWN
    assert lane["contracts"] == UNKNOWN


# ── trade chain accounting ───────────────────────────────────────────────────

def _approved_trade(instrument: str, ts: str) -> dict:
    return {"ts": ts, "decision": "TRADE", "instrument": instrument, "risk_check": {"result": "APPROVED"}, "outcome": None}


def _outcome(instrument: str, ts: str, result: str, *, no_fill_reason: str | None = None) -> dict:
    return {"ts": ts, "type": "OUTCOME", "instrument": instrument, "outcome": {"result": result, "no_fill_reason": no_fill_reason}}


def test_trade_chain_accounting_win_reconciles_pass():
    entries = [
        _approved_trade("MES", "2026-07-27T14:00:00+00:00"),
        _outcome("MES", "2026-07-27T14:05:00+00:00", "WIN"),
    ]
    result = build_trade_chain_health(entries, instruments=["MES"], broker_open_positions={"MES": False})
    counts = result["counts"]
    assert counts["attempts"] == 1
    assert counts["fills"] == 1
    assert counts["resolved"] == 1
    assert result["accounting"]["attempts_equation_holds"] is True
    assert result["accounting"]["fills_equation_holds"] is True


def test_trade_chain_known_no_fill_and_broker_reject_bucketed_separately():
    entries = [
        _approved_trade("MES", "2026-07-27T14:00:00+00:00"),
        _outcome("MES", "2026-07-27T14:01:00+00:00", "CANCELLED", no_fill_reason="NO_FILL_PRICE_MOVED_AWAY"),
        _approved_trade("MNQ", "2026-07-27T14:10:00+00:00"),
        _outcome("MNQ", "2026-07-27T14:11:00+00:00", "CANCELLED", no_fill_reason="NO_FILL_BROKER_REJECTED"),
    ]
    result = build_trade_chain_health(entries, instruments=["MES", "MNQ"], broker_open_positions={"MES": False, "MNQ": False})
    counts = result["counts"]
    assert counts["known_no_fills"] == 1
    assert counts["rejects"] == 1
    assert counts["attempts"] == 2
    assert result["accounting"]["attempts_equation_holds"] is True


def test_trade_chain_orphan_open_position_fails_broker_journal_parity():
    """Journal shows an open position the broker denies holding -> FAIL, not PASS."""
    entries = [_approved_trade("MES", "2026-07-27T14:00:00+00:00")]
    result = build_trade_chain_health(entries, instruments=["MES"], broker_open_positions={"MES": False})
    assert result["counts"]["orphan_count"] == 1
    assert result["broker_journal_parity"] == "FAIL"
    assert result["overall_state"] == "FAIL"
    assert "orphan" in (result["last_anomaly_summary"] or "")


def test_trade_chain_missing_broker_read_is_missing_outcome_not_orphan():
    """No broker read available (None) must not be misclassified as an orphan."""
    entries = [_approved_trade("MES", "2026-07-27T14:00:00+00:00")]
    result = build_trade_chain_health(entries, instruments=["MES"], broker_open_positions={})
    assert result["counts"]["orphan_count"] == 0
    assert result["counts"]["missing_outcome_count"] == 1


def test_trade_chain_duplicate_order_id_flagged():
    entries = [
        {"ts": "t1", "type": "ORDER_IDS", "instrument": "MES", "order_ids": {"stop": "abc123"}},
        {"ts": "t2", "type": "ORDER_IDS", "instrument": "MES", "order_ids": {"stop": "abc123"}},
    ]
    result = build_trade_chain_health(entries, instruments=["MES"])
    assert result["counts"]["duplicate_order_identity_count"] == 1
    assert result["overall_state"] == "FAIL"


def test_trade_chain_zero_activity_alone_does_not_imply_pass():
    """The exact failure mode flagged by the operator: zero attempts/fills/orphans
    must NOT read as a healthy PASS when liveness shows the system never ran."""
    stale_feed = {"5m": {"last_bar_ts": None, "stale": True}, "15m": {"last_bar_ts": None, "stale": True}}
    result = build_trade_chain_health(
        [], instruments=["MES"], feed_liveness_by_instrument={"MES": stale_feed},
    )
    assert result["counts"]["attempts"] == 0
    assert result["liveness"]["MES"]["diagnosis"] == "NO_TRADE_SYSTEM_FAILURE"
    assert result["overall_state"] != "PASS"


# ── no-trade liveness classification ─────────────────────────────────────────

_HEALTHY_FEED = {"5m": {"last_bar_ts": "t", "stale": False}, "15m": {"last_bar_ts": "t", "stale": False}}


def test_no_trade_healthy_when_legitimate_reason_and_feed_current():
    entries = [{"instrument": "MNQ", "decision": "NO_TRADE", "reason": "no valid setup"}]
    result = classify_no_trade_liveness(instrument="MNQ", entries=entries, feed_liveness=_HEALTHY_FEED)
    assert result["diagnosis"] == "NO_TRADE_HEALTHY"
    assert result["reason_legitimate"] is True


def test_no_trade_system_failure_when_reason_is_a_system_fault():
    entries = [{"instrument": "MNQ", "decision": "NO_TRADE", "reason": "detector_exception: KeyError"}]
    result = classify_no_trade_liveness(instrument="MNQ", entries=entries, feed_liveness=_HEALTHY_FEED)
    assert result["diagnosis"] == "NO_TRADE_SYSTEM_FAILURE"
    assert result["reason_legitimate"] is False


def test_no_trade_system_failure_when_feed_stale_even_with_legitimate_reason():
    stale_feed = {"5m": {"last_bar_ts": None, "stale": True}}
    entries = [{"instrument": "MNQ", "decision": "NO_TRADE", "reason": "no valid setup"}]
    result = classify_no_trade_liveness(instrument="MNQ", entries=entries, feed_liveness=stale_feed)
    assert result["diagnosis"] == "NO_TRADE_SYSTEM_FAILURE"


def test_no_trade_system_failure_when_strategy_never_evaluated():
    result = classify_no_trade_liveness(instrument="MNQ", entries=[], feed_liveness=_HEALTHY_FEED)
    assert result["diagnosis"] == "NO_TRADE_SYSTEM_FAILURE"
    assert result["strategy_evaluated"] is False


def test_traded_diagnosis_when_a_trade_decision_exists():
    entries = [{"instrument": "MNQ", "decision": "TRADE", "reason": "confluence A"}]
    result = classify_no_trade_liveness(instrument="MNQ", entries=entries, feed_liveness=_HEALTHY_FEED)
    assert result["diagnosis"] == "TRADED"


# ── market-closed must not read as a system failure (BLOCKER) ───────────────

_MARKET_CLOSED_FEED = {"5m": {"last_bar_ts": None, "stale": False, "market_open": False}}
_MARKET_OPEN_FEED = {"5m": {"last_bar_ts": "t", "stale": False, "market_open": True}}


def test_market_closed_zero_decisions_is_not_system_failure():
    """Required regression case: Saturday + zero decisions must NOT report
    NO_TRADE_SYSTEM_FAILURE -- build_feed_liveness already proved the feed
    isn't stale on a closed market; this proves the DIAGNOSIS layer honors
    that instead of independently re-deciding "no evaluation = fault"."""
    result = classify_no_trade_liveness(instrument="MNQ", entries=[], feed_liveness=_MARKET_CLOSED_FEED)
    assert result["diagnosis"] == "MARKET_CLOSED"
    assert result["diagnosis"] != "NO_TRADE_SYSTEM_FAILURE"
    assert result["expected_evaluation"] is False


def test_market_open_zero_decisions_is_still_system_failure():
    """The market-closed carve-out must not swallow a REAL failure -- zero
    decisions while the market is open (per feed_liveness) stays a fault."""
    result = classify_no_trade_liveness(instrument="MNQ", entries=[], feed_liveness=_MARKET_OPEN_FEED)
    assert result["diagnosis"] == "NO_TRADE_SYSTEM_FAILURE"
    assert result["expected_evaluation"] is True


def test_market_open_unknown_defaults_to_expecting_evaluation():
    """When feed_liveness carries no market_open signal at all (e.g. an older
    caller), absence of proof of closure must not manufacture a MARKET_CLOSED
    verdict -- stay conservative and keep the existing failure behavior."""
    result = classify_no_trade_liveness(instrument="MNQ", entries=[], feed_liveness=_HEALTHY_FEED)
    assert result["market_open"] is None
    assert result["diagnosis"] == "NO_TRADE_SYSTEM_FAILURE"


class _FakeBarHistory:
    """Mirrors context.bar_history.BarHistory's real read interface -- a flat,
    mixed-timeframe bar list per instrument/day, exactly like the real
    per-instrument-per-day JSONL file. Callers must filter by each bar's own
    `timeframe` field; nothing here pre-sorts by timeframe for them."""

    def __init__(self, bars: list[dict]):
        self._bars = bars

    def recent(self, instrument, n, for_date=None, lookback_days=3):
        return self._bars[-n:]


def test_feed_liveness_stale_when_bar_too_old():
    bars = [{"ts": "2026-07-27T00:00:00+00:00", "timeframe": "5"}]
    liveness = build_feed_liveness(
        _FakeBarHistory(bars), "MNQ", for_date=date(2026, 7, 27),
        now=datetime(2026, 7, 27, 5, 0, tzinfo=timezone.utc), required_minutes=(5,),
    )
    assert liveness["5m"]["stale"] is True
    assert liveness["5m"]["staleness_minutes"] == pytest.approx(300.0)


def test_feed_liveness_healthy_when_bar_recent():
    bars = [{"ts": "2026-07-27T04:58:00+00:00", "timeframe": "5"}]
    liveness = build_feed_liveness(
        _FakeBarHistory(bars), "MNQ", for_date=date(2026, 7, 27),
        now=datetime(2026, 7, 27, 5, 0, tzinfo=timezone.utc), required_minutes=(5,),
    )
    assert liveness["5m"]["stale"] is False
    assert liveness["5m"]["bars_seen_this_timeframe"] == 1


def test_feed_liveness_missing_bar_is_stale_not_a_crash():
    liveness = build_feed_liveness(
        _FakeBarHistory([]), "MNQ", for_date=date(2026, 7, 27),
        now=datetime(2026, 7, 27, 5, 0, tzinfo=timezone.utc), required_minutes=(5,),
    )
    assert liveness["5m"]["stale"] is True
    assert liveness["5m"]["last_bar_ts"] is None


def test_feed_liveness_bar_with_no_timeframe_field_is_dropped_not_guessed():
    """A bar with an unparseable/missing `timeframe` must not be silently
    attributed to any bucket -- that would let an untagged bar fill a gap it
    cannot actually prove is filled."""
    bars = [{"ts": "2026-07-27T04:58:00+00:00", "timeframe": None}]
    liveness = build_feed_liveness(
        _FakeBarHistory(bars), "MNQ", for_date=date(2026, 7, 27),
        now=datetime(2026, 7, 27, 5, 0, tzinfo=timezone.utc), required_minutes=(5,),
    )
    assert liveness["5m"]["bars_seen_this_timeframe"] == 0
    assert liveness["5m"]["stale"] is True


def test_feed_liveness_fresh_15m_bar_cannot_mask_stale_5m_feed():
    """The exact bug: a fresh 15m bar must not make the 5m check look
    current, and vice versa -- each timeframe is judged only by bars actually
    labeled that timeframe."""
    bars = [
        {"ts": "2026-07-27T00:00:00+00:00", "timeframe": "5"},  # 5m: 5 hours stale
        {"ts": "2026-07-27T04:59:00+00:00", "timeframe": "15"},  # 15m: fresh
    ]
    liveness = build_feed_liveness(
        _FakeBarHistory(bars), "MNQ", for_date=date(2026, 7, 27),
        now=datetime(2026, 7, 27, 5, 0, tzinfo=timezone.utc), required_minutes=(5, 15),
    )
    assert liveness["5m"]["stale"] is True
    assert liveness["15m"]["stale"] is False


# ── required-timeframe resolution (BLOCKER: strat_322_first_live is 5m-native) ─

def test_strat_322_first_live_requires_5m_per_risk_rules_yaml_comment():
    """risk_rules.yaml documents strat_322_first_live with the SAME "Canonical
    5m-native ... 5-minute entry-recovery fidelity" wording as
    strat_4hr_retrigger -- both must require 5m, not the 15m system default."""
    assert REQUIRED_DECISION_MINUTES_BY_CONCEPT["strat_322_first_live"] == (5,)
    assert _required_decision_minutes({"strat_322_first_live"}, 15) == (5,)


def test_strat_322_first_live_stale_5m_fresh_15m_reports_unhealthy():
    """Required regression case: an instrument running strat_322_first_live
    (5m-native) must be judged unhealthy off its OWN required 5m feed even
    when the system-default 15m feed is perfectly fresh -- proving the
    required-timeframe fix actually changes the no-trade diagnosis, not just
    the raw liveness dict."""
    bars = [
        {"ts": "2026-07-27T00:00:00+00:00", "timeframe": "5"},   # 5m: stale
        {"ts": "2026-07-27T04:59:00+00:00", "timeframe": "15"},  # 15m: fresh
    ]
    now = datetime(2026, 7, 27, 5, 0, tzinfo=timezone.utc)
    required = _required_decision_minutes({"strat_322_first_live"}, 15)
    liveness = build_feed_liveness(_FakeBarHistory(bars), "MNQ", for_date=date(2026, 7, 27), now=now, required_minutes=required)

    result = classify_no_trade_liveness(instrument="MNQ", entries=[], feed_liveness=liveness)
    assert "15m" not in liveness  # only the concept's own required timeframe(s) are checked
    assert liveness["5m"]["stale"] is True
    assert result["diagnosis"] == "NO_TRADE_SYSTEM_FAILURE"


def test_feed_liveness_real_bar_history_fresh_15m_cannot_mask_stale_5m(tmp_path):
    """Same proof as above, but against the ACTUAL context.bar_history.BarHistory
    class and its real on-disk mixed-timeframe file -- not a hand-rolled fake
    reproducing the same (buggy, pre-fix) interface the unit test's fake used."""
    from context.bar_history import BarHistory

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    history = BarHistory(log_dir=str(log_dir))
    for_date = date(2026, 7, 27)

    history.record(
        "MNQ", ts="2026-07-27T00:00:00+00:00", open=1, high=1, low=1, close=1,
        timeframe="5", for_date=for_date,
    )
    history.record(
        "MNQ", ts="2026-07-27T04:59:00+00:00", open=1, high=1, low=1, close=1,
        timeframe="15", for_date=for_date,
    )

    liveness = build_feed_liveness(
        history, "MNQ", for_date=for_date,
        now=datetime(2026, 7, 27, 5, 0, tzinfo=timezone.utc), required_minutes=(5, 15),
    )
    assert liveness["5m"]["stale"] is True
    assert liveness["5m"]["last_bar_ts"] == "2026-07-27T00:00:00+00:00"
    assert liveness["15m"]["stale"] is False
    assert liveness["15m"]["last_bar_ts"] == "2026-07-27T04:59:00+00:00"


# ── market-hours awareness (reuses ops.feed_gap_alarm, not a second calendar) ─

def test_feed_liveness_market_closed_never_reports_stale():
    """The exact scenario: 0 trades + a very old/missing bar during a CME
    Globex closure (Saturday here) must NOT read as a system failure."""
    assert market_open(datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)) is False  # Saturday
    liveness = build_feed_liveness(
        _FakeBarHistory([]), "MNQ", for_date=date(2026, 8, 1),
        now=datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc), required_minutes=(5, 15),
    )
    assert liveness["5m"]["stale"] is False
    assert liveness["15m"]["stale"] is False
    assert liveness["5m"]["market_open"] is False


def test_feed_liveness_market_open_reports_market_open_true():
    bars = [{"ts": "2026-07-27T04:58:00+00:00", "timeframe": "5"}]
    liveness = build_feed_liveness(
        _FakeBarHistory(bars), "MNQ", for_date=date(2026, 7, 27),
        now=datetime(2026, 7, 27, 5, 0, tzinfo=timezone.utc), required_minutes=(5,),
    )
    assert liveness["5m"]["market_open"] is True
    assert liveness["5m"]["stale"] is False


def test_feed_liveness_reopen_grace_no_false_alarm_right_after_reopen():
    """No bars yet, but `now` is shortly after the CME reopen (22:00Z Sunday)
    -- baseline is max(last_bar, last_reopen), so this must not be stale."""
    reopen_time = last_reopen(datetime(2026, 7, 27, 0, 0, tzinfo=timezone.utc))
    just_after_reopen = reopen_time + timedelta(minutes=2)
    liveness = build_feed_liveness(
        _FakeBarHistory([]), "MNQ", for_date=date(2026, 7, 26),
        now=just_after_reopen, required_minutes=(5,),
    )
    assert liveness["5m"]["stale"] is False


# ── atomic write / schema validation ────────────────────────────────────────

def _minimal_valid_snapshot() -> dict:
    return {
        "schema_version": "1.0.0", "generated_at": "t", "generator": {}, "repo": {},
        "deployed_sha": UNKNOWN, "runtime_drift": {}, "data_freshness": {}, "runtime_lanes": [],
        "strategy_evidence": [], "trade_chain": {}, "repo_health": {}, "blockers": [],
        "source_of_truth_conflict": False, "unknown_fields": [],
    }


def test_validate_snapshot_schema_accepts_minimal_valid_snapshot():
    assert validate_snapshot_schema(_minimal_valid_snapshot()) == []


def test_validate_snapshot_schema_rejects_missing_key():
    broken = _minimal_valid_snapshot()
    del broken["blockers"]
    errors = validate_snapshot_schema(broken)
    assert any("blockers" in e for e in errors)


def test_write_snapshot_atomic_writes_valid_json(tmp_path):
    target = tmp_path / "snapshot.json"
    write_snapshot_atomic(target, _minimal_valid_snapshot())
    assert json.loads(target.read_text())["schema_version"] == "1.0.0"


def test_write_snapshot_atomic_preserves_last_known_good_on_invalid_write(tmp_path):
    target = tmp_path / "snapshot.json"
    good = _minimal_valid_snapshot()
    write_snapshot_atomic(target, good)
    before = target.read_text()

    broken = dict(good)
    del broken["trade_chain"]
    with pytest.raises(ValueError):
        write_snapshot_atomic(target, broken)

    assert target.read_text() == before  # untouched, not blanked or partially written


def test_write_snapshot_atomic_never_leaves_a_temp_file_behind(tmp_path):
    target = tmp_path / "snapshot.json"
    write_snapshot_atomic(target, _minimal_valid_snapshot())
    leftovers = list(tmp_path.glob("*.tmp"))
    assert leftovers == []


# ── full-snapshot integration ────────────────────────────────────────────────

def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _build_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    (repo / "risk_rules.yaml").write_text(
        """
trading_mode:
  live_trading_enabled: false
  paper_mode: true
instruments:
  allowed: [MES, MNQ]
schedule:
  mode: current
strategy:
  enabled_concepts: [orb_reclaim]
  disabled_concepts_per_instrument: {}
""".lstrip(),
        encoding="utf-8",
    )
    inventory_dir = repo / "docs" / "strategy-rules"
    inventory_dir.mkdir(parents=True)
    (inventory_dir / "Strategy_Inventory.md").write_text(_INVENTORY_MD, encoding="utf-8")
    (repo / ".gitignore").write_text("logs/\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "risk_rules.yaml", "docs/strategy-rules/Strategy_Inventory.md", ".gitignore"],
        cwd=repo, check=True, stdout=subprocess.DEVNULL,
    )
    _git(repo, "commit", "-m", "init")
    (repo / "logs").mkdir()
    return repo


def test_build_system_status_snapshot_is_deterministic_and_schema_valid(tmp_path):
    repo = _build_repo(tmp_path)
    when = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)

    first = build_system_status_snapshot(
        repo_root=repo, log_dir="logs", for_date=date(2026, 7, 27), generated_at=when, env={},
    )
    second = build_system_status_snapshot(
        repo_root=repo, log_dir="logs", for_date=date(2026, 7, 27), generated_at=when, env={},
    )
    assert first == second
    assert validate_snapshot_schema(first) == []


def test_build_system_status_snapshot_marks_unknown_without_release_manifest(tmp_path):
    repo = _build_repo(tmp_path)
    snapshot = build_system_status_snapshot(repo_root=repo, log_dir="logs", env={})
    assert snapshot["deployed_sha"] == UNKNOWN
    assert "deployed_sha" in snapshot["unknown_fields"]


def test_build_system_status_snapshot_never_writes_to_risk_rules(tmp_path):
    repo = _build_repo(tmp_path)
    before = (repo / "risk_rules.yaml").read_text()
    build_system_status_snapshot(repo_root=repo, log_dir="logs", env={})
    after = (repo / "risk_rules.yaml").read_text()
    assert before == after


def test_build_system_status_snapshot_lane_preserves_per_instrument_entry_tolerance(tmp_path):
    repo = _build_repo(tmp_path)
    env = {"ENTRY_SLIPPAGE_TOLERANCE_TICKS_MES": "16", "ENTRY_SLIPPAGE_TOLERANCE_TICKS_MNQ": "32"}
    snapshot = build_system_status_snapshot(repo_root=repo, log_dir="logs", env=env)
    lanes_by_instrument = {
        lane["instrument"]: lane for lane in snapshot["runtime_lanes"] if lane["strategy"] == "orb_reclaim"
    }
    assert lanes_by_instrument["MES"]["entry_tolerance_ticks"] == 16.0
    assert lanes_by_instrument["MNQ"]["entry_tolerance_ticks"] == 32.0
