from __future__ import annotations

import json
import subprocess
from datetime import date
from pathlib import Path

from ops.project_check_daily import (
    _normalize_strategy_name,
    _trade_chain,
    build_daily_report,
)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def _trade(ts: str, *, instrument: str = "MNQ", strategy: str = "orb_breakout", risk: str = "APPROVED") -> dict:
    row = {
        "ts": ts,
        "instrument": instrument,
        "decision": "TRADE",
        "risk_check": {"result": risk},
        "setup": {"strategy": strategy, "direction": "LONG"},
    }
    if risk == "REJECTED":
        row["risk_check"]["reason"] = "daily_loss_limit"
    return row


def _order_ids(ts: str, *, instrument: str = "MNQ", entry="1", target="2", stop="3") -> dict:
    return {
        "ts": ts,
        "type": "ORDER_IDS",
        "instrument": instrument,
        "session": "new_york",
        "order_ids": {"entry": entry, "target": target, "stop": stop},
    }


def _outcome(ts: str, *, result: str, instrument: str = "MNQ", pnl: float | None = None, no_fill_reason: str | None = None) -> dict:
    body = {"result": result, "exit_reason": "target_hit" if result == "WIN" else "execution_failed:CANCELLED"}
    if pnl is not None:
        body["pnl_dollars"] = pnl
    if no_fill_reason:
        body["no_fill_reason"] = no_fill_reason
    return {"ts": ts, "type": "OUTCOME", "instrument": instrument, "outcome": body}


def test_normalize_strategy_name_strips_instrument_suffix() -> None:
    assert _normalize_strategy_name("ORB Breakout (MNQ)") == "orbbreakout"
    assert _normalize_strategy_name("orb_breakout") == "orbbreakout"


def test_trade_chain_passes_on_clean_fill_and_cancel() -> None:
    entries = [
        _trade("2026-08-01T14:00:00Z"),
        _order_ids("2026-08-01T14:00:05Z", entry="e1", target="t1", stop="s1"),
        _outcome("2026-08-01T14:30:00Z", result="WIN", pnl=100),
        _trade("2026-08-01T15:00:00Z"),
        _order_ids("2026-08-01T15:00:05Z", entry="e2", target="t2", stop="s2"),
        _outcome("2026-08-01T15:30:00Z", result="CANCELLED", no_fill_reason="NO_FILL_PRICE_MOVED_AWAY"),
    ]
    chain = _trade_chain(entries, date(2026, 8, 2), api_base=None)
    assert chain["pass"] is True
    assert chain["fills"] == 1
    assert chain["cancellations"] == 1
    assert chain["orphans"] == []
    assert chain["naked_or_unverified_brackets"] == []
    for identity in chain["accounting_identities"]:
        if "matches" in identity:
            assert identity["matches"] is True


def test_trade_chain_flags_naked_fill_missing_bracket() -> None:
    entries = [
        _trade("2026-08-01T14:00:00Z"),
        _outcome("2026-08-01T14:30:00Z", result="WIN", pnl=100),
    ]
    chain = _trade_chain(entries, date(2026, 8, 2), api_base=None)
    assert chain["pass"] is False
    assert len(chain["naked_or_unverified_brackets"]) == 1
    assert any("bracket" in p.lower() for p in chain["problems"])


def test_trade_chain_flags_orphan_from_prior_day() -> None:
    entries = [_trade("2020-01-01T14:00:00Z")]
    chain = _trade_chain(entries, date(2026, 8, 2), api_base=None)
    assert chain["pass"] is False
    assert len(chain["orphans"]) == 1


def test_trade_chain_flags_duplicate_order_ids() -> None:
    entries = [
        _trade("2026-08-01T14:00:00Z"),
        _order_ids("2026-08-01T14:00:05Z", entry="dup", target="t1", stop="s1"),
        _outcome("2026-08-01T14:30:00Z", result="WIN", pnl=100),
        _trade("2026-08-01T15:00:00Z"),
        _order_ids("2026-08-01T15:00:05Z", entry="dup", target="t2", stop="s2"),
        _outcome("2026-08-01T15:30:00Z", result="WIN", pnl=50),
    ]
    chain = _trade_chain(entries, date(2026, 8, 2), api_base=None)
    assert chain["duplicate_order_ids"] == {"dup": 2}
    assert chain["pass"] is False


def test_trade_chain_flags_rejected_candidate_without_reason() -> None:
    entries = [{"ts": "2026-08-01T14:00:00Z", "instrument": "MNQ", "decision": "TRADE", "risk_check": {"result": "REJECTED"}, "setup": {"strategy": "orb_breakout"}}]
    chain = _trade_chain(entries, date(2026, 8, 2), api_base=None)
    assert len(chain["rejected_with_no_reason"]) == 1
    assert chain["pass"] is False


def _init_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
    (root / "a.txt").write_text("1\n")
    subprocess.run(["git", "add", "a.txt"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True)


def test_build_daily_report_end_to_end_writes_only_checkpoint(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    _write_jsonl(
        log_dir / "journal_2026-08-01.jsonl",
        [
            _trade("2026-08-01T14:00:00Z"),
            _order_ids("2026-08-01T14:00:05Z"),
            _outcome("2026-08-01T14:30:00Z", result="WIN", pnl=100),
        ],
    )
    report = build_daily_report(cwd=str(tmp_path), log_dir=log_dir, since="2026-08-01")
    assert report["trade_chain"]["fills"] == 1
    assert "checkpoint_written" in report

    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=tmp_path, capture_output=True, text=True, check=True
    )
    assert "a.txt" not in status.stdout


def test_build_daily_report_never_mutates_journal(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    journal_path = log_dir / "journal_2026-08-01.jsonl"
    _write_jsonl(journal_path, [_trade("2026-08-01T14:00:00Z")])
    before = journal_path.read_text()
    build_daily_report(cwd=str(tmp_path), log_dir=log_dir, since="2026-08-01", update_checkpoint=False)
    after = journal_path.read_text()
    assert before == after
