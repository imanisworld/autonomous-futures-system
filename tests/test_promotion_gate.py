"""Tests for ops/promotion_gate.py (Strategy Promotion Proof Gate).

Uses small, hand-crafted synthetic results.json/raw_trades.jsonl fixtures
matching the REAL field shapes learned by reading
scripts/orb_breakout_canonical_evidence.py, scripts/vwap_reclaim_canonical_evidence.py,
and scripts/strat_212_122_canonical_evidence_run.py / _report.py -- never the
strategy logic itself, and never a real logs/ journal.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest
import yaml

from ops.promotion_gate import (
    KNOWN_STRATEGIES,
    build_promotion_report,
    compute_execution_identity,
    compute_paper_forward_evidence,
    compute_performance,
    detect_pnl_field,
    filter_rows_for_strategy,
    select_primary_scenario,
)


# ── row builders (orb_breakout / vwap_reclaim shape) ────────────────────────


def _row(
    date: str,
    *,
    half: str,
    direction: str = "LONG",
    instrument: str = "MNQ",
    session: str = "new_york",
    filled: int = 1,
    result: str | None = "WIN",
    pnl: float = 100.0,
    run_tag: str = "static_1tick",
    order_id: str | None = None,
) -> dict:
    cancelled = 0 if filled else 1
    resolved = 1 if result in ("WIN", "LOSS", "BREAKEVEN") else 0
    return {
        "attempted": 1,
        "date": date,
        "bar_ts": f"{date}T14:00:00+00:00",
        "direction": direction,
        "instrument": instrument,
        "session": session,
        "half": half,
        "filled": filled,
        "cancelled_no_fill": cancelled,
        "resolved": resolved,
        "open": 0,
        "result": result if filled else None,
        "pnl_after_commission": pnl if (filled and resolved) else 0.0,
        "pnl_before_commission": pnl if (filled and resolved) else 0.0,
        "run_tag": run_tag,
        "paper_order_id": order_id or f"PAPER-{date}-{run_tag}-{direction}",
    }


def _clean_validated_rows() -> list[dict]:
    """15 wins / 15 losses per half x per slippage tag, all halves/tags net positive.

    Sized to comfortably clear a --sample-adequate-min of 20 in tests while
    keeping fixtures small (default production minimum is 30; tests pass
    sample_adequate_min=20 explicitly where this fixture is used).
    """
    rows: list[dict] = []
    for tag in ("static_1tick", "static_2tick", "static_3tick"):
        for half, start_day in (("H1", 1), ("H2", 1)):
            date_prefix = "2025-08" if half == "H1" else "2026-02"
            for i in range(11):
                win = i % 2 == 0  # majority wins to keep PF>1 comfortably
                rows.append(
                    _row(
                        f"{date_prefix}-{(i % 27) + 1:02d}",
                        half=half,
                        result="WIN" if win or i % 5 == 0 else "LOSS",
                        pnl=120.0 if (win or i % 5 == 0) else -40.0,
                        run_tag=tag,
                        order_id=f"PAPER-{tag}-{half}-{i}",
                    )
                )
    return rows


CLEAN_META = {
    "main_sha": "deadbeef",
    "range": ["2025-07-24", "2026-07-23"],
    "commission_round_trip": 1.48,
    "isolation": {
        "enabled_concepts": ["orb_breakout"],
        "entry_fill_model": "ioc_limit",
        "entry_tolerance_ticks_by_root": {"MNQ": 32.0},
    },
}


def _write_artifact(tmp_path: Path, rows: list[dict], meta: dict, name: str = "x") -> tuple[Path, Path]:
    results_path = tmp_path / f"{name}_results.json"
    raw_path = tmp_path / f"{name}_raw_trades.jsonl"
    results_path.write_text(json.dumps({"meta": meta, "classification": {}}) + "\n")
    with raw_path.open("w") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")
    return results_path, raw_path


def _write_risk_rules(tmp_path: Path, strategy: str, status: str) -> Path:
    path = tmp_path / "risk_rules.yaml"
    path.write_text(
        yaml.safe_dump(
            {"strategy_permission_gate": {"enabled": True, "default_status": "SHADOW_ONLY", "strategy_status": {strategy: status}}}
        )
    )
    return path


@pytest.fixture
def matching_config(config):
    return dataclasses.replace(config, entry_fill_model="ioc_limit", entry_tolerance_ticks_by_root={"MNQ": 32.0})


@pytest.fixture
def mismatched_config(config):
    return dataclasses.replace(config, entry_fill_model="market", entry_tolerance_ticks_by_root={"MNQ": 8.0})


# ── (a) clean case: identities hold, parity matches -> non-BROKEN verdict ──


def test_clean_case_is_not_broken(tmp_path, matching_config):
    rows = _clean_validated_rows()
    results_path, raw_path = _write_artifact(tmp_path, rows, CLEAN_META)
    risk_rules_path = _write_risk_rules(tmp_path, "orb_breakout", "PAPER_ELIGIBLE")

    report = build_promotion_report(
        strategy="orb_breakout",
        results_path=results_path,
        raw_trades_path=raw_path,
        journal_dir=tmp_path / "no_logs",
        since=None,
        risk_rules_path=risk_rules_path,
        sample_adequate_min=20,
        config=matching_config,
    )

    assert report["ok"] is True
    assert report["execution"]["identity_mismatches"] == []
    assert report["classification"]["verdict"] != "BROKEN"
    assert report["classification"]["runtime_parity"]["parity_defects"] == []
    # This fixture is walk-forward-positive, slippage-sweep-positive, sample
    # adequate, PF>1, no parity defect -> should clear all the way to VALIDATED.
    assert report["classification"]["verdict"] == "VALIDATED", report["classification"]["verdict_reasons"]


# ── (b) accounting-identity mismatch -> BROKEN ──────────────────────────────


def test_accounting_identity_mismatch_is_broken(tmp_path, matching_config):
    rows = _clean_validated_rows()
    # Corrupt one row: mark it attempted+filled AND cancelled (double-booked),
    # breaking attempts == fills + cancellations.
    rows[0]["cancelled_no_fill"] = 1
    assert rows[0]["filled"] == 1  # both filled and cancelled -> inconsistent
    results_path, raw_path = _write_artifact(tmp_path, rows, CLEAN_META)
    risk_rules_path = _write_risk_rules(tmp_path, "orb_breakout", "PAPER_ELIGIBLE")

    report = build_promotion_report(
        strategy="orb_breakout",
        results_path=results_path,
        raw_trades_path=raw_path,
        journal_dir=tmp_path / "no_logs",
        since=None,
        risk_rules_path=risk_rules_path,
        sample_adequate_min=20,
        config=matching_config,
    )

    assert report["classification"]["verdict"] == "BROKEN"
    assert report["execution"]["identity_mismatches"]
    assert any("mismatch" in r.lower() for r in report["classification"]["verdict_reasons"])


# ── (c) fill-model/tolerance parity defect -> PROMISING BUT UNPROVEN at best ─


def test_parity_defect_caps_below_validated(tmp_path, matching_config, mismatched_config):
    rows = _clean_validated_rows()
    results_path, raw_path = _write_artifact(tmp_path, rows, CLEAN_META)
    risk_rules_path = _write_risk_rules(tmp_path, "orb_breakout", "PAPER_ELIGIBLE")

    # Same evidence, matching runtime config -> VALIDATED (control).
    clean_report = build_promotion_report(
        strategy="orb_breakout",
        results_path=results_path,
        raw_trades_path=raw_path,
        journal_dir=tmp_path / "no_logs",
        since=None,
        risk_rules_path=risk_rules_path,
        sample_adequate_min=20,
        config=matching_config,
    )
    assert clean_report["classification"]["verdict"] == "VALIDATED"

    # Same evidence, mismatched runtime config -> capped below VALIDATED.
    defect_report = build_promotion_report(
        strategy="orb_breakout",
        results_path=results_path,
        raw_trades_path=raw_path,
        journal_dir=tmp_path / "no_logs",
        since=None,
        risk_rules_path=risk_rules_path,
        sample_adequate_min=20,
        config=mismatched_config,
    )
    assert defect_report["classification"]["runtime_parity"]["parity_defects"]
    assert defect_report["classification"]["verdict"] == "PROMISING BUT UNPROVEN"
    assert defect_report["classification"]["verdict"] != "VALIDATED"


# ── (d) zero fills -> WAIT ───────────────────────────────────────────────────


def test_zero_fills_is_wait(tmp_path, matching_config):
    rows = [
        _row("2025-08-0" + str(i + 1), half="H1", filled=0, result=None, run_tag="static_1tick")
        for i in range(5)
    ]
    results_path, raw_path = _write_artifact(tmp_path, rows, CLEAN_META)
    risk_rules_path = _write_risk_rules(tmp_path, "orb_breakout", "PAPER_ELIGIBLE")

    report = build_promotion_report(
        strategy="orb_breakout",
        results_path=results_path,
        raw_trades_path=raw_path,
        journal_dir=tmp_path / "no_logs",
        since=None,
        risk_rules_path=risk_rules_path,
        sample_adequate_min=20,
        config=matching_config,
    )

    assert report["execution"]["fills"] == 0
    assert report["classification"]["verdict"] == "WAIT"
    assert any("zero executable fills" in r for r in report["classification"]["verdict_reasons"])


# ── (e) missing artifact files -> clear UNKNOWN/error, no crash ────────────


def test_missing_artifact_files_gives_clear_error_not_traceback(tmp_path):
    report = build_promotion_report(
        strategy="orb_breakout",
        results_path=tmp_path / "does_not_exist_results.json",
        raw_trades_path=tmp_path / "does_not_exist_raw_trades.jsonl",
        journal_dir=tmp_path / "no_logs",
        since=None,
        risk_rules_path=tmp_path / "does_not_exist_risk_rules.yaml",
        config=None,
    )
    assert report["ok"] is False
    assert "not found" in report["error"]
    assert "instructions" in report and report["instructions"]
    assert "Traceback" not in json.dumps(report)


def test_cli_missing_files_no_raw_traceback(tmp_path, capsys):
    from ops.promotion_gate import main

    exit_code = main(
        [
            "--strategy",
            "orb_breakout",
            "--results",
            str(tmp_path / "missing_results.json"),
            "--raw-trades",
            str(tmp_path / "missing_raw.jsonl"),
        ]
    )
    out = capsys.readouterr().out
    assert exit_code == 2
    assert "Traceback" not in out
    assert "ERROR" in out


def test_cli_unknown_strategy_no_defaults(capsys):
    from ops.promotion_gate import main

    exit_code = main(["--strategy", "not_a_real_strategy"])
    captured = capsys.readouterr()
    assert exit_code == 2
    assert "Unknown strategy" in captured.err
    assert "Traceback" not in captured.err


# ── PAPER FORWARD EVIDENCE (journal-based, via ops.proof_30_mnq) ───────────


def _write_journal(tmp_path: Path, day: str, entries: list[dict]) -> Path:
    path = tmp_path / f"journal_{day}.jsonl"
    with path.open("w") as fh:
        for entry in entries:
            fh.write(json.dumps(entry) + "\n")
    return path


def test_paper_forward_evidence_counts_real_filled_trade(tmp_path):
    _write_journal(
        tmp_path,
        "2026-01-05",
        [
            {
                "decision": "TRADE",
                "instrument": "MNQ",
                "ts": "2026-01-05T10:00:00Z",
                "risk_check": {"result": "APPROVED"},
                "setup": {"strategy": "orb_breakout", "direction": "LONG"},
            },
            {
                "type": "OUTCOME",
                "instrument": "MNQ",
                "ts": "2026-01-05T10:15:00Z",
                "outcome": {"result": "WIN", "pnl_dollars": 50.0, "exit_reason": "TARGET_HIT"},
            },
        ],
    )

    report = compute_paper_forward_evidence(tmp_path, "orb_breakout", ["MNQ"], None)

    assert report["total_resolved_pairs_this_strategy"] == 1
    assert report["filled_win_loss_count"] == 1
    assert report["has_real_paper_forward_evidence"] is True
    assert report["filled_win_loss_pnl_dollars"] == 50.0


def test_paper_forward_evidence_ignores_other_strategy(tmp_path):
    _write_journal(
        tmp_path,
        "2026-01-06",
        [
            {
                "decision": "TRADE",
                "instrument": "MNQ",
                "ts": "2026-01-06T10:00:00Z",
                "risk_check": {"result": "APPROVED"},
                "setup": {"strategy": "vwap_reclaim", "direction": "LONG"},
            },
            {
                "type": "OUTCOME",
                "instrument": "MNQ",
                "ts": "2026-01-06T10:15:00Z",
                "outcome": {"result": "WIN", "pnl_dollars": 75.0, "exit_reason": "TARGET_HIT"},
            },
        ],
    )

    report = compute_paper_forward_evidence(tmp_path, "orb_breakout", ["MNQ"], None)

    assert report["total_resolved_pairs_this_strategy"] == 0
    assert report["has_real_paper_forward_evidence"] is False


def test_paper_forward_evidence_since_filter_excludes_earlier_trade(tmp_path):
    _write_journal(
        tmp_path,
        "2026-01-05",
        [
            {
                "decision": "TRADE",
                "instrument": "MNQ",
                "ts": "2026-01-05T10:00:00Z",
                "risk_check": {"result": "APPROVED"},
                "setup": {"strategy": "orb_breakout", "direction": "LONG"},
            },
            {
                "type": "OUTCOME",
                "instrument": "MNQ",
                "ts": "2026-01-05T10:15:00Z",
                "outcome": {"result": "WIN", "pnl_dollars": 50.0, "exit_reason": "TARGET_HIT"},
            },
        ],
    )

    report = compute_paper_forward_evidence(tmp_path, "orb_breakout", ["MNQ"], "2026-06-01T00:00:00Z")

    assert report["total_resolved_pairs_this_strategy"] == 0


def test_paper_forward_evidence_missing_journal_dir_is_unknown(tmp_path):
    report = compute_paper_forward_evidence(tmp_path / "nope", "orb_breakout", ["MNQ"], None)
    assert report["status"] == "UNKNOWN"
    assert "does not exist" in report["reason"]


# ── unit-level coverage of building blocks ──────────────────────────────────


def test_filter_rows_for_strategy_combined_book_shape():
    rows = [
        {"strategy": "strat_212", "result": "WIN", "pnl": 10.0},
        {"strategy": "strat_122", "result": "LOSS", "pnl": -5.0},
        {"strategy": "strat_212", "result": "LOSS", "pnl": -3.0},
    ]
    filtered, note = filter_rows_for_strategy(rows, "strat_212", "strat_212")
    assert len(filtered) == 2
    assert all(r["strategy"] == "strat_212" for r in filtered)
    assert "filtered raw_trades rows" in note


def test_filter_rows_for_strategy_isolated_shape_assumes_all_rows():
    rows = [{"direction": "LONG", "result": "WIN"}, {"direction": "SHORT", "result": "LOSS"}]
    filtered, note = filter_rows_for_strategy(rows, "orb_breakout", None)
    assert filtered == rows
    assert "assumed" in note


def test_select_primary_scenario_prefers_static_1tick():
    rows = [
        {"run_tag": "runner_1tick"},
        {"run_tag": "static_1tick"},
        {"run_tag": "static_2tick"},
    ]
    primary, primary_rows, tags = select_primary_scenario(rows)
    assert primary == "static_1tick"
    assert len(primary_rows) == 1
    assert set(tags) == {"runner_1tick", "static_1tick", "static_2tick"}


def test_select_primary_scenario_no_tags_returns_all_rows():
    rows = [{"date": "2025-01-01"}, {"date": "2025-01-02"}]
    primary, primary_rows, tags = select_primary_scenario(rows)
    assert primary is None
    assert primary_rows == rows
    assert tags == []


def test_detect_pnl_field_prefers_after_commission():
    rows = [{"pnl_after_commission": 1.0, "pnl_before_commission": 2.0}]
    assert detect_pnl_field(rows) == "pnl_after_commission"


def test_detect_pnl_field_falls_back_to_pnl_dollars():
    rows = [{"pnl_dollars": 5.0}]
    assert detect_pnl_field(rows) == "pnl_dollars"


def test_detect_pnl_field_none_when_absent():
    rows = [{"result": "WIN"}]
    assert detect_pnl_field(rows) is None


def test_compute_execution_identity_strat212_shape_marks_fills_unknown():
    rows = [
        {"result": "WIN", "pnl": 10.0, "unjoinable_legacy": False},
        {"result": "LOSS", "pnl": -5.0, "unjoinable_legacy": False},
        {"result": None, "pnl": 0.0, "unjoinable_legacy": False},
    ]
    execution = compute_execution_identity(rows)
    assert execution["resolved_outcomes"] == 2
    assert execution["legitimately_open_positions"] == 1
    assert execution["fills"].startswith("UNKNOWN")
    assert execution["identity_checkable"] is False


def test_compute_performance_walk_forward_from_half_field():
    rows = [
        {"date": "2025-08-01", "half": "H1", "result": "WIN", "pnl_after_commission": 100.0},
        {"date": "2025-08-02", "half": "H1", "result": "LOSS", "pnl_after_commission": -20.0},
        {"date": "2026-02-01", "half": "H2", "result": "WIN", "pnl_after_commission": 50.0},
        {"date": "2026-02-02", "half": "H2", "result": "LOSS", "pnl_after_commission": -80.0},
    ]
    perf = compute_performance(rows, "pnl_after_commission", rows, None)
    assert perf["by_half_walk_forward"]["H1"]["net_pnl"] == 80.0
    assert perf["by_half_walk_forward"]["H2"]["net_pnl"] == -30.0
    assert perf["net_pnl"] == 50.0


def test_registry_has_expected_known_strategies():
    for name in ("orb_breakout", "vwap_reclaim", "strat_212", "strat_122"):
        assert name in KNOWN_STRATEGIES
