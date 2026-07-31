from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ops.daily_reconciliation import build_daily_report, _parse_strategy_inventory
from ops.live_box_guard import PROOF_CRITICAL_RUNTIME_OVERRIDES


@pytest.fixture(autouse=True)
def _isolate_runtime_env(tmp_path_factory, monkeypatch):
    for name in PROOF_CRITICAL_RUNTIME_OVERRIDES:
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(f"EXPECTED_PROOF_{name}", raising=False)
    monkeypatch.delenv("ENABLE_MANUAL_EXECUTION_CONTROLS", raising=False)
    monkeypatch.setenv("WEBHOOK_SECRET", "test-primary")
    monkeypatch.setenv("TRADINGVIEW_WEBHOOK_SECRET", "test-rotation")

    # Deterministically make `gh` unavailable while keeping `git` on PATH,
    # regardless of whether the CI box happens to have gh installed.
    git_path = shutil.which("git")
    assert git_path, "git must be on PATH to run these tests"
    binstub_dir = tmp_path_factory.mktemp("binstub")
    (binstub_dir / "git").symlink_to(git_path)
    monkeypatch.setenv("PATH", str(binstub_dir))


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.check_call(["git", "init", "-q", "-b", "main"], cwd=repo)
    subprocess.check_call(["git", "config", "user.email", "test@example.com"], cwd=repo)
    subprocess.check_call(["git", "config", "user.name", "Test User"], cwd=repo)
    (repo / "risk_rules.yaml").write_text("trading_mode:\n  live_trading_enabled: false\n", encoding="utf-8")
    (repo / "logs").mkdir()
    (repo / "docs" / "strategy-rules").mkdir(parents=True)
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    subprocess.check_call(["git", "add", "."], cwd=repo)
    subprocess.check_call(["git", "commit", "-q", "-m", "initial"], cwd=repo)
    return repo


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def test_trade_chain_pass_with_no_activity(tmp_path):
    repo = _init_repo(tmp_path)
    report = build_daily_report(repo_root=repo, journal_dir=repo / "logs", log_dir=repo / "logs")
    chain = report["trade_chain_integrity"]
    assert chain["verdict"] == "PASS"
    assert chain["totals"]["attempts"] == 0


def test_trade_chain_flags_orphan_outcome(tmp_path):
    repo = _init_repo(tmp_path)
    today = _today()
    orphan_outcome = {
        "ts": f"{today}T11:00:00+00:00",
        "type": "OUTCOME",
        "instrument": "MNQ",
        "outcome": {"result": "WIN", "exit_reason": "target hit", "pnl_dollars": 50.0, "contracts": 1},
    }
    _write_jsonl(repo / "logs" / f"journal_{today}.jsonl", [orphan_outcome])

    report = build_daily_report(repo_root=repo, journal_dir=repo / "logs", log_dir=repo / "logs")
    chain = report["trade_chain_integrity"]
    assert chain["verdict"] == "REVIEW"
    assert chain["per_instrument"]["MNQ"]["orphan_outcomes"]


def test_trade_chain_accounts_for_resolved_and_open(tmp_path):
    repo = _init_repo(tmp_path)
    today = _today()
    rows = [
        {
            "ts": f"{today}T10:00:00+00:00", "instrument": "MNQ", "decision": "TRADE",
            "risk_check": {"result": "APPROVED"},
            "setup": {"direction": "LONG", "strategy": "orb_breakout", "entry": 100.0, "stop": 95.0, "target": 110.0, "contracts": 1},
        },
        {
            "ts": f"{today}T10:30:00+00:00", "type": "OUTCOME", "instrument": "MNQ",
            "outcome": {"result": "WIN", "exit_reason": "target hit", "pnl_dollars": 40.0, "contracts": 1},
        },
        {
            "ts": f"{today}T11:00:00+00:00", "instrument": "MNQ", "decision": "TRADE",
            "risk_check": {"result": "APPROVED"},
            "setup": {"direction": "SHORT", "strategy": "orb_breakout", "entry": 100.0, "stop": 105.0, "target": 90.0, "contracts": 1},
        },
    ]
    _write_jsonl(repo / "logs" / f"journal_{today}.jsonl", rows)

    report = build_daily_report(repo_root=repo, journal_dir=repo / "logs", log_dir=repo / "logs")
    mnq = report["trade_chain_integrity"]["per_instrument"]["MNQ"]
    assert mnq["attempts"] == 2
    assert mnq["fills"] == 1
    assert mnq["resolved"] == 1
    assert mnq["legitimately_open"] == 1
    assert mnq["accounting_identity_holds"] is True
    assert report["trade_chain_integrity"]["verdict"] == "PASS"


def test_parse_strategy_inventory_extracts_verdicts(tmp_path):
    inventory = tmp_path / "Strategy_Inventory.md"
    inventory.write_text(
        "\n".join([
            "# STRATEGY INVENTORY",
            "",
            "| Strategy | Rules | Verdict |",
            "|---|---|---|",
            "| ORB Reclaim (MES) | ✅ | **PAPER PROOF** |",
            "| 12HR Miyagi | ✅ | **PROMISING BUT UNPROVEN** |",
        ]),
        encoding="utf-8",
    )
    rows = _parse_strategy_inventory(inventory)
    names = {r["strategy"]: r["verdict"] for r in rows}
    assert names["ORB Reclaim (MES)"] == "PAPER PROOF"
    assert names["12HR Miyagi"] == "PROMISING BUT UNPROVEN"


def test_evidence_preservation_reports_unavailable_without_gh(tmp_path):
    repo = _init_repo(tmp_path)
    report = build_daily_report(repo_root=repo, journal_dir=repo / "logs", log_dir=repo / "logs")
    assert report["github"]["available"] is False
    assert report["evidence_preservation"]["available"] is False


def test_daily_report_never_mutates_repo(tmp_path):
    repo = _init_repo(tmp_path)
    before = subprocess.check_output(["git", "status", "--porcelain"], cwd=repo, text=True)
    build_daily_report(repo_root=repo, journal_dir=repo / "logs", log_dir=repo / "logs")
    after = subprocess.check_output(["git", "status", "--porcelain"], cwd=repo, text=True)
    assert before == after
