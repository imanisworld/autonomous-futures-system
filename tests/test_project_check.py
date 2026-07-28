"""Tests for ops/project_check.py — session safety, promotion gate, daily reconciliation."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from ops.project_check import (
    _match_inventory_row,
    _normalize_name,
    _parse_strategy_inventory_table,
    _trade_chain_overlaps,
    build_daily_report,
    build_precommit_report,
    build_promotion_report,
    build_session_start_report,
)

SAMPLE_TABLE = """\
# STRATEGY INVENTORY

## Master Table

| Strategy | Rules | Detector | Replay parity | Honest fills | Walk-forward | Slippage | Sample | Verdict |
|---|---|---|---|---|---|---|---|---|
| ORB Reclaim (MES) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ n=305 | **PAPER PROOF** |
| 60M 3-2-2 First Live | ✅ blockers resolved | ✅ | Partial | ✅ IOC-faithful | ✅ both halves | ✅ 1-4 tick | ⚠️ n=34 MNQ thin | **PROMISING BUT UNPROVEN** |
| PDH Reclaim | ✅ | ✅ | ✅ | ✅ | ❌ both halves neg | ❌ | ✅ n=67 | **RETIRE** |

---
"""


# --------------------------------------------------------- table parsing


def test_parse_strategy_inventory_table_extracts_verdict_not_sample_column():
    rows = _parse_strategy_inventory_table(SAMPLE_TABLE)
    by_name = {r["strategy"]: r["verdict"] for r in rows}
    assert by_name["ORB Reclaim (MES)"] == "PAPER PROOF"
    assert by_name["60M 3-2-2 First Live"] == "PROMISING BUT UNPROVEN"
    assert by_name["PDH Reclaim"] == "RETIRE"
    # regression guard: the verdict must never be confused with the sample-size column
    assert "n=" not in by_name["ORB Reclaim (MES)"]


def test_normalize_name_strips_punctuation_and_instrument_suffix():
    assert _normalize_name("ORB Reclaim (MES)") == _normalize_name("orb_reclaim")
    assert _normalize_name(None) == ""


def test_match_inventory_row_fuzzy_matches_config_key_to_table_row():
    rows = _parse_strategy_inventory_table(SAMPLE_TABLE)
    row = _match_inventory_row("pdh_reclaim", rows)
    assert row is not None
    assert row["verdict"] == "RETIRE"
    assert _match_inventory_row("totally_unknown_strategy_xyz", rows) is None


# --------------------------------------------------------- git fixtures


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


# --------------------------------------------------------- session-start


def test_session_start_report_on_clean_repo_has_no_dirty_files(tmp_path):
    repo = _init_repo(tmp_path)
    report = build_session_start_report(repo_root=repo)
    assert report["current_branch"] == "main"
    assert report["dirty_tracked_files"] == []
    assert report["staged_files"] == []
    assert report["untracked_files"] == []
    assert report["branch_changed_during_check"] is False
    # no origin remote configured in this throwaway repo -> UNKNOWN, never invented
    assert report["origin_main"]["status"] == "UNKNOWN"


def test_session_start_report_surfaces_untracked_and_dirty_files(tmp_path):
    repo = _init_repo(tmp_path)
    (repo / "risk_rules.yaml").write_text(
        "trading_mode:\n  live_trading_enabled: false\n  paper_mode: true\n", encoding="utf-8"
    )
    (repo / "scratch.txt").write_text("wip", encoding="utf-8")
    report = build_session_start_report(repo_root=repo)
    assert "risk_rules.yaml" in report["dirty_tracked_files"]
    assert "scratch.txt" in report["untracked_files"]


# --------------------------------------------------------- precommit


def test_precommit_fails_closed_without_session_start_snapshot(tmp_path):
    repo = _init_repo(tmp_path)
    state_path = tmp_path / "no_such_state.json"
    report = build_precommit_report(repo_root=repo, state_path=state_path)
    assert report["ok"] is False
    assert report["status"] == "FAIL_CLOSED"
    assert any("no session-start snapshot" in reason for reason in report["fail_reasons"])


def test_precommit_passes_after_matching_session_start(tmp_path):
    repo = _init_repo(tmp_path)
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps({
            "recorded_at_head_sha": _git(repo, "rev-parse", "HEAD"),
            "branch": "main",
            "worktree": str(repo),
            "repo_root": str(repo),
        }),
        encoding="utf-8",
    )
    report = build_precommit_report(repo_root=repo, state_path=state_path)
    assert report["ok"] is True
    assert report["status"] == "PASS"
    assert report["fail_reasons"] == []


def test_precommit_fails_closed_on_branch_drift(tmp_path):
    repo = _init_repo(tmp_path)
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps({
            "recorded_at_head_sha": _git(repo, "rev-parse", "HEAD"),
            "branch": "main",
            "worktree": str(repo),
            "repo_root": str(repo),
        }),
        encoding="utf-8",
    )
    subprocess.check_call(["git", "checkout", "-b", "some-other-branch"], cwd=repo)
    report = build_precommit_report(repo_root=repo, state_path=state_path)
    assert report["ok"] is False
    assert any("branch differs from session-start" in reason for reason in report["fail_reasons"])


def test_precommit_never_mutates_the_repo(tmp_path):
    repo = _init_repo(tmp_path)
    before = _git(repo, "rev-parse", "HEAD")
    before_status = _git(repo, "status", "--porcelain")
    build_precommit_report(repo_root=repo, state_path=tmp_path / "missing.json")
    assert _git(repo, "rev-parse", "HEAD") == before
    assert _git(repo, "status", "--porcelain") == before_status


# --------------------------------------------------------- trade chain


def _write_journal(tmp_path: Path, name: str, rows: list[dict]) -> Path:
    path = tmp_path / name
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return path


def test_trade_chain_overlaps_detects_duplicate_open_instrument():
    entries = [
        {"decision": "TRADE", "instrument": "MES", "ts": "2026-07-27T10:00:00+00:00",
         "setup": {"strategy": "orb_reclaim"}},
        {"decision": "TRADE", "instrument": "MES", "ts": "2026-07-27T10:05:00+00:00",
         "setup": {"strategy": "orb_reclaim"}},
        {"type": "OUTCOME", "instrument": "MES", "ts": "2026-07-27T10:30:00+00:00",
         "outcome": {"result": "WIN"}},
    ]
    overlaps, still_open = _trade_chain_overlaps(entries)
    assert len(overlaps) == 1
    assert overlaps[0]["instrument"] == "MES"
    assert still_open == []


def test_trade_chain_overlaps_clean_sequence_has_none():
    entries = [
        {"decision": "TRADE", "instrument": "MES", "ts": "2026-07-27T10:00:00+00:00",
         "setup": {"strategy": "orb_reclaim"}},
        {"type": "OUTCOME", "instrument": "MES", "ts": "2026-07-27T10:30:00+00:00",
         "outcome": {"result": "WIN"}},
        {"decision": "TRADE", "instrument": "MNQ", "ts": "2026-07-27T11:00:00+00:00",
         "setup": {"strategy": "vwap_reclaim"}},
    ]
    overlaps, still_open = _trade_chain_overlaps(entries)
    assert overlaps == []
    assert still_open == [("MNQ", "2026-07-27T11:00:00+00:00")]


def test_daily_report_trade_chain_pass_on_clean_journal(tmp_path):
    _write_journal(tmp_path, "journal_2026-07-27.jsonl", [
        {"ts": "2026-07-27T10:00:00+00:00", "instrument": "MES", "decision": "TRADE",
         "setup": {"strategy": "orb_reclaim", "direction": "LONG"},
         "candidate_audit": [{"strategy": "orb_reclaim", "direction": "LONG", "selected": True, "attempted": True}]},
        {"ts": "2026-07-27T10:30:00+00:00", "type": "OUTCOME", "instrument": "MES",
         "outcome": {"result": "WIN", "exit_reason": "TARGET_HIT"}},
        {"ts": "2026-07-27T11:00:00+00:00", "instrument": "MNQ", "decision": "TRADE",
         "setup": {"strategy": "vwap_reclaim", "direction": "SHORT"},
         "candidate_audit": [{"strategy": "vwap_reclaim", "direction": "SHORT", "selected": True, "attempted": True}]},
        {"ts": "2026-07-27T11:05:00+00:00", "type": "OUTCOME", "instrument": "MNQ",
         "outcome": {"result": "CANCELLED", "exit_reason": "execution_failed:CANCELLED"}},
    ])
    report = build_daily_report(repo_root=Path("."), journal_dir=tmp_path, checkpoint_path=tmp_path / "checkpoint.json")
    tc = report["trade_chain_integrity"]
    assert tc["attempts"] == 2
    assert tc["fills"] == 1
    assert tc["no_fills"] == 1
    assert tc["duplicate_identities"] == []
    assert tc["status"] == "PASS"


def test_daily_report_trade_chain_flags_duplicate_identity(tmp_path):
    _write_journal(tmp_path, "journal_2026-07-27.jsonl", [
        {"ts": "2026-07-27T10:00:00+00:00", "instrument": "MES", "decision": "TRADE",
         "setup": {"strategy": "orb_reclaim", "direction": "LONG"}},
        {"ts": "2026-07-27T10:05:00+00:00", "instrument": "MES", "decision": "TRADE",
         "setup": {"strategy": "orb_reclaim", "direction": "LONG"}},
        {"ts": "2026-07-27T10:30:00+00:00", "type": "OUTCOME", "instrument": "MES",
         "outcome": {"result": "WIN", "exit_reason": "TARGET_HIT"}},
    ])
    report = build_daily_report(repo_root=Path("."), journal_dir=tmp_path, checkpoint_path=tmp_path / "checkpoint.json")
    tc = report["trade_chain_integrity"]
    assert len(tc["duplicate_identities"]) == 1
    assert tc["status"] == "REVIEW"


# --------------------------------------------------------- promotion gate


def test_promotion_report_flags_standalone_research_for_unwired_strategy(tmp_path):
    _write_journal(tmp_path, "journal_2026-07-27.jsonl", [])
    report = build_promotion_report("totally_fake_strategy_never_wired", journal_dir=tmp_path)
    assert report["identity_parity"]["wired_into_live_signal_engine"]["wired"] is False
    assert report["classification"]["runtime_parity"] == "STANDALONE_RESEARCH_ONLY"
    assert report["execution"]["zero_executable_fills"] is True
    assert report["classification"]["paper_forward_evidence"] == "NONE"


def test_promotion_report_recognizes_strat_322_first_live_is_wired(tmp_path):
    _write_journal(tmp_path, "journal_2026-07-27.jsonl", [])
    report = build_promotion_report("strat_322_first_live", journal_dir=tmp_path)
    assert report["identity_parity"]["wired_into_live_signal_engine"]["wired"] is True
    assert report["classification"]["runtime_parity"] == "REAL_PATH"


def test_promotion_report_counts_fills_and_gate_attrition_for_strategy(tmp_path):
    _write_journal(tmp_path, "journal_2026-07-27.jsonl", [
        {"ts": "2026-07-27T10:00:00+00:00", "instrument": "MES", "decision": "TRADE",
         "setup": {"strategy": "orb_reclaim", "direction": "LONG"}},
        {"ts": "2026-07-27T10:30:00+00:00", "type": "OUTCOME", "instrument": "MES",
         "outcome": {"result": "WIN", "exit_reason": "TARGET_HIT"}},
        {"ts": "2026-07-27T11:00:00+00:00", "instrument": "MNQ", "decision": "NO_TRADE",
         "reason": "gated",
         "candidate_audit": [
             {"strategy": "orb_reclaim", "failed_gates": ["MARKET_CONDITION_NOT_TRENDING"]},
         ]},
    ])
    report = build_promotion_report("orb_reclaim", journal_dir=tmp_path)
    assert report["execution"]["fills"] == 1
    assert report["execution"]["zero_executable_fills"] is False
    assert report["gate_attrition"]["failed_gate_counts"] == {"MARKET_CONDITION_NOT_TRENDING": 1}
