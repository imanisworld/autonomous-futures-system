from __future__ import annotations

import json
import subprocess
from pathlib import Path

from ops.promotion_gate import CANONICAL_EVIDENCE_REGISTRY, build_promotion_report
from ops.session_snapshot import (
    build_precommit_report,
    build_session_start_report,
    git_repo_report,
)
from ops.daily_reconciliation import build_trade_chain_report
from datetime import date


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.check_call(["git", "init", "-b", "main"], cwd=repo)
    subprocess.check_call(["git", "config", "user.email", "test@example.com"], cwd=repo)
    subprocess.check_call(["git", "config", "user.name", "Test User"], cwd=repo)
    (repo / "risk_rules.yaml").write_text("trading_mode:\n  live_trading_enabled: false\n", encoding="utf-8")
    (repo / "logs").mkdir()
    subprocess.check_call(["git", "add", "risk_rules.yaml"], cwd=repo)
    subprocess.check_call(["git", "commit", "-m", "init"], cwd=repo)
    return repo


# ─────────────────────────────────────────────────── session_snapshot


def test_git_repo_report_is_read_only_and_reports_current_branch(tmp_path):
    repo = _init_repo(tmp_path)
    report = git_repo_report(repo)
    assert report["current_branch"] == "main"
    assert report["dirty_tracked_files"] == []
    assert report["untracked_files"] == []
    assert report["open_prs"].startswith("UNKNOWN")
    # no mutation happened
    assert _git(repo, "status", "--porcelain") == ""


def test_precommit_fails_closed_without_session_start_checkpoint(tmp_path):
    repo = _init_repo(tmp_path)
    report = build_precommit_report(repo, log_dir=str(repo / "logs"))
    assert report["verdict"] == "FAIL_CLOSED"
    assert any("checkpoint" in v for v in report["violations"])


def test_session_start_then_precommit_passes_on_same_branch(tmp_path):
    repo = _init_repo(tmp_path)
    log_dir = str(repo / "logs")
    build_session_start_report(repo, log_dir=log_dir)
    report = build_precommit_report(repo, log_dir=log_dir)
    assert report["verdict"] == "PASS"
    assert report["violations"] == []


def test_precommit_fails_closed_on_branch_change(tmp_path):
    repo = _init_repo(tmp_path)
    log_dir = str(repo / "logs")
    build_session_start_report(repo, log_dir=log_dir)
    subprocess.check_call(["git", "checkout", "-b", "other-branch"], cwd=repo)
    report = build_precommit_report(repo, log_dir=log_dir)
    assert report["verdict"] == "FAIL_CLOSED"
    assert any("branch differs" in v for v in report["violations"])


def test_precommit_never_touches_git_state(tmp_path):
    repo = _init_repo(tmp_path)
    log_dir = str(repo / "logs")
    build_session_start_report(repo, log_dir=log_dir)
    head_before = _git(repo, "rev-parse", "HEAD")
    build_precommit_report(repo, log_dir=log_dir)
    assert _git(repo, "rev-parse", "HEAD") == head_before
    status = _git(repo, "status", "--porcelain")
    assert status == "" or "logs/" in status


# ─────────────────────────────────────────────────── promotion_gate


def test_promotion_report_blocked_for_unregistered_strategy():
    report = build_promotion_report(
        "not_a_real_strategy", repo_root=Path(__file__).resolve().parents[1]
    )
    assert report["classification"] == "BLOCKED"
    assert "not_a_real_strategy" not in CANONICAL_EVIDENCE_REGISTRY
    assert set(report["known_strategies"]) == set(CANONICAL_EVIDENCE_REGISTRY)


def test_promotion_report_never_declares_validated():
    """The gate must never itself assert a strategy is safe to promote."""
    repo_root = Path(__file__).resolve().parents[1]
    for strategy in CANONICAL_EVIDENCE_REGISTRY:
        report = build_promotion_report(strategy, repo_root=repo_root)
        assert report["classification"] != "VALIDATED"
        assert "research_result" in report or report["classification"] == "BLOCKED"


# ─────────────────────────────────────────────────── daily_reconciliation trade chain


def _trade(ts: str, *, strategy: str = "orb_breakout", approved: bool = True) -> dict:
    return {
        "ts": ts,
        "type": "DECISION",
        "instrument": "MNQ",
        "decision": "TRADE",
        "risk_check": {"result": "APPROVED" if approved else "REJECTED"},
        "setup": {"strategy": strategy, "direction": "LONG", "entry": 100.0, "stop": 99.0, "target": 102.0},
    }


def _outcome(ts: str, *, result: str = "WIN", pnl: float = 10.0) -> dict:
    return {
        "ts": ts,
        "type": "OUTCOME",
        "instrument": "MNQ",
        "outcome": {"result": result, "pnl_dollars": pnl, "exit_reason": "target"},
    }


def _order_ids(ts: str) -> dict:
    return {"ts": ts, "type": "ORDER_IDS", "instrument": "MNQ", "order_ids": {"entry": "12345678"}}


def _write_journal(log_dir: Path, day: str, rows: list[dict]) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / f"journal_{day}.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def test_trade_chain_accounting_identity_holds_for_simple_day(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    log_dir = repo / "logs"
    day = "2026-08-01"
    rows = [
        _trade(f"{day}T10:00:00"),
        _order_ids(f"{day}T10:00:01"),
        _outcome(f"{day}T10:05:00", result="WIN", pnl=10.0),
        _trade(f"{day}T11:00:00"),
        _outcome(f"{day}T11:05:00", result="CANCELLED", pnl=0.0),
    ]
    _write_journal(log_dir, day, rows)

    report = build_trade_chain_report(repo, str(log_dir), date.fromisoformat(day), date.fromisoformat(day))
    acc = report["detail"]["accounting"]
    assert acc["attempts"] == 2
    assert acc["resolved_fills"] == 1
    assert acc["no_fills_cancellations"] == 1
    assert acc["orphans_no_outcome_not_current_open"] == 0
    assert acc["identity_holds"] is True
    assert report["verdict"] == "PASS"
    assert report["blockers"] == []


def test_trade_chain_flags_orphan_trade_with_no_outcome(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    log_dir = repo / "logs"
    day = "2026-08-01"
    # A TRADE with no OUTCOME, immediately followed by a second TRADE+OUTCOME —
    # the first is not the day's currently-open position, so it's an orphan.
    rows = [
        _trade(f"{day}T09:00:00"),
        _trade(f"{day}T10:00:00"),
        _outcome(f"{day}T10:05:00", result="WIN", pnl=5.0),
    ]
    _write_journal(log_dir, day, rows)

    report = build_trade_chain_report(repo, str(log_dir), date.fromisoformat(day), date.fromisoformat(day))
    assert report["verdict"] == "FAIL"
    assert report["orphans"] == 1
    assert any("orphan" in b for b in report["blockers"])


def test_trade_chain_treats_current_open_position_as_legitimate(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    log_dir = repo / "logs"
    day = "2026-08-01"
    rows = [_trade(f"{day}T15:55:00")]  # last row, no outcome yet: legitimately open
    _write_journal(log_dir, day, rows)

    report = build_trade_chain_report(repo, str(log_dir), date.fromisoformat(day), date.fromisoformat(day))
    assert report["orphans"] == 0
    assert report["legitimate_opens"] == 1
    assert report["verdict"] == "PASS"


def test_trade_chain_empty_window_is_a_clean_pass(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    log_dir = repo / "logs"
    report = build_trade_chain_report(repo, str(log_dir), date(2026, 1, 1), date(2026, 1, 1))
    assert report["verdict"] == "PASS"
    assert report["attempts"] == 0
