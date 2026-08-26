from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ops.project_check.daily import _normalize, _parse_strategy_inventory, _strategy_source_of_truth, build_daily_report


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    (root / "a.txt").write_text("one\n")
    _git(root, "add", "a.txt")
    _git(root, "commit", "-q", "-m", "initial")
    return root


def test_normalize_strips_instrument_suffix_and_punctuation() -> None:
    assert _normalize("ORB Reclaim (MES)") == "orb reclaim"
    assert _normalize("4HR Re-Trigger") == "4hr re-trigger"


def test_parse_strategy_inventory_missing_file(tmp_path: Path) -> None:
    rows, error = _parse_strategy_inventory(tmp_path / "nope.md")
    assert rows == []
    assert "not found" in error


def test_parse_strategy_inventory_extracts_name_and_verdict(tmp_path: Path) -> None:
    doc = tmp_path / "Strategy_Inventory.md"
    doc.write_text(
        "\n".join(
            [
                "# STRATEGY INVENTORY",
                "",
                "## Master Table",
                "",
                "| Strategy | Rules | Verdict |",
                "|---|---|---|",
                "| ORB Reclaim (MES) | ✅ | **PAPER PROOF** |",
                "| ORB Breakout (MNQ) | ✅ | **WAIT** |",
                "",
                "## Detailed Strategy Profiles",
                "some other content with | pipes | that must not be parsed |",
            ]
        ),
        encoding="utf-8",
    )
    rows, error = _parse_strategy_inventory(doc)
    assert error is None
    assert len(rows) == 2
    by_name = {r["name"]: r["verdict"] for r in rows}
    assert by_name["ORB Reclaim (MES)"] == "PAPER PROOF"
    assert by_name["ORB Breakout (MNQ)"] == "WAIT"


def test_strategy_source_of_truth_flags_active_in_docs_but_disabled_in_config(tmp_path: Path) -> None:
    inventory_dir = tmp_path / "docs" / "strategy-rules"
    inventory_dir.mkdir(parents=True)
    (inventory_dir / "Strategy_Inventory.md").write_text(
        "\n".join(
            [
                "## Master Table",
                "",
                "| Strategy | Verdict |",
                "|---|---|",
                "| ORB Reclaim (MES) | **PAPER PROOF** |",
                "| ORB Breakout (MNQ) | **WAIT** |",
            ]
        ),
        encoding="utf-8",
    )
    lanes = {"active_lane_summary": {"MNQ": ["orb_breakout"]}}  # orb_reclaim not active anywhere
    result = _strategy_source_of_truth(repo_root=tmp_path, rules_active_lanes=lanes)
    assert result["checked"] is True
    issues = {f["strategy"]: f["issue"] for f in result["drift_findings"]}
    assert "described as active" in issues["ORB Reclaim (MES)"]
    assert "described as BROKEN/RETIRE/WAIT" in issues["ORB Breakout (MNQ)"]


def test_strategy_source_of_truth_unmatched_rows_reported_not_dropped(tmp_path: Path) -> None:
    inventory_dir = tmp_path / "docs" / "strategy-rules"
    inventory_dir.mkdir(parents=True)
    (inventory_dir / "Strategy_Inventory.md").write_text(
        "\n".join(
            [
                "## Master Table",
                "",
                "| Strategy | Verdict |",
                "|---|---|",
                "| 12HR Miyagi | **PROMISING BUT UNPROVEN** |",
            ]
        ),
        encoding="utf-8",
    )
    result = _strategy_source_of_truth(repo_root=tmp_path, rules_active_lanes={"active_lane_summary": {}})
    assert result["drift_findings"] == []
    assert len(result["unmatched_inventory_rows"]) == 1
    assert result["unmatched_inventory_rows"][0]["name"] == "12HR Miyagi"


def test_build_daily_report_smoke(repo: Path) -> None:
    (repo / "logs").mkdir()
    report = build_daily_report(repo_root=repo, journal_dir="logs", use_checkpoint=False, advance_checkpoint=False)
    assert report["ok"] is True
    assert report["repo_reconciliation"]["current_branch"] == "main"
    assert report["trade_chain"]["status"] == "PASS"
    assert report["trade_chain"]["summary"]["attempts"] == 0


def test_build_daily_report_wires_deployed_state_into_trade_chain_execution_context(repo: Path) -> None:
    (repo / "logs").mkdir()
    report = build_daily_report(repo_root=repo, journal_dir="logs", use_checkpoint=False, advance_checkpoint=False)
    tc_ctx = report["trade_chain"]["execution_context"]
    assert tc_ctx["checked_against_current_runtime"] is True
    # No fills in this fixture, so nothing to flag -- this only proves the
    # current deployed_state (not a hardcoded/duplicated value) is what got
    # passed through as the trade-chain routine's expected execution context.
    assert tc_ctx["fills_with_execution_context_mismatch"] == []


def test_build_daily_report_ok_reflects_trade_chain_fail(repo: Path) -> None:
    logs = repo / "logs"
    logs.mkdir()
    # A RISK_REJECTED row with no reason fails the trade-chain check.
    (logs / "journal_2026-07-01.jsonl").write_text(
        '{"ts": "2026-07-01T14:00:00Z", "instrument": "MNQ", "decision": "RISK_REJECTED"}\n',
        encoding="utf-8",
    )
    report = build_daily_report(repo_root=repo, journal_dir="logs", use_checkpoint=False, advance_checkpoint=False)
    assert report["trade_chain"]["status"] == "FAIL"
    assert report["ok"] is False


def test_build_daily_report_never_deletes_or_creates_tags_or_branches(repo: Path) -> None:
    _git(repo, "branch", "stale/unmerged")
    (repo / "logs").mkdir()
    before_branches = subprocess.run(
        ["git", "branch", "--list"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout
    before_tags = subprocess.run(["git", "tag"], cwd=repo, capture_output=True, text=True, check=True).stdout
    build_daily_report(repo_root=repo, journal_dir="logs", use_checkpoint=False, advance_checkpoint=False)
    after_branches = subprocess.run(
        ["git", "branch", "--list"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout
    after_tags = subprocess.run(["git", "tag"], cwd=repo, capture_output=True, text=True, check=True).stdout
    assert before_branches == after_branches
    assert before_tags == after_tags
