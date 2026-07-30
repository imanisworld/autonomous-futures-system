from __future__ import annotations

import argparse
import json
import subprocess
from datetime import date, timedelta
from pathlib import Path

import ops.project_check as pc


# ─────────────────────────────────────────────────────────── git fixtures ──

def _run(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)


def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _run(path, "init", "-q")
    _run(path, "config", "user.email", "test@example.com")
    _run(path, "config", "user.name", "Test")
    (path / "README.md").write_text("hello\n")
    _run(path, "add", "README.md")
    _run(path, "commit", "-q", "-m", "init")
    _run(path, "branch", "-M", "main")
    return path


# ─────────────────────────────────────────────────────── git state collector ──

def test_collect_git_state_reports_clean_repo(tmp_path):
    repo = _init_repo(tmp_path)
    state = pc.collect_git_state(repo)
    assert state["current_branch"] == "main"
    assert state["dirty_tracked_files"] == []
    assert state["staged_files"] == []
    assert state["untracked_files"] == []
    assert state["stash"] == []
    assert state["local_main_relationship"]["status"] == "UNKNOWN"  # no origin configured


def test_collect_git_state_flags_dirty_and_untracked(tmp_path):
    repo = _init_repo(tmp_path)
    (repo / "README.md").write_text("changed\n")
    (repo / "new_file.txt").write_text("new\n")
    state = pc.collect_git_state(repo)
    assert "README.md" in state["dirty_tracked_files"]
    assert "new_file.txt" in state["untracked_files"]


def test_local_branches_detect_local_only(tmp_path):
    repo = _init_repo(tmp_path)
    _run(repo, "branch", "feature-x")
    branches = pc._local_branches(repo)
    names = {b["name"]: b for b in branches}
    assert names["feature-x"]["local_only"] is True


# ──────────────────────────────────────────────────────────────── precommit ──

def _namespace(**kwargs) -> argparse.Namespace:
    return argparse.Namespace(**kwargs)


def test_precommit_fails_closed_without_checkpoint(tmp_path, monkeypatch, capsys):
    repo = _init_repo(tmp_path)
    monkeypatch.setattr(pc, "ROOT", repo)
    monkeypatch.setattr(pc, "SESSION_CHECKPOINT_PATH", repo / "logs" / ".missing.json")
    rc = pc.cmd_precommit(_namespace(json=True))
    out = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert out["verdict"] == "FAIL"
    assert any("session-start state cannot be verified" in p for p in out["problems"])


def test_precommit_passes_after_session_start_with_no_changes(tmp_path, monkeypatch, capsys):
    repo = _init_repo(tmp_path)
    checkpoint = repo / "logs" / ".project_check_session.json"
    monkeypatch.setattr(pc, "ROOT", repo)
    monkeypatch.setattr(pc, "SESSION_CHECKPOINT_PATH", checkpoint)
    rc_start = pc.cmd_session_start(_namespace(json=True, fetch=False, gh=False))
    capsys.readouterr()
    assert rc_start == 0
    assert checkpoint.exists()

    rc = pc.cmd_precommit(_namespace(json=True))
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["verdict"] == "PASS"
    assert out["problems"] == []


def test_precommit_fails_when_branch_changed_since_session_start(tmp_path, monkeypatch, capsys):
    repo = _init_repo(tmp_path)
    checkpoint = repo / "logs" / ".project_check_session.json"
    monkeypatch.setattr(pc, "ROOT", repo)
    monkeypatch.setattr(pc, "SESSION_CHECKPOINT_PATH", checkpoint)
    pc.cmd_session_start(_namespace(json=True, fetch=False, gh=False))
    capsys.readouterr()

    _run(repo, "checkout", "-q", "-b", "other-branch")

    rc = pc.cmd_precommit(_namespace(json=True))
    out = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert out["verdict"] == "FAIL"
    assert any("branch differs from session-start branch unexpectedly" in p for p in out["problems"])


def test_precommit_flags_staged_secret_looking_file(tmp_path, monkeypatch, capsys):
    repo = _init_repo(tmp_path)
    checkpoint = repo / "logs" / ".project_check_session.json"
    monkeypatch.setattr(pc, "ROOT", repo)
    monkeypatch.setattr(pc, "SESSION_CHECKPOINT_PATH", checkpoint)
    pc.cmd_session_start(_namespace(json=True, fetch=False, gh=False))
    capsys.readouterr()

    (repo / ".env").write_text("SECRET=1\n")
    _run(repo, "add", ".env")

    rc = pc.cmd_precommit(_namespace(json=True))
    out = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert any("possible secret/key file staged" in p for p in out["problems"])


# ───────────────────────────────────────────────────── promotion classification ──

def _runtime(status: str = "PAPER_ELIGIBLE") -> dict:
    return {
        "permission_status": status,
        "entry_fill_model": "market",
        "tradovate_entry_execution_mode": "legacy",
        "exit_mode": "static",
    }


def _paper_evidence(**overrides) -> dict:
    base = {
        "attempts": 0,
        "fills_total": 0,
        "cancellations_no_fill": 0,
        "unjoinable_needs_manual_review": 0,
        "accounting_identity_holds": True,
        "zero_executable_fills": True,
        "net_pnl_dollars": 0.0,
        "fills_resolved": 0,
    }
    base.update(overrides)
    return base


def test_classify_promotion_blocked_by_permission_gate():
    classification, reasons = pc._classify_promotion(
        _runtime(status="SHADOW_ONLY"), _paper_evidence(), {"verdict": "PROMISING BUT UNPROVEN"}, {},
    )
    assert classification == "WAIT"
    assert any("permission_status" in r for r in reasons)


def test_classify_promotion_broken_on_accounting_mismatch():
    evidence = _paper_evidence(accounting_identity_holds=False)
    classification, reasons = pc._classify_promotion(_runtime(), evidence, {"verdict": "UNKNOWN"}, {})
    assert classification == "BROKEN"
    assert any("accounting identity failed" in r for r in reasons)


def test_classify_promotion_zero_fills_is_promising_but_unproven():
    classification, reasons = pc._classify_promotion(
        _runtime(), _paper_evidence(), {"verdict": "PROMISING BUT UNPROVEN"}, {},
    )
    assert classification == "PROMISING BUT UNPROVEN"
    assert any("ZERO EXECUTABLE FILLS" in r for r in reasons)


def test_classify_promotion_zero_fills_with_broken_research_stays_broken():
    classification, _reasons = pc._classify_promotion(
        _runtime(), _paper_evidence(), {"verdict": "BROKEN"}, {},
    )
    assert classification == "BROKEN"


def test_classify_promotion_negative_pnl_is_broken():
    evidence = _paper_evidence(
        attempts=40, fills_total=40, zero_executable_fills=False,
        net_pnl_dollars=-125.0, fills_resolved=40,
    )
    classification, reasons = pc._classify_promotion(_runtime(), evidence, {"verdict": "PROMISING BUT UNPROVEN"}, {})
    assert classification == "BROKEN"
    assert any("negative" in r for r in reasons)


def test_classify_promotion_thin_sample_is_promising_but_unproven():
    evidence = _paper_evidence(
        attempts=10, fills_total=10, zero_executable_fills=False,
        net_pnl_dollars=50.0, fills_resolved=10,
    )
    classification, reasons = pc._classify_promotion(_runtime(), evidence, {"verdict": "PROMISING BUT UNPROVEN"}, {})
    assert classification == "PROMISING BUT UNPROVEN"
    assert any("30-trade sample bar" in r for r in reasons)


def test_classify_promotion_validated_requires_sample_and_profit():
    evidence = _paper_evidence(
        attempts=35, fills_total=35, zero_executable_fills=False,
        net_pnl_dollars=500.0, fills_resolved=35,
    )
    classification, _reasons = pc._classify_promotion(_runtime(), evidence, {"verdict": "PAPER PROOF"}, {})
    assert classification == "VALIDATED"


def test_classify_promotion_parity_defect_blocks_validated():
    evidence = _paper_evidence(
        attempts=35, fills_total=35, zero_executable_fills=False,
        net_pnl_dollars=500.0, fills_resolved=35,
    )
    parity = {"mismatches": [{"field": "entry_fill_model", "evidence_assumed": "ioc_limit", "runtime_actual": "market"}]}
    classification, reasons = pc._classify_promotion(_runtime(), evidence, {"verdict": "PAPER PROOF"}, parity)
    assert classification == "PROMISING BUT UNPROVEN"
    assert any("parity defects" in r for r in reasons)


# ──────────────────────────────────────────────────────── inventory parsing ──

_INVENTORY_MD = """# STRATEGY INVENTORY

## Master Table

| Strategy | Rules | Detector | Replay parity | Honest fills | Walk-forward | Slippage | Sample | Verdict |
|---|---|---|---|---|---|---|---|---|
| ORB Breakout (MNQ) | ✅ | ✅ | Partial | ✅ | ❌ | ❌ | n=25 | **WAIT** |
| ORB Reclaim (MES) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | n=305 | **PAPER PROOF** |

---
"""


def test_parse_inventory_rows_extracts_name_and_verdict(tmp_path):
    path = tmp_path / "Strategy_Inventory.md"
    path.write_text(_INVENTORY_MD, encoding="utf-8")
    rows = pc._parse_inventory_rows(path)
    verdicts = {r["name"]: r["verdict"] for r in rows}
    assert verdicts["ORB Breakout (MNQ)"] == "WAIT"
    assert verdicts["ORB Reclaim (MES)"] == "PAPER PROOF"


def test_lookup_inventory_verdict_matches_by_code_name(tmp_path):
    inv_dir = tmp_path / "docs" / "strategy-rules"
    inv_dir.mkdir(parents=True)
    (inv_dir / "Strategy_Inventory.md").write_text(_INVENTORY_MD, encoding="utf-8")
    result = pc._lookup_inventory_verdict(tmp_path, "orb_breakout")
    assert result["verdict"] == "WAIT"


def test_lookup_inventory_verdict_unknown_when_unmatched(tmp_path):
    inv_dir = tmp_path / "docs" / "strategy-rules"
    inv_dir.mkdir(parents=True)
    (inv_dir / "Strategy_Inventory.md").write_text(_INVENTORY_MD, encoding="utf-8")
    result = pc._lookup_inventory_verdict(tmp_path, "totally_unmatched_strategy")
    assert result["verdict"] == "UNKNOWN"


# ─────────────────────────────────────────────── trade-chain integrity (journal) ──

def _write_journal(log_dir: Path, day: date, entries: list[dict]) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / f"journal_{day.isoformat()}.jsonl"
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")


def _trade(order_id: str, *, strategy="orb_breakout", instrument="MNQ", direction="LONG",
           stop=4990.0, target=5020.0, ts="2026-07-30T13:00:00+00:00") -> dict:
    return {
        "ts": ts,
        "instrument": instrument,
        "decision": "TRADE",
        "risk_check": {"result": "APPROVED"},
        "paper_order_id": order_id,
        "setup": {"strategy": strategy, "direction": direction, "entry": 5000.0,
                  "stop": stop, "target": target, "contracts": 1},
    }


def _outcome(order_id: str, result: str, pnl: float, *, ts="2026-07-30T13:05:00+00:00") -> dict:
    return {
        "ts": ts,
        "type": "OUTCOME",
        "outcome": {"paper_order_id": order_id, "result": result, "pnl_dollars": pnl},
    }


def test_trade_chain_report_passes_on_clean_day(tmp_path):
    today = date.today()
    entries = [
        _trade("A"), _outcome("A", "WIN", 40.0),
        _trade("B"), _outcome("B", "CANCELLED", 0.0),
    ]
    _write_journal(tmp_path, today, entries)
    report = pc._trade_chain_report(tmp_path, since=today)
    assert report["pass"] is True
    assert report["attempts"] == 2
    assert report["resolved"] == 1
    assert report["no_fills"] == 1
    assert report["accounting_identity_holds"] is True


def test_trade_chain_report_flags_naked_position(tmp_path):
    today = date.today()
    entries = [
        _trade("A", stop=None), _outcome("A", "WIN", 40.0),
    ]
    _write_journal(tmp_path, today, entries)
    report = pc._trade_chain_report(tmp_path, since=today)
    assert report["pass"] is False
    assert report["naked_positions"] == 1


def test_trade_chain_report_flags_duplicate_order_identity(tmp_path):
    today = date.today()
    entries = [
        _trade("A"), _trade("A"), _outcome("A", "WIN", 40.0),
    ]
    _write_journal(tmp_path, today, entries)
    report = pc._trade_chain_report(tmp_path, since=today)
    assert report["duplicate_order_identity"] == 1
    assert report["pass"] is False


def test_trade_chain_report_empty_journal_is_clean_pass(tmp_path):
    today = date.today()
    report = pc._trade_chain_report(tmp_path, since=today)
    assert report["pass"] is True
    assert report["attempts"] == 0


# ──────────────────────────────────────────────────── strategy source-of-truth ──

def test_strategy_source_of_truth_flags_stale_and_undocumented_drift(tmp_path, monkeypatch):
    (tmp_path / "risk_rules.yaml").write_text(
        "strategy_permission_gate:\n"
        "  enabled: true\n"
        "  default_status: SHADOW_ONLY\n"
        "  strategy_status:\n"
        "    orb_breakout: PAPER_ELIGIBLE\n",
        encoding="utf-8",
    )
    inv_dir = tmp_path / "docs" / "strategy-rules"
    inv_dir.mkdir(parents=True)
    (inv_dir / "Strategy_Inventory.md").write_text(_INVENTORY_MD, encoding="utf-8")

    result = pc._strategy_source_of_truth(tmp_path)
    issues = {d["strategy"]: d["issue"] for d in result["drift"]}
    assert "runtime is PAPER_ELIGIBLE despite an inventory verdict of BROKEN/WAIT/OVERFIT/RETIRE" == issues["ORB Breakout (MNQ)"]
    assert "described as active/validated in inventory but runtime is not PAPER_ELIGIBLE" == issues["ORB Reclaim (MES)"]
