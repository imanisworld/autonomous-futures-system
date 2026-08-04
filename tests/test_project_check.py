from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from ops import project_check as pc


def _run(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)


def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _run(path, "init", "-q", "-b", "main")
    _run(path, "config", "user.email", "test@example.com")
    _run(path, "config", "user.name", "Test")
    (path / "README.md").write_text("hello\n", encoding="utf-8")
    _run(path, "add", "README.md")
    _run(path, "commit", "-q", "-m", "init")
    return path


# ─────────────────────────── git helpers ───────────────────────────────────

def test_current_branch_and_status(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    assert pc.current_branch(repo) == "main"

    (repo / "untracked.txt").write_text("x", encoding="utf-8")
    (repo / "README.md").write_text("changed\n", encoding="utf-8")
    status = pc.working_tree_status(repo)
    assert "README.md" in status["dirty_tracked"]
    assert "untracked.txt" in status["untracked"]
    assert status["staged"] == []


def test_rev_list_relationship_in_sync_and_ahead(tmp_path: Path) -> None:
    origin = _init_repo(tmp_path / "origin")
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(origin), str(clone)], check=True, capture_output=True, text=True)
    _run(clone, "config", "user.email", "test@example.com")
    _run(clone, "config", "user.name", "Test")

    assert pc.rev_list_relationship(clone, "main", "origin/main") == "IN_SYNC"

    (clone / "new.txt").write_text("x", encoding="utf-8")
    _run(clone, "add", "new.txt")
    _run(clone, "commit", "-q", "-m", "extra")
    assert pc.rev_list_relationship(clone, "main", "origin/main") == "AHEAD"


def test_rev_list_relationship_unknown_ref(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    assert pc.rev_list_relationship(repo, "main", "origin/does-not-exist") == pc.UNKNOWN


def test_list_worktrees_reports_current(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    worktrees = pc.list_worktrees(repo)
    assert len(worktrees) == 1
    assert worktrees[0]["branch"] == "main"
    assert Path(worktrees[0]["path"]).resolve() == repo.resolve()


def test_branch_tracking_report_local_only(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    _run(repo, "checkout", "-q", "-b", "feature/local-only")
    report = pc.branch_tracking_report(repo)
    assert "feature/local-only" in report["local_only"]
    assert report["tracking_deleted_remote"] == []


def test_unique_evidence_without_archive_tag(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    _run(repo, "checkout", "-q", "-b", "feature/x")
    (repo / "extra.txt").write_text("x", encoding="utf-8")
    _run(repo, "add", "extra.txt")
    _run(repo, "commit", "-q", "-m", "feature work")
    _run(repo, "checkout", "-q", "main")

    flagged = pc.unique_evidence_without_archive_tag(repo)
    assert any(f["branch"] == "feature/x" for f in flagged)

    _run(repo, "tag", "-a", "archive/feature/x-2026-01-01", "feature/x", "-m", "archived")
    flagged_after_tag = pc.unique_evidence_without_archive_tag(repo)
    assert all(f["branch"] != "feature/x" for f in flagged_after_tag)


def test_unique_evidence_excludes_checked_out_branches(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    _run(repo, "checkout", "-q", "-b", "claude/active-wip")
    (repo / "extra.txt").write_text("x", encoding="utf-8")
    _run(repo, "add", "extra.txt")
    _run(repo, "commit", "-q", "-m", "active work in progress")

    flagged = pc.unique_evidence_without_archive_tag(repo)
    assert any(f["branch"] == "claude/active-wip" for f in flagged)

    flagged_excluded = pc.unique_evidence_without_archive_tag(repo, exclude_branches=frozenset({"claude/active-wip"}))
    assert all(f["branch"] != "claude/active-wip" for f in flagged_excluded)


def test_session_state_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _init_repo(tmp_path / "repo")
    state_path = tmp_path / "state.json"
    monkeypatch.setattr(pc, "SESSION_STATE_PATH", state_path)

    written = pc.write_session_state(repo, repo)
    assert state_path.exists()
    loaded = pc.read_session_state()
    assert loaded == written
    assert loaded["branch"] == "main"


def test_read_session_state_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pc, "SESSION_STATE_PATH", tmp_path / "missing.json")
    assert pc.read_session_state() is None


# ─────────────────────────── promotion evidence extraction ─────────────────

def test_find_first_shallow_and_nested() -> None:
    payload = {"fills": 12, "nested": {"cancelled": 3, "deeper": {"rejects": 1}}}
    assert pc._find_first(payload, ("fills",)) == 12
    assert pc._find_first(payload, ("cancelled",)) == 3
    assert pc._find_first(payload, ("rejects",)) == 1
    assert pc._find_first(payload, ("nonexistent",)) is None


def test_check_accounting_identity_pass_and_fail() -> None:
    ok_fields = {"attempts": 10, "fills": 6, "cancellations": 3, "rejects": 1, "resolved": 5, "legitimately_open": 1}
    result = pc.check_accounting_identity(ok_fields)
    assert result["computable"] is True
    assert result["all_pass"] is True

    bad_fields = dict(ok_fields, fills=7)
    result_bad = pc.check_accounting_identity(bad_fields)
    assert result_bad["all_pass"] is False


def test_check_accounting_identity_not_computable() -> None:
    result = pc.check_accounting_identity({k: None for k in pc.FIELD_ALIASES})
    assert result["computable"] is False
    assert result["all_pass"] is None


def test_classify_promotion_no_evidence() -> None:
    classification, reasons = pc.classify_promotion(
        evidence_found=False,
        accounting={"computable": False, "all_pass": None},
        gate={"permission_status": pc.UNKNOWN, "in_enabled_concepts": False},
        inventory={"found": False},
        evidence_fields={},
    )
    assert classification == "WAIT"
    assert reasons


def test_classify_promotion_accounting_mismatch_is_unsafe() -> None:
    classification, _ = pc.classify_promotion(
        evidence_found=True,
        accounting={"computable": True, "all_pass": False},
        gate={"permission_status": "PAPER_ELIGIBLE", "in_enabled_concepts": True},
        inventory={"found": False},
        evidence_fields={"fills": 5},
    )
    assert classification == "UNSAFE"


def test_classify_promotion_inventory_broken_wins() -> None:
    classification, reasons = pc.classify_promotion(
        evidence_found=True,
        accounting={"computable": False, "all_pass": None},
        gate={"permission_status": "PAPER_ELIGIBLE", "in_enabled_concepts": True},
        inventory={"found": True, "matches": [{"name": "PDH Reclaim", "verdict": "**RETIRE**"}]},
        evidence_fields={"fills": 10},
    )
    assert classification == "BROKEN"
    assert any("RETIRE" in r for r in reasons)


def test_classify_promotion_default_promising() -> None:
    classification, _ = pc.classify_promotion(
        evidence_found=True,
        accounting={"computable": False, "all_pass": None},
        gate={"permission_status": "PAPER_ELIGIBLE", "in_enabled_concepts": True},
        inventory={"found": False},
        evidence_fields={"fills": 10},
    )
    assert classification == "PROMISING BUT UNPROVEN"


def test_strategy_gate_status() -> None:
    rules = {
        "strategy_permission_gate": {"default_status": "SHADOW_ONLY", "strategy_status": {"orb_breakout": "PAPER_ELIGIBLE"}},
        "strategy": {"enabled_concepts": ["orb_breakout"], "disabled_concepts_per_instrument": {"MES": ["orb_breakout"]}},
    }
    status = pc.strategy_gate_status(rules, "orb_breakout")
    assert status["permission_status"] == "PAPER_ELIGIBLE"
    assert status["in_enabled_concepts"] is True
    assert status["disabled_per_instrument"]["MES"] is True

    other = pc.strategy_gate_status(rules, "vwap_hold")
    assert other["permission_status"] == "SHADOW_ONLY"
    assert other["in_enabled_concepts"] is False


def test_parse_inventory_table_and_find_row(tmp_path: Path) -> None:
    doc_dir = tmp_path / "docs" / "strategy-rules"
    doc_dir.mkdir(parents=True)
    (doc_dir / "Strategy_Inventory.md").write_text(
        "\n".join([
            "# STRATEGY INVENTORY",
            "",
            "| Strategy | Rules | Detector | Replay parity | Honest fills | Walk-forward | Slippage | Sample | Verdict |",
            "|---|---|---|---|---|---|---|---|---|",
            "| ORB Breakout (MNQ) | Y | Y | Y | Y | N | N | n=25 | **WAIT** |",
            "| ORB Reclaim (MES) | Y | Y | Y | Y | Y | Y | n=305 | **PAPER PROOF** |",
            "",
            "## Detailed Strategy Profiles",
            "some text with a | pipe that is not a table row",
        ]),
        encoding="utf-8",
    )
    rows = pc.parse_inventory_table(tmp_path)
    assert len(rows) == 2

    found = pc.find_inventory_row(tmp_path, "orb_breakout")
    assert found["found"] is True
    assert found["matches"][0]["verdict"] == "**WAIT**"

    missing = pc.find_inventory_row(tmp_path, "no_such_strategy")
    assert missing["found"] is False


def test_locate_evidence_prefers_canonical(tmp_path: Path) -> None:
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "orb_breakout_entry_study_results.json").write_text("{}", encoding="utf-8")
    (scripts_dir / "orb_breakout_canonical_evidence_results.json").write_text("{}", encoding="utf-8")
    (scripts_dir / "orb_breakout_canonical_evidence_raw_trades.jsonl").write_text("", encoding="utf-8")

    chosen, all_matches = pc._locate_evidence(tmp_path, "orb_breakout")
    assert chosen is not None
    assert chosen.name == "orb_breakout_canonical_evidence_results.json"
    assert len(all_matches) == 2


def test_locate_evidence_ambiguous_without_canonical(tmp_path: Path) -> None:
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "vwap_hold_a_results.json").write_text("{}", encoding="utf-8")
    (scripts_dir / "vwap_hold_b_results.json").write_text("{}", encoding="utf-8")

    chosen, all_matches = pc._locate_evidence(tmp_path, "vwap_hold")
    assert chosen is None
    assert len(all_matches) == 2


# ─────────────────────────── trade chain integrity ─────────────────────────

def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def _trade(ts: str, *, instrument: str = "MNQ") -> dict:
    return {
        "ts": ts,
        "instrument": instrument,
        "decision": "TRADE",
        "risk_check": {"result": "APPROVED"},
        "setup": {"direction": "LONG", "strategy": "orb_breakout", "entry": 100.0, "stop": 90.0, "target": 130.0, "contracts": 1},
    }


def _outcome(ts: str, *, instrument: str = "MNQ", result: str = "WIN", exit_reason: str = "target_hit") -> dict:
    return {
        "ts": ts,
        "type": "OUTCOME",
        "instrument": instrument,
        "session": "new_york",
        "outcome": {"result": result, "entry_price": 100.0, "exit_price": 130.0, "exit_reason": exit_reason, "pnl_ticks": 30, "pnl_dollars": 60.0, "contracts": 1},
    }


def test_trade_chain_integrity_clean_pass(tmp_path: Path) -> None:
    journal_dir = tmp_path / "logs"
    journal_dir.mkdir()
    _write_jsonl(journal_dir / "journal_2026-08-04.jsonl", [
        _trade("2026-08-04T10:00:00+00:00"),
        _outcome("2026-08-04T10:05:00+00:00", result="WIN"),
        _trade("2026-08-04T11:00:00+00:00"),
        _outcome("2026-08-04T11:05:00+00:00", result="CANCELLED"),
    ])
    report = pc.trade_chain_integrity(journal_dir, since_date="2026-08-04", broker_json=None, api_base=None)
    assert report["totals"]["attempts"] == 2
    assert report["totals"]["fills"] == 1
    assert report["totals"]["cancellations_no_fill"] == 1
    assert report["totals"]["orphan_outcomes"] == 0
    assert report["accounting_mismatches"] == []
    assert report["pass"] is True


def test_trade_chain_integrity_flags_orphan_outcome(tmp_path: Path) -> None:
    journal_dir = tmp_path / "logs"
    journal_dir.mkdir()
    _write_jsonl(journal_dir / "journal_2026-08-04.jsonl", [
        _outcome("2026-08-04T10:05:00+00:00", result="WIN"),
    ])
    report = pc.trade_chain_integrity(journal_dir, since_date="2026-08-04", broker_json=None, api_base=None)
    assert report["totals"]["orphan_outcomes"] == 1
    assert report["pass"] is False


def test_trade_chain_integrity_legitimately_open(tmp_path: Path) -> None:
    journal_dir = tmp_path / "logs"
    journal_dir.mkdir()
    _write_jsonl(journal_dir / "journal_2026-08-04.jsonl", [
        _trade("2026-08-04T10:00:00+00:00"),
    ])
    report = pc.trade_chain_integrity(journal_dir, since_date="2026-08-04", broker_json=None, api_base=None)
    assert report["per_instrument"]["MNQ"]["legitimately_open"] == 1
    assert report["per_instrument"]["MNQ"]["accounting_identity_ok"] is True


# ─────────────────────────── strategy source of truth ──────────────────────

def test_strategy_source_of_truth_flags_active_but_broken(tmp_path: Path) -> None:
    doc_dir = tmp_path / "docs" / "strategy-rules"
    doc_dir.mkdir(parents=True)
    (doc_dir / "Strategy_Inventory.md").write_text(
        "\n".join([
            "| Strategy | Verdict |",
            "|---|---|",
            "| ORB Breakout (MNQ) | **BROKEN** |",
        ]),
        encoding="utf-8",
    )
    (tmp_path / "risk_rules.yaml").write_text(
        "\n".join([
            "strategy:",
            "  enabled_concepts: [orb_breakout]",
            "strategy_permission_gate:",
            "  default_status: SHADOW_ONLY",
            "  strategy_status:",
            "    orb_breakout: PAPER_ELIGIBLE",
        ]),
        encoding="utf-8",
    )
    result = pc.strategy_source_of_truth(tmp_path)
    assert any(d["strategy"] == "orb_breakout" and d["issue"] == "active_but_inventory_says_not_ready" for d in result["drift"])
