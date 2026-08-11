from __future__ import annotations

import json
from pathlib import Path

from ops import project_check as pc


def _decision(
    *,
    ts: str,
    instrument: str = "MNQ",
    decision: str = "TRADE",
    strategy: str = "orb_breakout",
    direction: str = "LONG",
    risk_result: str = "APPROVED",
    candidates: list[dict] | None = None,
) -> dict:
    row: dict = {
        "ts": ts,
        "instrument": instrument,
        "decision": decision,
        "risk_check": {"result": risk_result},
        "candidate_audit": candidates or [],
    }
    if decision == "TRADE":
        row["setup"] = {"strategy": strategy, "direction": direction, "entry": 100.0, "stop": 99.0, "target": 103.0}
    return row


def _outcome(*, ts: str, instrument: str = "MNQ", result: str = "WIN", pnl_dollars: float = 25.0, exit_reason: str = "target_hit") -> dict:
    return {
        "ts": ts,
        "instrument": instrument,
        "type": "OUTCOME",
        "outcome": {"result": result, "pnl_dollars": pnl_dollars, "exit_reason": exit_reason, "contracts": 1},
    }


def _write_journal(tmp_path: Path, name: str, rows: list[dict]) -> Path:
    path = tmp_path / name
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# accounting identity
# ---------------------------------------------------------------------------

def test_accounting_identity_holds_for_a_clean_win_loss_cancel_sequence():
    entries = [
        _decision(ts="2026-01-01T10:00:00Z"),
        _outcome(ts="2026-01-01T10:05:00Z", result="WIN", pnl_dollars=25.0),
        _decision(ts="2026-01-01T11:00:00Z"),
        _outcome(ts="2026-01-01T11:05:00Z", result="LOSS", pnl_dollars=-15.0),
        _decision(ts="2026-01-01T12:00:00Z"),
        _outcome(ts="2026-01-01T12:05:00Z", result="CANCELLED", pnl_dollars=None, exit_reason="ioc_expired"),
    ]
    result = pc._accounting_from_entries(entries, instrument="MNQ", strategy="orb_breakout")
    assert result["attempts"] == 3
    assert result["fills_resolved"] == 2
    assert result["cancellations_no_fill"] == 1
    assert result["unresolved_at_replay_boundary"] == 0
    assert result["accounting_identity_holds"] is True


def test_accounting_identity_counts_unresolved_trade_as_open_at_boundary():
    entries = [
        _decision(ts="2026-01-01T10:00:00Z"),
        _outcome(ts="2026-01-01T10:05:00Z", result="WIN", pnl_dollars=25.0),
        _decision(ts="2026-01-01T15:00:00Z"),  # never resolved -- data set ends here
    ]
    result = pc._accounting_from_entries(entries, instrument="MNQ", strategy="orb_breakout")
    assert result["attempts"] == 2
    assert result["fills_resolved"] == 1
    assert result["unresolved_at_replay_boundary"] == 1
    assert result["accounting_identity_holds"] is True


def test_accounting_is_scoped_to_requested_strategy_only():
    entries = [
        _decision(ts="2026-01-01T10:00:00Z", strategy="orb_breakout"),
        _outcome(ts="2026-01-01T10:05:00Z", result="WIN", pnl_dollars=25.0),
        _decision(ts="2026-01-01T11:00:00Z", strategy="vwap_hold"),
        _outcome(ts="2026-01-01T11:05:00Z", result="LOSS", pnl_dollars=-10.0),
    ]
    result = pc._accounting_from_entries(entries, instrument="MNQ", strategy="orb_breakout")
    assert result["attempts"] == 1
    assert result["fills_resolved"] == 1


def test_accounting_is_scoped_to_requested_instrument_only():
    entries = [
        _decision(ts="2026-01-01T10:00:00Z", instrument="MNQ"),
        _outcome(ts="2026-01-01T10:05:00Z", instrument="MNQ", result="WIN", pnl_dollars=25.0),
        _decision(ts="2026-01-01T11:00:00Z", instrument="MES"),
        _outcome(ts="2026-01-01T11:05:00Z", instrument="MES", result="WIN", pnl_dollars=10.0),
    ]
    result = pc._accounting_from_entries(entries, instrument="MNQ", strategy="orb_breakout")
    assert result["attempts"] == 1
    assert result["fills_resolved"] == 1


def test_reconciler_touched_outcome_is_bucketed_separately_from_fills():
    entries = [
        _decision(ts="2026-01-01T10:00:00Z"),
        _outcome(ts="2026-01-01T10:05:00Z", result="WIN", pnl_dollars=25.0, exit_reason="reconciler_phantom_clear"),
    ]
    result = pc._accounting_from_entries(entries, instrument="MNQ", strategy="orb_breakout")
    assert result["fills_resolved"] == 0
    assert result["rejects_or_unclassified"] == 1
    assert result["reconciler_touched_needs_manual_review"] == 1
    assert result["accounting_identity_holds"] is True


# ---------------------------------------------------------------------------
# performance
# ---------------------------------------------------------------------------

def test_performance_summary_computes_pf_expectancy_and_walk_forward():
    entries = [
        _decision(ts="2026-01-01T10:00:00Z"),
        _outcome(ts="2026-01-01T10:05:00Z", result="WIN", pnl_dollars=100.0),
        _decision(ts="2026-01-02T10:00:00Z"),
        _outcome(ts="2026-01-02T10:05:00Z", result="LOSS", pnl_dollars=-50.0),
        _decision(ts="2026-01-03T10:00:00Z"),
        _outcome(ts="2026-01-03T10:05:00Z", result="WIN", pnl_dollars=80.0),
        _decision(ts="2026-01-04T10:00:00Z"),
        _outcome(ts="2026-01-04T10:05:00Z", result="LOSS", pnl_dollars=-20.0),
    ]
    accounting = pc._accounting_from_entries(entries, instrument="MNQ", strategy="orb_breakout")
    perf = pc._performance_from_resolved(accounting["resolved_trades"])
    assert perf["sample"] == 4
    assert perf["net_pnl_dollars"] == 110.0
    assert perf["profit_factor"] == round(180.0 / 70.0, 3)
    assert perf["win_rate"] == 0.5


def test_performance_with_no_resolved_trades_is_explicit():
    perf = pc._performance_from_resolved([])
    assert perf["sample"] == 0
    assert "note" in perf


# ---------------------------------------------------------------------------
# gate attrition
# ---------------------------------------------------------------------------

def test_gate_attrition_counts_candidates_scoped_to_strategy():
    audit = {
        "decisions": [
            {
                "candidates": [
                    {"strategy": "orb_breakout", "attempted": True, "selected": True, "winner": True, "reject_code": None, "failed_gates": []},
                    {"strategy": "vwap_hold", "attempted": True, "selected": False, "winner": False, "reject_code": "min_rr", "failed_gates": ["min_rr"]},
                ]
            },
            {
                "candidates": [
                    {"strategy": "orb_breakout", "attempted": False, "selected": False, "winner": False, "reject_code": "trend_strength", "failed_gates": ["trend_strength"]},
                ]
            },
        ]
    }
    gates = pc._gate_attrition(audit, "orb_breakout")
    assert gates["candidates_considered"] == 2
    assert gates["candidates_attempted"] == 1
    assert gates["candidates_selected"] == 1
    assert gates["candidates_winner"] == 1
    assert gates["reject_code_counts"] == {"trend_strength": 1}


# ---------------------------------------------------------------------------
# strategy_permission_gate lookup
# ---------------------------------------------------------------------------

def test_strategy_permission_status_reads_risk_rules(tmp_path: Path):
    (tmp_path / "risk_rules.yaml").write_text(
        "strategy_permission_gate:\n"
        "  enabled: true\n"
        "  default_status: SHADOW_ONLY\n"
        "  strategy_status:\n"
        "    orb_breakout: PAPER_ELIGIBLE\n",
        encoding="utf-8",
    )
    status = pc._strategy_permission_status("orb_breakout", tmp_path)
    assert status["gate_enabled"] is True
    assert status["effective_status"] == "PAPER_ELIGIBLE"

    status_missing = pc._strategy_permission_status("strat_322", tmp_path)
    assert status_missing["strategy_status"] is None
    assert status_missing["effective_status"] == "SHADOW_ONLY"


# ---------------------------------------------------------------------------
# session-state persistence used by precommit
# ---------------------------------------------------------------------------

def test_write_and_read_json_round_trips(tmp_path: Path):
    path = tmp_path / "nested" / "state.json"
    pc._write_json(path, {"branch": "main", "n": 1})
    assert pc._read_json(path) == {"branch": "main", "n": 1}


def test_read_json_missing_file_returns_none(tmp_path: Path):
    assert pc._read_json(tmp_path / "missing.json") is None
