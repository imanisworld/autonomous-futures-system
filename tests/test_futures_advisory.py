"""Presentation-only futures advisory cards. No order path."""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

from notifications.futures_advisory import (
    RANK_STATUS,
    advisory_can_place_order,
    attach_runtime_sources,
    build_advisory_records,
    format_advisory_card,
    journal_advisory_records,
    notify_futures_advisory,
)


ROOT = Path(__file__).resolve().parents[1]
ADVISORY_PATH = ROOT / "notifications" / "futures_advisory.py"

_FORBIDDEN_IMPORTS = {
    "execution.tradovate_broker",
    "execution.paper_broker",
    "execution.broker",
    "risk.risk_engine",
    "strategy.signal_engine",
    "strategy.shadow_setups",
}


def _candidate(**overrides):
    row = {
        "strategy": "orb_reclaim",
        "direction": "LONG",
        "entry": 24310.25,
        "stop": 24300.25,
        "target": 24333.25,
        "rr_ratio": 2.3,
        "selected": False,
        "attempted": True,
        "reject_code": "rr_below_minimum",
        "reject_reason": "target too small for the risk",
        "session": "new_york",
        "market_condition": "TRENDING",
        "notes": "price reclaimed the opening-range high",
        "selection_mode": "first_match",
        "rank_score": 912.0,
        "rank_priority_index": 0,
        "risk_tier": "B",
    }
    row.update(overrides)
    return row


def _result(**overrides):
    result = {
        "decision": "SHADOW_NO_ORDER",
        "instrument": "MNQ",
        "session": "new_york",
        "timestamp": "2026-05-23T14:30:00+00:00",
        "gate_reason": "always_on_shadow",
        "reason": "schedule gate suppressed the order",
        "context": {
            "instrument": "MNQ",
            "session": "new_york",
            "market_condition": "TRENDING",
        },
        "candidate_audit": [_candidate()],
        "shadow_candidates": [],
    }
    result.update(overrides)
    return result


def test_advisory_module_does_not_import_execution_risk_or_strategy_engines():
    tree = ast.parse(ADVISORY_PATH.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
            imported.update(f"{node.module}.{alias.name}" for alias in node.names)
    assert not (imported & _FORBIDDEN_IMPORTS)
    assert advisory_can_place_order() is False


def test_notify_futures_advisory_never_calls_broker_execute():
    class _Router:
        def is_enabled(self, route):
            return route == "signal"

        def send(self, route, body):
            assert route == "signal"
            text = str(body)
            assert "execute_bracket" not in text
            assert "ADVISORY ONLY" in text
            return True

    sent = notify_futures_advisory(_result(), router=_Router())
    assert sent == 1
    assert advisory_can_place_order() is False


def test_displayed_geometry_equals_recorded_candidate():
    source = _candidate(entry=24310.25, stop=24300.25, target=24333.25, rr_ratio=2.3)
    records = build_advisory_records(_result(candidate_audit=[source]))
    assert len(records) == 1
    assert records[0]["entry"] == source["entry"]
    assert records[0]["stop"] == source["stop"]
    assert records[0]["target"] == source["target"]
    assert records[0]["rr_ratio"] == source["rr_ratio"]
    card = format_advisory_card(records[0])
    assert "24,310.25" in card
    assert "24,300.25" in card
    assert "24,333.25" in card
    assert "R:R: 2.3" in card


def test_suppression_reason_comes_from_recorded_state_only():
    records = build_advisory_records(_result())
    assert records[0]["suppression_reason"] == "target too small for the risk"
    card = format_advisory_card(records[0])
    assert "Why execution was blocked/suppressed: target too small for the risk" in card

    silent = _candidate()
    silent.pop("reject_reason")
    silent.pop("reject_code")
    silent["selected"] = False
    silent["attempted"] = False
    records = build_advisory_records(
        _result(
            candidate_audit=[silent],
            reason="should not be inferred for an unselected candidate",
            gate_reason="always_on_shadow",
        )
    )
    assert "suppression_reason" not in records[0]
    card = format_advisory_card(records[0])
    assert "Why execution was blocked/suppressed" not in card


def test_experimental_rank_is_labeled_and_not_called_best():
    ranked = _candidate(
        selection_mode="ranked",
        rank_priority_index=0,
        rank_reason="ranked candidate audit: confluence 8/10",
    )
    other = _candidate(
        strategy="vwap_hold",
        selection_mode="ranked",
        rank_priority_index=1,
        reject_code=None,
        reject_reason=None,
        selected=False,
    )
    records = build_advisory_records(_result(candidate_audit=[ranked, other]))
    card = format_advisory_card(records[0])
    assert "System rank: #1 of 2" in card
    assert RANK_STATUS in card
    assert "best trade" not in card.lower()
    assert "best setup" not in card.lower()

    first_match = build_advisory_records(_result())
    assert "system_rank" not in first_match[0]
    assert "Ranking status" not in format_advisory_card(first_match[0])


def test_missing_fields_stay_missing_instead_of_being_fabricated():
    sparse = {
        "strategy": "mystery_fade",
        "direction": "SHORT",
        "entry": 100.0,
        "stop": 110.0,
        "target": 80.0,
    }
    records = build_advisory_records(_result(candidate_audit=[sparse], shadow_candidates=[]))
    record = records[0]
    assert "risk_tier" not in record
    assert "evidence_classification" not in record
    assert "system_rank" not in record
    assert "rr_ratio" not in record
    assert "session" in record  # copied from the recorded result context
    card = format_advisory_card(record)
    assert "Entry: 100" in card
    assert "Risk tier" not in card
    assert "Strategy evidence" not in card
    assert "System rank" not in card
    assert "R:R" not in card
    assert "unknown" not in card.lower()
    assert "?" not in card


def test_outcome_display_uses_canonical_shadow_result():
    records = build_advisory_records(
        _result(
            shadow_candidates=[
                {
                    "strategy": "orb_false_break_fade",
                    "direction": "SHORT",
                    "entry": 24310.0,
                    "stop": 24320.0,
                    "target": 24290.0,
                    "rr_ratio": 2.0,
                    "risk_tier": "B",
                    "notes": "false break of the opening-range high",
                }
            ],
            candidate_audit=[],
            shadow_outcomes=[
                {
                    "instrument": "MNQ",
                    "strategy": "orb_false_break_fade",
                    "direction": "SHORT",
                    "entry": 24310.0,
                    "shadow_outcome": {
                        "result": "WIN",
                        "exit_reason": "TARGET_HIT",
                        "pnl_ticks": 80.0,
                    },
                }
            ],
        )
    )
    assert records[0]["outcome"] == "WIN"
    assert records[0]["pnl_ticks"] == 80.0
    card = format_advisory_card(records[0])
    assert "Later outcome: WIN (TARGET_HIT)" in card
    assert "Simulated ticks: 80.0" in card


def test_duplicate_bars_do_not_surface_advisory_cards():
    assert build_advisory_records(_result(decision="BLOCKED_DUPLICATE_BAR")) == []


def test_attach_runtime_sources_copies_recorded_decision_fields_only():
    result = {"decision": "NO_TRADE"}
    decision = SimpleNamespace(
        candidate_audit=[_candidate()],
        blocked_candidate_audit={"observation_only": True, "candidates": []},
    )
    attach_runtime_sources(
        result,
        decision=decision,
        shadow_outcomes=[{"strategy": "orb_false_break_fade", "shadow_outcome": {"result": "LOSS"}}],
    )
    assert result["candidate_audit"][0]["entry"] == 24310.25
    assert result["blocked_candidate_audit"]["observation_only"] is True
    assert result["shadow_outcomes_resolved"] == 1
    assert result["decision"] == "NO_TRADE"


def test_journal_advisory_joins_canonical_shadow_outcome():
    entries = [
        {
            "type": "DECISION",
            "instrument": "MNQ",
            "decision": "NO_TRADE",
            "shadow_candidates": [
                {
                    "strategy": "gap_fill",
                    "direction": "LONG",
                    "entry": 1.0,
                    "stop": 0.0,
                    "target": 3.0,
                    "rr_ratio": 2.0,
                }
            ],
        },
        {
            "type": "SHADOW_OUTCOME",
            "instrument": "MNQ",
            "strategy": "gap_fill",
            "direction": "LONG",
            "entry": 1.0,
            "shadow_outcome": {"result": "NO_FILL", "pnl_ticks": None},
        },
    ]
    records = journal_advisory_records(entries)
    assert records[0]["outcome"] == "NO_FILL"
    assert "pnl_ticks" not in records[0]


def test_notify_skips_when_signal_route_disabled():
    class _Router:
        def is_enabled(self, route):
            return False

        def send(self, *args, **kwargs):
            raise AssertionError("disabled route must not send")

    assert notify_futures_advisory(_result(), router=_Router()) == 0
