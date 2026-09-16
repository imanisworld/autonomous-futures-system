from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts.why_no_trade_report import (
    _filter_rows,
    _load_jsonl,
    _parse_filter_dt,
    build_report,
    decision_bar,
)


def _row(**overrides):
    row = {
        "ts": "2026-09-16T09:00:00+00:00",
        "instrument": "MNQ",
        "session": "london",
        "timeframe_minutes": 15,
        "decision": "NO_TRADE",
        "reason": "Market condition RANGE_BOUND is not TRENDING.",
        "market_condition": "RANGE_BOUND",
        "regime": None,
        "failed_gates": ["MARKET_CONDITION_NOT_TRENDING"],
        "candidate_audit": [],
        "shadow_candidates": [],
        "setup": None,
        "context": {
            "instrument": "MNQ",
            "market_condition": "RANGE_BOUND",
            "structural_market_condition": "STRUCTURAL_TREND_UP",
            "structural_direction": "UP",
            "structural_mismatch": True,
            "structural_gate_authoritative": False,
        },
    }
    row.update(overrides)
    return row


def test_per_bar_chain_preserves_authoritative_and_observation_fields():
    bar = decision_bar(_row())
    assert bar["market_condition"] == "RANGE_BOUND"
    assert bar["structural_market_condition"] == "STRUCTURAL_TREND_UP"
    assert bar["structural_mismatch"] is True
    assert bar["structural_gate_authoritative"] is False
    assert bar["failed_gates"] == ["MARKET_CONDITION_NOT_TRENDING"]
    assert bar["primary_rejection"] == "MARKET_CONDITION_NOT_TRENDING"
    assert bar["decision_candidate_count"] == 0
    assert bar["shadow_candidate_count"] == 0
    assert bar["candidate_record_count"] == 0
    assert bar["selected_setup"] is None


def test_report_preserves_decision_and_shadow_candidate_provenance():
    second = _row(
        ts="2026-09-16T12:00:00+00:00",
        market_condition="TRENDING",
        regime="FULL_LONG",
        failed_gates=[],
        reason="No qualifying setup found.",
        context={
            "instrument": "MNQ",
            "market_condition": "TRENDING",
            "structural_market_condition": "STRUCTURAL_TREND_UP",
            "structural_direction": "UP",
            "structural_mismatch": False,
            "structural_gate_authoritative": False,
        },
        candidate_audit=[
            {
                "strategy": "strat_22_reversal",
                "candidate_direction": "LONG",
                "selected": False,
                "attempted": False,
                "reject_code": "NOT_EXECUTABLE",
            }
        ],
        shadow_candidates=[
            {
                "strategy": "strat_22_reversal",
                "direction": "LONG",
            }
        ],
    )
    outcome = {"type": "OUTCOME", "instrument": "MNQ", "outcome": {"result": "WIN"}}
    report = build_report([_row(), second, outcome])
    assert report["authority"] == "journal_read_only"
    assert report["candidate_sources_preserved"] is True
    assert report["structural_gate_expected_authoritative"] is False
    assert report["decision_bars"] == 2

    bar = report["bars"][1]
    assert bar["candidate_sources"]["decision"][0]["direction"] == "LONG"
    assert bar["candidate_sources"]["shadow"][0]["direction"] == "LONG"
    assert bar["decision_candidate_count"] == 1
    assert bar["shadow_candidate_count"] == 1
    assert bar["candidate_record_count"] == 2

    summary = report["summary"]
    assert summary["bars_with_decision_candidates"] == 1
    assert summary["bars_with_shadow_candidates"] == 1
    assert summary["bars_with_any_candidate"] == 1
    assert summary["bars_without_any_candidate"] == 1
    assert summary["structural_trend_bars"] == 2
    assert summary["structural_trend_pine_nontrending"] == 1
    assert summary["structural_mismatch_true"] == 1
    assert summary["unexpected_structural_authority_true"] == 0
    assert summary["failed_gates"] == {"MARKET_CONDITION_NOT_TRENDING": 1}
    assert summary["decision_candidate_strategy_records"] == {"strat_22_reversal": 1}
    assert summary["shadow_candidate_strategy_records"] == {"strat_22_reversal": 1}


def test_unexpected_structural_authority_is_visible_not_silently_normalized():
    row = _row(
        context={
            "instrument": "MNQ",
            "market_condition": "RANGE_BOUND",
            "structural_market_condition": "STRUCTURAL_TREND_UP",
            "structural_direction": "UP",
            "structural_mismatch": True,
            "structural_gate_authoritative": True,
        }
    )
    assert build_report([row])["summary"]["unexpected_structural_authority_true"] == 1


def test_risk_rejection_is_primary_over_earlier_diagnostic_gate():
    row = _row(
        decision="RISK_REJECTED",
        failed_gates=["NON_BLOCKING_DIAGNOSTIC"],
        risk_check={"result": "REJECTED", "failed_rule": "max_daily_loss", "reason": "cap"},
    )
    assert decision_bar(row)["primary_rejection"] == "max_daily_loss"


def test_journal_parse_and_time_filters_fail_closed(tmp_path):
    broken = tmp_path / "journal_2026-09-16.jsonl"
    broken.write_text('{"decision":"NO_TRADE"}\nnot-json\n', encoding="utf-8")
    with pytest.raises(ValueError, match="invalid JSON"):
        _load_jsonl([broken])

    non_object = tmp_path / "journal_2026-09-17.jsonl"
    non_object.write_text("[]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="journal row must be a JSON object"):
        _load_jsonl([non_object])

    with pytest.raises(ValueError, match="valid ISO-8601"):
        _parse_filter_dt("not-a-time", label="--start")

    bad_ts_row = _row(ts="not-a-time")
    with pytest.raises(ValueError, match="invalid timestamp"):
        _filter_rows(
            [bad_ts_row],
            instrument="MNQ",
            timeframe=15,
            start=datetime(2026, 9, 16, tzinfo=timezone.utc),
            end=None,
        )


def test_report_script_is_reporting_only():
    source = (
        Path(__file__).resolve().parents[1] / "scripts" / "why_no_trade_report.py"
    ).read_text(encoding="utf-8")
    for forbidden in (
        "from strategy",
        "import strategy",
        "from risk",
        "import risk",
        "from execution",
        "import execution",
        "from webhook",
        "import webhook",
        "PaperBroker",
        "RiskEngine",
        "DecisionEngine",
    ):
        assert forbidden not in source
