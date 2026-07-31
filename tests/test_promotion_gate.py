from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from ops.evidence_readiness import MIN_PROFIT_FACTOR, STRATEGY_MIN_DAYS, STRATEGY_MIN_EXAMPLES
from ops.promotion_gate import build_promotion_report


def _day(offset: int) -> str:
    return (date(2026, 6, 1) + timedelta(days=offset)).isoformat()


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def _trade(ts: str, instrument: str, strategy: str, direction: str = "LONG") -> dict:
    return {
        "ts": ts,
        "instrument": instrument,
        "decision": "TRADE",
        "risk_check": {"result": "APPROVED"},
        "setup": {"direction": direction, "strategy": strategy, "entry": 100.0, "stop": 95.0, "target": 110.0, "contracts": 1},
    }


def _outcome(ts: str, instrument: str, *, result: str, pnl: float, exit_reason: str = "target hit") -> dict:
    return {
        "ts": ts,
        "type": "OUTCOME",
        "instrument": instrument,
        "outcome": {"result": result, "exit_reason": exit_reason, "pnl_dollars": pnl, "contracts": 1},
    }


def test_zero_fills_classifies_wait(tmp_path):
    report = build_promotion_report(journal_dir=tmp_path, strategy="orb_breakout")
    assert report["classification"]["classification"] == "WAIT"
    assert report["performance"]["filled_count"] == 0


def test_small_profitable_sample_classifies_promising_but_unproven(tmp_path):
    rows = []
    for day in range(3):
        ts = f"2026-06-0{day + 1}T10:00:00+00:00"
        outcome_ts = f"2026-06-0{day + 1}T11:00:00+00:00"
        rows.append(_trade(ts, "MNQ", "orb_breakout"))
        rows.append(_outcome(outcome_ts, "MNQ", result="WIN", pnl=50.0))
    _write_jsonl(tmp_path / "journal_2026-06-01.jsonl", rows)

    report = build_promotion_report(journal_dir=tmp_path, strategy="orb_breakout", instrument="MNQ")
    assert report["performance"]["filled_count"] == 3
    assert report["classification"]["classification"] == "PROMISING BUT UNPROVEN"


def test_large_losing_sample_classifies_broken(tmp_path):
    rows = []
    n = STRATEGY_MIN_EXAMPLES + 2
    for day in range(n):
        ts = f"{_day(day)}T10:00:00+00:00"
        outcome_ts = f"{_day(day)}T11:00:00+00:00"
        rows.append(_trade(ts, "MNQ", "orb_breakout"))
        rows.append(_outcome(outcome_ts, "MNQ", result="LOSS", pnl=-25.0))
    _write_jsonl(tmp_path / "journal_2026-06-01.jsonl", rows)

    report = build_promotion_report(journal_dir=tmp_path, strategy="orb_breakout", instrument="MNQ")
    assert report["performance"]["filled_count"] == n
    assert report["classification"]["classification"] == "BROKEN"
    assert report["performance"]["net_pnl_dollars"] < 0


def test_large_winning_sample_classifies_validated(tmp_path):
    rows = []
    n = STRATEGY_MIN_EXAMPLES + 5
    for day in range(n):
        ts = f"{_day(day)}T10:00:00+00:00"
        outcome_ts = f"{_day(day)}T11:00:00+00:00"
        rows.append(_trade(ts, "MNQ", "orb_breakout"))
        # 3:1 win:loss ratio in dollar terms, well above MIN_PROFIT_FACTOR.
        result = "LOSS" if day % 4 == 3 else "WIN"
        pnl = -20.0 if result == "LOSS" else 60.0
        rows.append(_outcome(outcome_ts, "MNQ", result=result, pnl=pnl))
    _write_jsonl(tmp_path / "journal_2026-06-01.jsonl", rows)

    report = build_promotion_report(journal_dir=tmp_path, strategy="orb_breakout", instrument="MNQ")
    assert report["performance"]["filled_count"] >= STRATEGY_MIN_EXAMPLES
    assert report["performance"]["profit_factor"] >= MIN_PROFIT_FACTOR
    assert report["classification"]["classification"] == "VALIDATED"


def test_accounting_identity_holds_with_open_position(tmp_path):
    rows = [
        _trade("2026-06-01T10:00:00+00:00", "MNQ", "orb_breakout"),
        _outcome("2026-06-01T11:00:00+00:00", "MNQ", result="WIN", pnl=50.0),
        # Second decision with no OUTCOME yet -> legitimately open.
        _trade("2026-06-02T10:00:00+00:00", "MNQ", "orb_breakout"),
    ]
    _write_jsonl(tmp_path / "journal_2026-06-01.jsonl", rows)

    report = build_promotion_report(journal_dir=tmp_path, strategy="orb_breakout", instrument="MNQ")
    exe = report["execution"]
    assert exe["legitimately_open"] == 1
    assert exe["candidates_approved"] == 2
    assert exe["accounting_identity_holds"] is True


def test_research_evidence_low_parity_flags_overfit(tmp_path):
    rows = []
    n = STRATEGY_MIN_EXAMPLES + 5
    for day in range(n):
        ts = f"{_day(day)}T10:00:00+00:00"
        outcome_ts = f"{_day(day)}T11:00:00+00:00"
        rows.append(_trade(ts, "MNQ", "orb_breakout"))
        result = "LOSS" if day % 4 == 3 else "WIN"
        pnl = -20.0 if result == "LOSS" else 60.0
        rows.append(_outcome(outcome_ts, "MNQ", result=result, pnl=pnl))
    _write_jsonl(tmp_path / "journal_2026-06-01.jsonl", rows)

    research_path = tmp_path / "research_evidence.json"
    research_path.write_text(json.dumps({"raw_candidate_count": 100_000}), encoding="utf-8")

    report = build_promotion_report(
        journal_dir=tmp_path, strategy="orb_breakout", instrument="MNQ",
        research_evidence_path=research_path,
    )
    assert report["classification"]["classification"] == "OVERFIT"
    assert report["performance"]["parity_survival_ratio"] < 0.05


def test_missing_research_evidence_file_reported_not_invented(tmp_path):
    report = build_promotion_report(
        journal_dir=tmp_path, strategy="orb_breakout",
        research_evidence_path=tmp_path / "does_not_exist.json",
    )
    assert report["identity_parity"]["research_evidence"]["provided"] is False
    assert report["identity_parity"]["raw_candidate_count"].startswith("UNKNOWN")
