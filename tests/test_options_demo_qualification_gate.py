from __future__ import annotations

import hashlib
import json
from pathlib import Path

import options_manager.validation.demo_qualification as gate


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _evidence(tmp_path: Path) -> dict:
    underlying = tmp_path / "underlying_manifest.json"
    quotes = tmp_path / "quotes_manifest.json"
    policy = tmp_path / "options_policy.json"
    selector = tmp_path / "selector_rule.json"
    underlying.write_text('{"frozen":true}\n', encoding="utf-8")
    quotes.write_text('{"frozen":true}\n', encoding="utf-8")
    policy.write_text('{"policy":"test"}\n', encoding="utf-8")
    selector.write_text('{"selector":"test"}\n', encoding="utf-8")
    return {
        "strategy": "options_test",
        "classification": "PROMISING BUT UNPROVEN",
        "change_scope": {
            "base_sha": "base123",
            "allowed_strategy_paths": ["options_manager/strategies/example.py"],
            "strategy_or_parameter_only": True,
            "risk_policy_unchanged": True,
            "contract_selection_semantics_unchanged": True,
            "fill_model_semantics_unchanged": True,
            "broker_routing_unchanged": True,
            "runtime_activation_unchanged": True,
        },
        "strategy_identity": {
            "underlying_entry_formula_frozen": True,
            "underlying_invalidation_formula_frozen": True,
            "target_formula_frozen": True,
            "timeframe_formula_frozen": True,
            "replay_forward_formula_parity": True,
            "causal_data_only": True,
            "lookahead_or_future_leak": False,
        },
        "contract_selection": {
            "mechanical_selection": True,
            "expiration_rule_frozen": True,
            "strike_rule_frozen": True,
            "dte_rule_frozen": True,
            "moneyness_or_delta_rule_frozen": True,
            "liquidity_rule_frozen": True,
            "quote_timestamp_aligned_to_decision": True,
            "no_hindsight_contract_choice": True,
            "same_selector_replay_and_forward": True,
            "selection_rule_id": "selector-v1",
            "selection_rule_path": str(selector),
            "selection_rule_sha256": _sha(selector),
        },
        "data_integrity": {
            "underlying_dataset_frozen": True,
            "option_quotes_dataset_frozen": True,
            "quote_source_identified": True,
            "bid_ask_available_at_decision": True,
            "stale_quotes_fail_closed": True,
            "missing_contract_rows_fail_closed": True,
            "underlying_manifest_path": str(underlying),
            "underlying_manifest_sha256": _sha(underlying),
            "option_quotes_manifest_path": str(quotes),
            "option_quotes_manifest_sha256": _sha(quotes),
        },
        "fill_realism": {
            "entry_uses_executable_quote": True,
            "exit_uses_executable_quote": True,
            "spread_included": True,
            "fees_included": True,
            "slippage_included": True,
            "no_fill_modeled": True,
            "gap_handling_modeled": True,
            "same_bar_ambiguity_pessimistic": True,
            "slippage_stress_pre_registered": True,
            "slippage_stress_pass": True,
            "entry_fill_basis": "ASK",
            "exit_fill_basis": "BID",
            "max_quote_age_seconds": 60,
        },
        "risk_policy": {
            "underlying_invalidation_required": True,
            "numeric_premium_stop_required": True,
            "planned_risk_uses_premium_stop": True,
            "aggregate_open_risk_enforced": True,
            "no_averaging_down": True,
            "max_trade_risk_dollars": 300,
            "max_aggregate_open_risk_dollars": 900,
        },
        "validation": {
            "untouched_validation_window": True,
            "multiple_months_covered": True,
            "chronological_walk_forward_pass": True,
            "sample_requirement_pre_registered": True,
            "drawdown_limit_pre_registered": True,
            "concentration_limit_pre_registered": True,
            "aggregate_expectancy_after_costs_positive": True,
            "aggregate_net_pnl_after_costs_positive": True,
            "required_resolved_fills_per_cell": 30,
            "required_cell_dimensions": [
                "setup_family",
                "direction",
                "dte_bucket",
                "liquidity_quality",
                "market_regime",
            ],
            "cells": [
                {
                    "cell_id": "212-call-45plus-liquid-aligned",
                    "required": True,
                    "resolved_fills": 34,
                    "expectancy_r_after_costs": 0.12,
                    "drawdown_within_limit": True,
                    "concentration_pass": True,
                    "average_spread_percent": 4.2,
                }
            ],
        },
        "golden_parity": {
            "fixture_set_frozen": True,
            "underlying_candidate_parity": True,
            "contract_selection_parity": True,
            "risk_decision_parity": True,
            "entry_fill_formula_parity": True,
            "exit_fill_formula_parity": True,
            "no_fill_case_covered": True,
            "stale_quote_case_covered": True,
            "missing_quote_case_covered": True,
            "wide_spread_case_covered": True,
            "premium_stop_case_covered": True,
            "underlying_invalidation_case_covered": True,
            "event_risk_case_covered": True,
        },
        "provenance": {
            "code_sha": "head123",
            "options_policy_path": str(policy),
            "options_policy_sha256": _sha(policy),
        },
    }


def _write(tmp_path: Path, payload: dict) -> Path:
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _patch_repo(monkeypatch, changed: str = "options_manager/strategies/example.py\ntests/test_example.py\n") -> None:
    monkeypatch.setattr(gate, "_head", lambda _root: "head123")
    monkeypatch.setattr(gate, "_git", lambda _root, _args: (changed, None))


def test_complete_evidence_is_demo_evidence_eligible(tmp_path: Path, monkeypatch) -> None:
    _patch_repo(monkeypatch)
    report = gate.build_options_demo_qualification_report(
        strategy="options_test",
        repo_root=tmp_path,
        evidence_path=_write(tmp_path, _evidence(tmp_path)),
    )
    assert report["gate_pass"] is True
    assert report["demo_evidence_eligible"] is True
    assert report["paper_demo_activation_authorized"] is False
    assert report["live_trading_authorized"] is False


def test_each_required_cell_needs_30_resolved_fills(tmp_path: Path, monkeypatch) -> None:
    _patch_repo(monkeypatch)
    payload = _evidence(tmp_path)
    payload["validation"]["cells"][0]["resolved_fills"] = 29
    report = gate.build_options_demo_qualification_report(
        strategy="options_test",
        repo_root=tmp_path,
        evidence_path=_write(tmp_path, payload),
    )
    assert report["gate_pass"] is False
    assert any("insufficient resolved fills" in x for x in report["blockers"])


def test_negative_required_cell_blocks_even_when_aggregate_is_positive(
    tmp_path: Path, monkeypatch
) -> None:
    _patch_repo(monkeypatch)
    payload = _evidence(tmp_path)
    payload["validation"]["cells"][0]["expectancy_r_after_costs"] = -0.01
    report = gate.build_options_demo_qualification_report(
        strategy="options_test",
        repo_root=tmp_path,
        evidence_path=_write(tmp_path, payload),
    )
    assert report["gate_pass"] is False
    assert any("negative expectancy" in x for x in report["blockers"])


def test_missing_quote_manifest_fails_closed(tmp_path: Path, monkeypatch) -> None:
    _patch_repo(monkeypatch)
    payload = _evidence(tmp_path)
    payload["data_integrity"]["option_quotes_manifest_path"] = str(tmp_path / "missing.json")
    report = gate.build_options_demo_qualification_report(
        strategy="options_test",
        repo_root=tmp_path,
        evidence_path=_write(tmp_path, payload),
    )
    assert report["gate_pass"] is False
    assert any("could not be hashed" in x for x in report["blockers"])


def test_disallowed_runtime_path_cannot_qualify(tmp_path: Path, monkeypatch) -> None:
    _patch_repo(
        monkeypatch,
        "options_manager/strategies/example.py\noptions_manager/app.py\n",
    )
    report = gate.build_options_demo_qualification_report(
        strategy="options_test",
        repo_root=tmp_path,
        evidence_path=_write(tmp_path, _evidence(tmp_path)),
    )
    assert report["gate_pass"] is False
    assert any("disallowed paths" in x for x in report["blockers"])


def test_missing_aggregate_risk_budget_blocks(tmp_path: Path, monkeypatch) -> None:
    _patch_repo(monkeypatch)
    payload = _evidence(tmp_path)
    payload["risk_policy"]["max_aggregate_open_risk_dollars"] = None
    report = gate.build_options_demo_qualification_report(
        strategy="options_test",
        repo_root=tmp_path,
        evidence_path=_write(tmp_path, payload),
    )
    assert report["gate_pass"] is False
    assert any("max_aggregate_open_risk_dollars" in x for x in report["blockers"])


def test_midpoint_only_fill_model_cannot_qualify(tmp_path: Path, monkeypatch) -> None:
    _patch_repo(monkeypatch)
    payload = _evidence(tmp_path)
    payload["fill_realism"]["entry_fill_basis"] = "MID"
    report = gate.build_options_demo_qualification_report(
        strategy="options_test",
        repo_root=tmp_path,
        evidence_path=_write(tmp_path, payload),
    )
    assert report["gate_pass"] is False
    assert any("entry_fill_basis" in x for x in report["blockers"])


def test_bool_numeric_values_fail_closed(tmp_path: Path, monkeypatch) -> None:
    _patch_repo(monkeypatch)
    payload = _evidence(tmp_path)
    payload["risk_policy"]["max_trade_risk_dollars"] = True
    payload["validation"]["cells"][0]["expectancy_r_after_costs"] = True
    payload["validation"]["cells"][0]["average_spread_percent"] = True
    report = gate.build_options_demo_qualification_report(
        strategy="options_test",
        repo_root=tmp_path,
        evidence_path=_write(tmp_path, payload),
    )
    assert report["gate_pass"] is False
    assert any("max_trade_risk_dollars" in x for x in report["blockers"])
    assert any("missing finite expectancy" in x for x in report["blockers"])
    assert any("missing valid average spread" in x for x in report["blockers"])


def test_selector_hash_is_verified_against_bytes(tmp_path: Path, monkeypatch) -> None:
    _patch_repo(monkeypatch)
    payload = _evidence(tmp_path)
    payload["contract_selection"]["selection_rule_sha256"] = "0" * 64
    report = gate.build_options_demo_qualification_report(
        strategy="options_test",
        repo_root=tmp_path,
        evidence_path=_write(tmp_path, payload),
    )
    assert report["gate_pass"] is False
    assert any("selection_rule_sha256" in x for x in report["blockers"])


def test_code_sha_mismatch_blocks(tmp_path: Path, monkeypatch) -> None:
    _patch_repo(monkeypatch)
    payload = _evidence(tmp_path)
    payload["provenance"]["code_sha"] = "wrong"
    report = gate.build_options_demo_qualification_report(
        strategy="options_test",
        repo_root=tmp_path,
        evidence_path=_write(tmp_path, payload),
    )
    assert report["gate_pass"] is False
    assert any("code_sha" in x for x in report["blockers"])


def test_base_sha_equal_to_head_blocks(tmp_path: Path, monkeypatch) -> None:
    _patch_repo(monkeypatch, changed="")
    payload = _evidence(tmp_path)
    payload["change_scope"]["base_sha"] = "head123"
    report = gate.build_options_demo_qualification_report(
        strategy="options_test",
        repo_root=tmp_path,
        evidence_path=_write(tmp_path, payload),
    )
    assert report["gate_pass"] is False
    assert any("pre-change commit" in x for x in report["blockers"])


def test_lookahead_true_blocks(tmp_path: Path, monkeypatch) -> None:
    _patch_repo(monkeypatch)
    payload = _evidence(tmp_path)
    payload["strategy_identity"]["lookahead_or_future_leak"] = True
    report = gate.build_options_demo_qualification_report(
        strategy="options_test",
        repo_root=tmp_path,
        evidence_path=_write(tmp_path, payload),
    )
    assert report["gate_pass"] is False
    assert any("lookahead_or_future_leak" in x for x in report["blockers"])


def test_bad_classification_blocks(tmp_path: Path, monkeypatch) -> None:
    _patch_repo(monkeypatch)
    payload = _evidence(tmp_path)
    payload["classification"] = "WAIT"
    report = gate.build_options_demo_qualification_report(
        strategy="options_test",
        repo_root=tmp_path,
        evidence_path=_write(tmp_path, payload),
    )
    assert report["gate_pass"] is False
    assert any("classification" in x for x in report["blockers"])
