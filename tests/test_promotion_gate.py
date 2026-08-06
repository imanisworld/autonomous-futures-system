from __future__ import annotations

import json
from pathlib import Path

from ops.promotion_gate import _classify, build_promotion_report, find_inventory_row, parse_master_table

INVENTORY_TEXT = """
# STRATEGY INVENTORY

## Master Table

| Strategy | Rules | Detector | Replay parity | Honest fills | Walk-forward | Slippage | Sample | Verdict |
|---|---|---|---|---|---|---|---|---|
| Strat 22 Reversal (Test) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ n=305 | **PAPER PROOF** |
| Broken Strategy Example | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ | ✅ n=50 | **BROKEN** |

## Retired Strategies

| Strategy | Verdict |
|---|---|
| Old One | RETIRE |
"""


def test_parse_master_table_stops_before_next_table():
    rows = parse_master_table(INVENTORY_TEXT)
    names = [row["Strategy"] for row in rows]
    assert names == ["Strat 22 Reversal (Test)", "Broken Strategy Example"]
    assert "Old One" not in names


def test_find_inventory_row_matches_by_token_overlap():
    row = find_inventory_row(INVENTORY_TEXT, "strat_22_reversal")
    assert row["status"] == "FOUND"
    assert row["documented_verdict"] == "PAPER PROOF"


def test_find_inventory_row_not_found_reports_unknown():
    row = find_inventory_row(INVENTORY_TEXT, "strat_nonexistent")
    assert row["status"] == "NOT_FOUND"
    assert row["documented_verdict"] is None


def _accounting(attempts=0, fills=0):
    return {"attempts": attempts, "fills": fills}


def _performance(filled=0, pf=None):
    return {"filled_trade_count": filled, "profit_factor": pf}


def test_classify_zero_attempts_is_wait():
    result = _classify(
        documented_verdict=None, accounting=_accounting(0, 0),
        performance=_performance(), chain_ok=True, chain_problems=[],
    )
    assert result["verdict"] == "WAIT"


def test_classify_zero_fills_is_broken():
    result = _classify(
        documented_verdict="PAPER PROOF", accounting=_accounting(5, 0),
        performance=_performance(), chain_ok=True, chain_problems=[],
    )
    assert result["verdict"] == "BROKEN"
    assert "Zero executable fills" in result["why"]


def test_classify_documented_broken_short_circuits():
    result = _classify(
        documented_verdict="BROKEN", accounting=_accounting(10, 8),
        performance=_performance(30, 2.0), chain_ok=True, chain_problems=[],
    )
    assert result["verdict"] == "BROKEN"


def test_classify_chain_integrity_problem_is_unsafe():
    result = _classify(
        documented_verdict="PAPER PROOF", accounting=_accounting(5, 5),
        performance=_performance(30, 2.0), chain_ok=False, chain_problems=["orphan outcome"],
    )
    assert result["verdict"] == "UNSAFE"


def test_classify_below_sample_minimum_is_promising_but_unproven():
    result = _classify(
        documented_verdict="PAPER PROOF", accounting=_accounting(5, 5),
        performance=_performance(3, 2.0), chain_ok=True, chain_problems=[],
    )
    assert result["verdict"] == "PROMISING BUT UNPROVEN"


def test_classify_validated_when_documented_and_pf_and_sample_clear():
    result = _classify(
        documented_verdict="PAPER PROOF", accounting=_accounting(35, 30),
        performance=_performance(30, 2.5), chain_ok=True, chain_problems=[],
    )
    assert result["verdict"] == "VALIDATED"


def _write_inventory(repo: Path) -> None:
    path = repo / "docs" / "strategy-rules" / "Strategy_Inventory.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(INVENTORY_TEXT, encoding="utf-8")


def _write_journal_trades(repo: Path, count: int, *, wins: int) -> None:
    log_dir = repo / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for i in range(count):
        ts = f"2026-08-01T{9 + (i % 6):02d}:{(i * 3) % 60:02d}:00Z"
        rows.append({
            "decision": "TRADE", "instrument": "MNQ", "ts": ts,
            "risk_check": {"result": "APPROVED"},
            "setup": {"direction": "LONG", "strategy": "strat_22_reversal", "entry": 100.0, "stop": 95.0, "target": 110.0},
        })
        result = "WIN" if i < wins else "LOSS"
        pnl = 10.0 if result == "WIN" else -2.0
        rows.append({
            "type": "OUTCOME", "instrument": "MNQ", "ts": ts,
            "outcome": {"result": result, "exit_reason": "TARGET_HIT", "pnl_dollars": pnl},
        })
    (log_dir / "journal_2026-08-01.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )


def test_build_promotion_report_end_to_end_validated(tmp_path):
    _write_inventory(tmp_path)
    _write_journal_trades(tmp_path, 30, wins=25)

    report = build_promotion_report("strat_22_reversal", repo_root=tmp_path, log_dir="logs")

    assert report["identity_parity"]["status"] == "FOUND"
    assert report["identity_parity"]["documented_verdict"] == "PAPER PROOF"
    assert report["paper_forward_evidence"]["accounting"]["attempts"] == 30
    assert report["paper_forward_evidence"]["accounting"]["fills"] == 30
    assert report["paper_forward_evidence"]["zero_executable_fills"] is False
    assert report["classification"]["verdict"] == "VALIDATED"


def test_build_promotion_report_reports_zero_fills_as_broken(tmp_path):
    _write_inventory(tmp_path)
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    rows = [
        {
            "decision": "TRADE", "instrument": "MNQ", "ts": "2026-08-01T09:00:00Z",
            "risk_check": {"result": "APPROVED"},
            "setup": {"direction": "LONG", "strategy": "strat_22_reversal", "entry": 100.0, "stop": 95.0, "target": 110.0},
        },
        {
            "type": "OUTCOME", "instrument": "MNQ", "ts": "2026-08-01T09:30:00Z",
            "outcome": {"result": "CANCELLED", "exit_reason": "IOC_NO_FILL", "no_fill_reason": "NO_FILL_PRICE_MOVED_AWAY"},
        },
    ]
    (log_dir / "journal_2026-08-01.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")

    report = build_promotion_report("strat_22_reversal", repo_root=tmp_path, log_dir="logs")
    assert report["classification"]["verdict"] == "BROKEN"
    assert report["paper_forward_evidence"]["zero_executable_fills"] is True


def test_build_promotion_report_research_result_is_labeled_unverified(tmp_path):
    _write_inventory(tmp_path)
    research = tmp_path / "research.json"
    research.write_text(json.dumps({"profit_factor": 3.5, "trade_count": 500}), encoding="utf-8")

    report = build_promotion_report(
        "strat_22_reversal", repo_root=tmp_path, log_dir="logs", research_evidence_path=research,
    )
    assert report["research_result"]["status"] == "PROVIDED_UNVERIFIED"
    assert "not independently verified" in report["research_result"]["caveat"]
    # Zero paper-forward evidence still drives classification, regardless of research file.
    assert report["classification"]["verdict"] == "WAIT"
