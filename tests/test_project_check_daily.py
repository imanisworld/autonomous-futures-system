from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ops.project_check.daily import _normalize, _overall_blockers, _parse_strategy_inventory, _strategy_source_of_truth, build_daily_report


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


def test_strategy_source_of_truth_separates_evidence_verdict_from_config_state(tmp_path: Path) -> None:
    inventory_dir = tmp_path / "docs" / "strategy-rules"
    inventory_dir.mkdir(parents=True)
    (inventory_dir / "Strategy_Inventory.md").write_text(
        "\n".join(
            [
                "## Master Table",
                "",
                "| Strategy | Verdict |",
                "|---|---|",
                "| ORB Reclaim (MES) | **PROMISING BUT UNPROVEN** |",
                "| ORB Breakout (MNQ) | **WAIT** |",
            ]
        ),
        encoding="utf-8",
    )
    lanes = {"active_lane_summary": {"MNQ": ["orb_breakout"]}}
    result = _strategy_source_of_truth(repo_root=tmp_path, rules_active_lanes=lanes)
    assert result["checked"] is True
    assert result["drift_findings"] == []
    statuses = {row["strategy"]: row["configured_active"] for row in result["matched_inventory_rows"]}
    assert statuses == {"ORB Reclaim (MES)": False, "ORB Breakout (MNQ)": True}


def test_strategy_source_of_truth_flags_explicit_broken_concept_when_active(tmp_path: Path) -> None:
    inventory_dir = tmp_path / "docs" / "strategy-rules"
    inventory_dir.mkdir(parents=True)
    (inventory_dir / "Strategy_Inventory.md").write_text(
        "\n".join([
            "## Master Table", "", "| Strategy | Verdict |", "|---|---|",
            "| ORB Breakout (MNQ) | **BROKEN** |",
        ]),
        encoding="utf-8",
    )
    lanes = {"active_lane_summary": {"MNQ": ["orb_breakout"]}}
    result = _strategy_source_of_truth(repo_root=tmp_path, rules_active_lanes=lanes)
    assert len(result["drift_findings"]) == 1
    assert "BROKEN/RETIRE/UNSAFE" in result["drift_findings"][0]["issue"]


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
    assert report["overall_status"] in {"PASS", "FAIL"}
    assert report["ok"] is (report["overall_status"] == "PASS")
    assert isinstance(report["overall_blockers"], list)
    assert report["repo_reconciliation"]["current_branch"] == "main"
    assert report["trade_chain"]["status"] == "PASS"
    assert report["trade_chain"]["summary"]["attempts"] == 0


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
    assert any(b["code"] == "TRADE_CHAIN_FAIL" for b in report["overall_blockers"])


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


def test_overall_blockers_fail_on_runtime_drift_error() -> None:
    blockers = _overall_blockers(
        hygiene={"dirty_tracked_files": [], "staged_files": []},
        runtime={
            "live_box_drift": {"status": "error", "summary": "branch mismatch"},
            "risk_rules_load_error": None,
        },
        strategy_drift={"checked": True, "drift_findings": []},
        trade_chain={"status": "PASS"},
    )
    assert any(b["code"] == "RUNTIME_DRIFT_ERROR" for b in blockers)


def test_overall_blockers_allow_promising_disabled_and_wait_active() -> None:
    blockers = _overall_blockers(
        hygiene={"dirty_tracked_files": [], "staged_files": []},
        runtime={"live_box_drift": {"status": "ok"}, "risk_rules_load_error": None},
        strategy_drift={"checked": True, "drift_findings": []},
        trade_chain={"status": "PASS"},
    )
    assert blockers == []


def test_overall_blockers_fail_when_explicit_unsafe_concept_is_active() -> None:
    blockers = _overall_blockers(
        hygiene={"dirty_tracked_files": [], "staged_files": []},
        runtime={"live_box_drift": {"status": "ok"}, "risk_rules_load_error": None},
        strategy_drift={"checked": True, "drift_findings": [{"strategy": "ORB Breakout"}]},
        trade_chain={"status": "PASS"},
    )
    assert any(b["code"] == "UNSAFE_STRATEGY_ACTIVE" for b in blockers)
