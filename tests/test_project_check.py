from __future__ import annotations

import json
import subprocess
from pathlib import Path

from ops import project_check as pc


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "a.txt").write_text("1\n")
    subprocess.run(["git", "add", "a.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    subprocess.run(["git", "branch", "-M", "main"], cwd=repo, check=True)
    return repo


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def _trade_row(ts: str, instrument: str = "MNQ") -> dict:
    return {
        "ts": ts, "instrument": instrument, "decision": "TRADE",
        "risk_check": {"result": "APPROVED"},
        "setup": {"direction": "LONG", "strategy": "orb_breakout", "entry": 100.0,
                  "stop": 99.0, "target": 103.0, "contracts": 1},
    }


def _outcome_row(ts: str, instrument: str = "MNQ", result: str = "WIN", pnl=50.0) -> dict:
    return {
        "ts": ts, "instrument": instrument, "type": "OUTCOME",
        "outcome": {"result": result, "exit_reason": "target_hit", "pnl_dollars": pnl, "contracts": 1},
    }


# ── git status parsing (regression test for the leading-space bug) ────────

def test_classify_status_preserves_leading_space():
    lines = [" M ops/behavior_neutral_gate.py", "?? ops/project_check.py", "M  scripts/foo.py"]
    result = pc.classify_status(lines)
    assert result["dirty_tracked"] == ["ops/behavior_neutral_gate.py"]
    assert result["untracked"] == ["ops/project_check.py"]
    assert result["staged"] == ["scripts/foo.py"]


def test_git_status_porcelain_does_not_eat_leading_space(tmp_path):
    repo = _init_repo(tmp_path)
    (repo / "a.txt").write_text("2\n")  # modified, not staged
    lines, err = pc.status_porcelain(repo)
    assert err is None
    assert lines == [" M a.txt"]


# ── core git primitives ────────────────────────────────────────────────────

def test_current_branch_and_head_sha(tmp_path):
    repo = _init_repo(tmp_path)
    assert pc.current_branch(repo) == "main"
    assert len(pc.head_sha(repo)) == 40


def test_local_main_relationship_unknown_without_remote(tmp_path):
    repo = _init_repo(tmp_path)
    rel = pc.local_main_relationship(repo)
    assert rel["status"] == pc.UNKNOWN


# ── session-start -> precommit fail-closed behavior ────────────────────────

def test_session_start_then_precommit_pass(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    monkeypatch.setenv("PROJECT_CHECK_STATE_DIR", str(tmp_path / "state"))
    report = pc.session_start_report(repo)
    pc.save_session_state(repo, report)
    pre = pc.precommit_report(repo)
    assert pre["status"] == "PASS"
    assert pre["reasons"] == []


def test_precommit_fail_closed_without_baseline(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    monkeypatch.setenv("PROJECT_CHECK_STATE_DIR", str(tmp_path / "empty_state"))
    pre = pc.precommit_report(repo)
    assert pre["status"] == "FAIL_CLOSED"
    assert any("session-start state not found" in r for r in pre["reasons"])


def test_precommit_fail_closed_on_branch_change(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    monkeypatch.setenv("PROJECT_CHECK_STATE_DIR", str(tmp_path / "state2"))
    report = pc.session_start_report(repo)
    pc.save_session_state(repo, report)
    subprocess.run(["git", "checkout", "-b", "feature"], cwd=repo, check=True, capture_output=True)
    pre = pc.precommit_report(repo)
    assert pre["status"] == "FAIL_CLOSED"
    assert any("branch differs" in r for r in pre["reasons"])


def test_precommit_never_mutates_repo(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    monkeypatch.setenv("PROJECT_CHECK_STATE_DIR", str(tmp_path / "state3"))
    before = pc.head_sha(repo)
    pc.precommit_report(repo)  # no session-start first -- still must not touch the repo
    assert pc.head_sha(repo) == before
    assert pc.current_branch(repo) == "main"


# ── evidence preservation heuristic ────────────────────────────────────────

def test_evidence_preservation_flags_then_clears_on_archive_tag(tmp_path):
    repo = _init_repo(tmp_path)
    subprocess.run(["git", "checkout", "-b", "claude/some-feature"], cwd=repo, check=True, capture_output=True)
    (repo / "b.txt").write_text("x\n")
    subprocess.run(["git", "add", "b.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "feature work"], cwd=repo, check=True)
    subprocess.run(["git", "checkout", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "update-ref", "refs/remotes/origin/main", "main"], cwd=repo, check=True)
    subprocess.run(["git", "update-ref", "refs/remotes/origin/claude/some-feature", "claude/some-feature"], cwd=repo, check=True)

    before = pc.evidence_preservation_candidates(repo)
    assert before["checked"] is True
    flagged = [c["branch"] for c in before["candidates_missing_archive_tag"]]
    assert "origin/claude/some-feature" in flagged

    subprocess.run(
        ["git", "tag", "-a", "archive/some-feature-2026-01-01", "claude/some-feature", "-m", "archive"],
        cwd=repo, check=True,
    )
    after = pc.evidence_preservation_candidates(repo)
    flagged_after = [c["branch"] for c in after["candidates_missing_archive_tag"]]
    assert "origin/claude/some-feature" not in flagged_after


def test_evidence_preservation_excludes_branches_with_open_pr(tmp_path):
    repo = _init_repo(tmp_path)
    subprocess.run(["git", "checkout", "-b", "claude/under-review"], cwd=repo, check=True, capture_output=True)
    (repo / "b.txt").write_text("x\n")
    subprocess.run(["git", "add", "b.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "work"], cwd=repo, check=True)
    subprocess.run(["git", "checkout", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "update-ref", "refs/remotes/origin/main", "main"], cwd=repo, check=True)
    subprocess.run(["git", "update-ref", "refs/remotes/origin/claude/under-review", "claude/under-review"], cwd=repo, check=True)

    without_filter = pc.evidence_preservation_candidates(repo)
    assert "origin/claude/under-review" in [c["branch"] for c in without_filter["candidates_missing_archive_tag"]]

    with_filter = pc.evidence_preservation_candidates(repo, open_pr_head_branches={"claude/under-review"})
    assert "origin/claude/under-review" not in [c["branch"] for c in with_filter["candidates_missing_archive_tag"]]
    assert "origin/claude/under-review" in with_filter["excluded_branches_with_open_pr"]


# ── strategy inventory parsing / drift matching ────────────────────────────

def test_strategy_inventory_table_parses_master_table_only(tmp_path):
    md = tmp_path / "Strategy_Inventory.md"
    md.write_text(
        "# STRATEGY INVENTORY\n\n"
        "## Master Table\n\n"
        "| Strategy | Rules | Verdict |\n"
        "|---|---|---|\n"
        "| ORB Reclaim (MES) | OK | **PAPER PROOF** |\n"
        "| Foo Bar | OK | **WAIT — build detector** |\n\n"
        "## Detailed Strategy Profiles\n"
        "| Strategy | Verdict |\n|---|---|\n| Not In Master | **VALIDATED** |\n",
        encoding="utf-8",
    )
    rows = pc.strategy_inventory_table(md)
    assert [r["strategy"] for r in rows] == ["ORB Reclaim (MES)", "Foo Bar"]
    assert rows[0]["verdict"] == "PAPER PROOF"
    assert rows[1]["verdict"] == "WAIT"


def test_normalize_strategy_key_strips_parenthetical_instrument():
    assert pc._normalize_strategy_key("ORB Reclaim (MES)") == "orb_reclaim"
    assert pc._normalize_strategy_key("ORB Breakout (MNQ)") == "orb_breakout"


# ── promotion gate: accounting / gate-attrition / classification ──────────

def test_execution_accounting_basic(tmp_path):
    journal_dir = tmp_path / "logs"
    journal_dir.mkdir()
    rows = [
        _trade_row("2026-01-01T10:00:00Z"),
        _outcome_row("2026-01-01T10:05:00Z", result="WIN", pnl=50.0),
        _trade_row("2026-01-01T11:00:00Z"),
        _outcome_row("2026-01-01T11:05:00Z", result="CANCELLED", pnl=None),
        _trade_row("2026-01-01T12:00:00Z"),  # left open -- no matching OUTCOME
    ]
    _write_jsonl(journal_dir / "journal_2026-01-01.jsonl", rows)
    entries = pc._entries_from_dir(journal_dir)
    accounting, summaries = pc._execution_accounting(entries, ["MNQ"])
    assert accounting["entry_attempts"] == 3
    assert accounting["fills"] == 1
    assert accounting["cancellations_or_known_no_fills"] == 1
    assert accounting["legitimately_open"] == 1
    assert accounting["unmatched_outcomes"] == 0
    assert accounting["accounting_identity_check"] == "PASS"
    assert len(summaries) == 2


def test_execution_accounting_flags_unmatched_outcome(tmp_path):
    journal_dir = tmp_path / "logs"
    journal_dir.mkdir()
    rows = [_outcome_row("2026-01-01T10:05:00Z", result="WIN", pnl=50.0)]  # no TRADE at all
    _write_jsonl(journal_dir / "journal_2026-01-01.jsonl", rows)
    entries = pc._entries_from_dir(journal_dir)
    accounting, _ = pc._execution_accounting(entries, ["MNQ"])
    assert accounting["unmatched_outcomes"] == 1
    assert accounting["accounting_identity_check"] != "PASS"


def test_gate_attrition_counts_failed_gates_and_risk_rejections():
    rows = [
        {"decision": "NO_TRADE", "failed_gates": ["MARKET_CONDITION_NOT_TRADABLE"]},
        {"decision": "NO_TRADE", "failed_gates": ["MARKET_CONDITION_NOT_TRADABLE", "WEAK_BAR_CLOSE"]},
        {"decision": "RISK_REJECTED", "risk_check": {"result": "REJECTED", "failed_rule": "max_daily_loss"}},
    ]
    result = pc._gate_attrition(rows)
    assert result["decision_type_counts"] == {"NO_TRADE": 2, "RISK_REJECTED": 1}
    assert result["pre_risk_engine_gate_rejections"]["MARKET_CONDITION_NOT_TRADABLE"] == 2
    assert result["risk_engine_rejections_by_rule"]["max_daily_loss"] == 1


def test_classify_promotion_wait_on_zero_attempts():
    accounting = {"entry_attempts": 0, "fills": 0, "accounting_identity_check": "PASS"}
    result = pc._classify_promotion(accounting, {"note": "no resolved"}, "no baseline")
    assert result["classification"] == "WAIT"


def test_classify_promotion_broken_on_zero_fills():
    accounting = {"entry_attempts": 5, "fills": 0, "accounting_identity_check": "PASS"}
    result = pc._classify_promotion(accounting, {"note": "no resolved"}, "no baseline")
    assert result["classification"] == "BROKEN"
    assert "zero executable fills" in result["reason"]


def test_classify_promotion_unsafe_on_accounting_mismatch():
    accounting = {"entry_attempts": 5, "fills": 3, "accounting_identity_check": "MISMATCH -- reconcile first"}
    result = pc._classify_promotion(accounting, {"sample_size": 3}, "no baseline")
    assert result["classification"] == "UNSAFE"


def test_classify_promotion_capped_at_promising_never_validated():
    accounting = {"entry_attempts": 100, "fills": 90, "accounting_identity_check": "PASS"}
    performance = {"sample_size": 90, "profit_factor": 2.5, "expectancy_dollars": 20.0}
    result = pc._classify_promotion(accounting, performance, "no baseline")
    assert result["classification"] == "PROMISING BUT UNPROVEN"
    assert "VALIDATED requires actual paper-forward evidence" in result["reason"]


def test_classify_promotion_broken_on_negative_expectancy():
    accounting = {"entry_attempts": 100, "fills": 90, "accounting_identity_check": "PASS"}
    performance = {"sample_size": 90, "profit_factor": 0.7, "expectancy_dollars": -5.0}
    result = pc._classify_promotion(accounting, performance, "no baseline")
    assert result["classification"] == "BROKEN"


def test_promotion_gate_reports_wait_without_candles(tmp_path):
    report = pc.promotion_gate_report(tmp_path, strategy="orb_breakout", candle_paths=[])
    assert report["classification"]["classification"] == "WAIT"
    assert "no --candles corpus" in report["classification"]["reason"]


# ── daily: trade chain integrity on an empty day ──────────────────────────

def test_trade_chain_integrity_pass_on_no_journal(tmp_path):
    from datetime import date
    journal_dir = tmp_path / "logs"
    journal_dir.mkdir()
    result = pc.trade_chain_integrity(tmp_path, journal_dir, date(2026, 1, 1))
    assert result["overall"] == "PASS"


def test_trade_chain_integrity_fails_on_unmatched_outcome(tmp_path):
    from datetime import date
    journal_dir = tmp_path / "logs"
    journal_dir.mkdir()
    _write_jsonl(
        journal_dir / "journal_2026-01-01.jsonl",
        [_outcome_row("2026-01-01T10:05:00Z", result="WIN", pnl=50.0)],
    )
    result = pc.trade_chain_integrity(tmp_path, journal_dir, date(2026, 1, 1))
    assert result["overall"] == "FAIL"
    assert result["orphans_unmatched_outcomes"] == 1
