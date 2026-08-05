from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ops import daily_reconciliation as dr
from ops import session_safety as ss


# ─── Hermetic git repo fixture (mirrors tests/test_session_safety.py) ──────

def _run(args: list[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=10)
    if check and result.returncode != 0:
        raise AssertionError(f"git {args} failed: {result.stderr}")
    return result


def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _run(["init", "-q", "-b", "main"], path)
    _run(["config", "user.name", "Test"], path)
    _run(["config", "user.email", "test@example.com"], path)
    _run(["config", "commit.gpgsign", "false"], path)
    (path / "README.md").write_text("hello\n", encoding="utf-8")
    _run(["add", "README.md"], path)
    _run(["commit", "-q", "-m", "initial commit"], path)
    return path


@pytest.fixture
def repo(tmp_path) -> Path:
    return _init_repo(tmp_path / "repo")


@pytest.fixture(autouse=True)
def no_gh(monkeypatch):
    monkeypatch.setattr(ss.shutil, "which", lambda name: None)


# ─── Journal fixtures ────────────────────────────────────────────────────

def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def _trade(ts, instrument="MNQ", *, direction="LONG", entry=19500.0, stop=19480.0, target=19540.0,
           strategy="orb_reclaim", contracts=1, entry_fill_model=None) -> dict:
    row = {
        "ts": ts, "instrument": instrument, "decision": "TRADE",
        "risk_check": {"result": "APPROVED"},
        "setup": {"direction": direction, "strategy": strategy, "entry": entry, "stop": stop,
                  "target": target, "contracts": contracts},
    }
    if entry_fill_model:
        row["entry_fill_model"] = entry_fill_model
    return row


def _outcome(ts, instrument="MNQ", *, result="WIN", pnl=100.0, entry_price=None, contracts=1, exit_reason="target_hit") -> dict:
    body = {"result": result, "pnl_dollars": pnl, "contracts": contracts, "exit_reason": exit_reason}
    if entry_price is not None:
        body["entry_price"] = entry_price
    return {"ts": ts, "type": "OUTCOME", "instrument": instrument, "outcome": body}


def _order_ids(ts, instrument="MNQ", **ids) -> dict:
    return {"ts": ts, "type": "ORDER_IDS", "instrument": instrument, "order_ids": ids or {"parent": "1", "stop": "2", "target": "3"}}


# ─── Section D: strategy source of truth ───────────────────────────────────

def test_parse_strategy_inventory_master_table(tmp_path):
    doc = tmp_path / "Strategy_Inventory.md"
    doc.write_text(
        "# STRATEGY INVENTORY\n"
        "*Last updated: 2026-07-23*\n\n"
        "## Master Table\n\n"
        "| Strategy | Rules | Verdict |\n"
        "|---|---|---|\n"
        "| ORB Reclaim (MES) | ✅ | **PAPER PROOF** |\n"
        "| PDH Reclaim | ✅ | **RETIRE** |\n\n"
        "## Detailed Strategy Profiles\n"
        "### ORB Reclaim — MES\n",
        encoding="utf-8",
    )
    result = dr.parse_strategy_inventory(doc)
    assert result["available"] is True
    assert result["last_updated"] == "2026-07-23"
    assert len(result["rows"]) == 2
    assert result["rows"][0] == {
        "strategy": "ORB Reclaim (MES)", "verdict_raw": "**PAPER PROOF**", "verdict": "PAPER PROOF",
    }
    assert result["rows"][1]["verdict"] == "RETIRE"


def test_parse_strategy_inventory_missing_file(tmp_path):
    result = dr.parse_strategy_inventory(tmp_path / "nope.md")
    assert result["available"] is False
    assert result["rows"] == []


def test_strategy_source_of_truth_flags_conflict(tmp_path):
    inventory = tmp_path / "Strategy_Inventory.md"
    inventory.write_text(
        "*Last updated: 2026-07-23*\n\n"
        "## Master Table\n\n"
        "| Strategy | Verdict |\n"
        "|---|---|\n"
        "| ORB Reclaim (MES) | **PAPER PROOF** |\n\n",
        encoding="utf-8",
    )
    risk_rules = tmp_path / "risk_rules.yaml"
    risk_rules.write_text(
        "strategy_permission_gate:\n"
        "  default_status: SHADOW_ONLY\n"
        "  strategy_status:\n"
        "    orb_reclaim: SHADOW_ONLY\n"
        "strategy:\n"
        "  enabled_concepts:\n"
        "    - orb_reclaim\n",
        encoding="utf-8",
    )
    section = dr.build_strategy_source_of_truth_section(
        tmp_path, inventory_path=inventory, risk_rules_path=risk_rules,
        now=datetime(2026, 8, 5, tzinfo=timezone.utc),
    )
    conflicts = [d for d in section["drift"] if d["status"] == "CONFLICT"]
    assert any("orb_reclaim" == d["guessed_key"] for d in conflicts)
    assert section["inventory_staleness"]["stale"] is False


def test_strategy_source_of_truth_flags_missing_from_inventory(tmp_path):
    inventory = tmp_path / "Strategy_Inventory.md"
    inventory.write_text("## Master Table\n\n| Strategy | Verdict |\n|---|---|\n", encoding="utf-8")
    risk_rules = tmp_path / "risk_rules.yaml"
    risk_rules.write_text(
        "strategy_permission_gate:\n"
        "  default_status: SHADOW_ONLY\n"
        "  strategy_status:\n"
        "    vwap_hold: PAPER_ELIGIBLE\n"
        "strategy:\n  enabled_concepts: [vwap_hold]\n",
        encoding="utf-8",
    )
    section = dr.build_strategy_source_of_truth_section(tmp_path, inventory_path=inventory, risk_rules_path=risk_rules)
    assert "vwap_hold" in section["risk_rules_strategies_missing_from_inventory"]


def test_strategy_source_of_truth_stale_inventory(tmp_path):
    inventory = tmp_path / "Strategy_Inventory.md"
    inventory.write_text("*Last updated: 2026-01-01*\n\n## Master Table\n\n| Strategy | Verdict |\n|---|---|\n", encoding="utf-8")
    section = dr.build_strategy_source_of_truth_section(
        tmp_path, inventory_path=inventory, risk_rules_path=tmp_path / "missing.yaml",
        now=datetime(2026, 8, 5, tzinfo=timezone.utc),
    )
    assert section["inventory_staleness"]["stale"] is True


# ─── Section B: evidence preservation ──────────────────────────────────────

def test_evidence_preservation_flags_blocker():
    candidates = {
        "scoped": True,
        "branches": [
            {"branch": "b1", "unique_commit_count": 2, "archive_exact_preserved": False,
             "archive_descendant_preserved": False, "archive_matches": []},
            {"branch": "b2", "unique_commit_count": 1, "archive_exact_preserved": True,
             "archive_descendant_preserved": True, "archive_matches": [{"tag": "archive/b2-x", "exact": True, "descends": True}]},
        ],
    }
    result = dr.build_evidence_preservation_section(candidates)
    assert len(result["blockers"]) == 1
    assert result["blockers"][0]["branch"] == "b1"
    assert result["blockers"][0]["severity"] == "BLOCKER"
    assert len(result["preserved"]) == 1


def test_evidence_preservation_scope_skipped():
    result = dr.build_evidence_preservation_section({"scoped": False, "limitation": "too many branches"})
    assert result["scoped"] is False
    assert result["blockers"] == []


# ─── per-fill checks / duplicates ──────────────────────────────────────────

def test_per_fill_check_consistent_trade():
    from ops.proof_30_mnq import ResolvedTrade
    trade = ResolvedTrade(
        trade=_trade("2026-08-05T14:00:00+00:00"),
        outcome=_outcome("2026-08-05T14:05:00+00:00", entry_price=19500.0),
    )

    class FakeConfig:
        entry_fill_model = "market"
        entry_tolerance_ticks_by_root = {"MNQ": 32}

    result = dr.per_fill_check(trade, FakeConfig())
    assert result["consistent"] is True
    assert result["issues"] == []
    assert result["slippage_flag"] is False


def test_per_fill_check_flags_missing_fields_and_bad_bracket():
    from ops.proof_30_mnq import ResolvedTrade
    bad_trade = {
        "ts": "2026-08-05T14:00:00+00:00", "instrument": "MNQ", "decision": "TRADE",
        "risk_check": {"result": "APPROVED"},
        "setup": {"direction": "LONG", "strategy": "orb_reclaim", "entry": 19500.0, "stop": 19520.0, "target": 19480.0},
    }
    trade = ResolvedTrade(trade=bad_trade, outcome=_outcome("2026-08-05T14:05:00+00:00"))
    result = dr.per_fill_check(trade, config=None)
    assert result["consistent"] is False
    assert any("not internally consistent" in issue for issue in result["issues"])


def test_per_fill_check_flags_slippage():
    from ops.proof_30_mnq import ResolvedTrade
    trade = ResolvedTrade(
        trade=_trade("2026-08-05T14:00:00+00:00", entry=19500.0),
        outcome=_outcome("2026-08-05T14:05:00+00:00", entry_price=19510.0),  # 10 points = 40 ticks @ 0.25
    )

    class FakeConfig:
        entry_fill_model = "ioc_limit"
        entry_tolerance_ticks_by_root = {"MNQ": 32}

    result = dr.per_fill_check(trade, FakeConfig())
    assert result["slippage_flag"] is True
    assert result["consistent"] is False


def test_duplicate_order_identities_detected():
    rows = [
        {"order_ids": {"parent": "A1", "stop": "A2", "target": "A3"}},
        {"order_ids": {"parent": "B1", "stop": "A2", "target": "B3"}},  # A2 reused
    ]
    dups = dr.duplicate_order_identities(rows)
    assert len(dups) == 1
    assert dups[0]["order_id"] == "A2"
    assert dups[0]["occurrence_count"] == 2


def test_duplicate_order_identities_none_when_unique():
    rows = [{"order_ids": {"parent": "A1"}}, {"order_ids": {"parent": "B1"}}]
    assert dr.duplicate_order_identities(rows) == []


# ─── Trade chain integrity (Section E) ─────────────────────────────────────

def test_trade_chain_clean_pass(tmp_path):
    since = datetime(2026, 8, 5, 0, 0, tzinfo=timezone.utc)
    _write_jsonl(tmp_path / "journal_2026-08-05.jsonl", [
        _trade("2026-08-05T14:00:00+00:00"),
        _outcome("2026-08-05T14:05:00+00:00", result="WIN", entry_price=19500.0),
        _trade("2026-08-05T15:00:00+00:00", strategy="orb_breakout"),
        _outcome("2026-08-05T15:10:00+00:00", result="CANCELLED", exit_reason="ioc_expired"),
    ])
    section = dr.build_trade_chain_section(tmp_path, since=since)
    assert section["clean"] is True
    assert section["filled_count"] == 1
    assert section["cancelled_nofill_count"] == 1
    assert section["resolved_count"] == 2
    assert section["orphan_count"] == 0
    assert section["broker_parity_status"] == "UNKNOWN"


def test_trade_chain_legitimate_open_not_flagged(tmp_path):
    since = datetime(2026, 8, 5, 0, 0, tzinfo=timezone.utc)
    _write_jsonl(tmp_path / "journal_2026-08-05.jsonl", [
        _trade("2026-08-05T19:00:00+00:00"),  # unresolved, but recent
    ])
    section = dr.build_trade_chain_section(
        tmp_path, since=since,
    )
    assert section["still_open_count"] == 1
    assert section["legitimate_open_count"] == 1
    assert section["orphan_count"] == 0
    assert section["clean"] is True


def test_trade_chain_orphan_detected_when_stale_and_no_order_evidence(tmp_path):
    since = datetime(2026, 8, 5, 0, 0, tzinfo=timezone.utc)
    _write_jsonl(tmp_path / "journal_2026-08-05.jsonl", [
        _trade("2026-08-05T05:00:00+00:00"),  # 14h before the last entry below -> stale
        _trade("2026-08-05T19:00:00+00:00", instrument="MES"),  # keeps "now_ref" recent
    ])
    section = dr.build_trade_chain_section(tmp_path, since=since, stale_after_hours=6)
    assert section["stale_order_count"] == 1
    assert section["orphan_count"] == 1
    assert section["clean"] is False


def test_trade_chain_stale_but_not_orphan_when_order_ids_present(tmp_path):
    since = datetime(2026, 8, 5, 0, 0, tzinfo=timezone.utc)
    _write_jsonl(tmp_path / "journal_2026-08-05.jsonl", [
        _trade("2026-08-05T05:00:00+00:00"),
        _order_ids("2026-08-05T05:01:00+00:00"),  # corroborates the trade reached the broker
        _trade("2026-08-05T19:00:00+00:00", instrument="MES"),
    ])
    section = dr.build_trade_chain_section(tmp_path, since=since, stale_after_hours=6)
    assert section["stale_order_count"] == 1
    assert section["orphan_count"] == 0  # has order-id evidence, so not an orphan
    assert section["clean"] is False  # still flagged: fills/attempts mismatch from the stale open position


def test_trade_chain_duplicate_order_ids_flagged(tmp_path):
    since = datetime(2026, 8, 5, 0, 0, tzinfo=timezone.utc)
    _write_jsonl(tmp_path / "journal_2026-08-05.jsonl", [
        _trade("2026-08-05T14:00:00+00:00"),
        _order_ids("2026-08-05T14:00:30+00:00", parent="DUP1", stop="S1", target="T1"),
        _outcome("2026-08-05T14:05:00+00:00", result="WIN"),
        _trade("2026-08-05T15:00:00+00:00", strategy="orb_breakout"),
        _order_ids("2026-08-05T15:00:30+00:00", parent="DUP1", stop="S2", target="T2"),
        _outcome("2026-08-05T15:05:00+00:00", result="WIN"),
    ])
    section = dr.build_trade_chain_section(tmp_path, since=since)
    assert len(section["duplicate_order_identities"]) == 1
    assert section["duplicate_order_identities"][0]["order_id"] == "DUP1"
    assert section["clean"] is False


def test_trade_chain_since_filters_out_old_entries(tmp_path):
    since = datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc)
    _write_jsonl(tmp_path / "journal_2026-08-04.jsonl", [
        _trade("2026-08-04T14:00:00+00:00"),
        _outcome("2026-08-04T14:05:00+00:00", result="LOSS"),
    ])
    _write_jsonl(tmp_path / "journal_2026-08-05.jsonl", [
        _trade("2026-08-05T14:00:00+00:00"),
        _outcome("2026-08-05T14:05:00+00:00", result="WIN"),
    ])
    section = dr.build_trade_chain_section(tmp_path, since=since)
    assert section["resolved_count"] == 1
    assert section["filled_count"] == 1


def test_trade_chain_empty_journal_dir_is_clean(tmp_path):
    since = datetime(2026, 8, 5, tzinfo=timezone.utc)
    section = dr.build_trade_chain_section(tmp_path, since=since)
    assert section["clean"] is True
    assert section["resolved_count"] == 0
    assert dr.format_trade_chain_summary(section).startswith("TRADE CHAIN: PASS")


# ─── since / checkpoint ─────────────────────────────────────────────────────

def test_parse_since_date_only():
    now = datetime(2026, 8, 5, 12, tzinfo=timezone.utc)
    parsed = dr._parse_since("2026-08-01", now=now)
    assert parsed == datetime(2026, 8, 1, tzinfo=timezone.utc)


def test_parse_since_full_iso():
    now = datetime(2026, 8, 5, 12, tzinfo=timezone.utc)
    parsed = dr._parse_since("2026-08-01T03:04:05Z", now=now)
    assert parsed == datetime(2026, 8, 1, 3, 4, 5, tzinfo=timezone.utc)


def test_checkpoint_roundtrip(repo):
    now = datetime(2026, 8, 5, 12, tzinfo=timezone.utc)
    assert dr.load_checkpoint(repo) is None
    written, path = dr.write_checkpoint(repo, now=now)
    assert written is True
    checkpoint = dr.load_checkpoint(repo)
    assert checkpoint["checkpoint_ts"] == now.isoformat()

    # Never tracked.
    tracked = _run(["ls-files", "--", Path(path).name], repo).stdout
    assert tracked.strip() == ""


def test_resolve_since_explicit_overrides_checkpoint(repo):
    now = datetime(2026, 8, 5, 12, tzinfo=timezone.utc)
    dr.write_checkpoint(repo, now=now - timedelta(days=10))
    since, source = dr.resolve_since(repo, "2026-08-04", now=now)
    assert since == datetime(2026, 8, 4, tzinfo=timezone.utc)
    assert source == "explicit --since"


def test_resolve_since_uses_checkpoint_when_no_since_arg(repo):
    now = datetime(2026, 8, 5, 12, tzinfo=timezone.utc)
    checkpoint_ts = now - timedelta(days=2)
    dr.write_checkpoint(repo, now=checkpoint_ts)
    since, source = dr.resolve_since(repo, None, now=now)
    assert since == checkpoint_ts
    assert "checkpoint" in source


def test_resolve_since_defaults_to_24h_lookback(repo):
    now = datetime(2026, 8, 5, 12, tzinfo=timezone.utc)
    since, source = dr.resolve_since(repo, None, now=now)
    assert since == now - timedelta(hours=24)
    assert "default" in source


# ─── Section A smoke (real hermetic repo, gh unavailable) ─────────────────

def test_build_repo_reconciliation_section_smoke(repo):
    section = dr.build_repo_reconciliation_section(repo, now=datetime(2026, 8, 5, tzinfo=timezone.utc))
    assert section["open_prs"] == "UNKNOWN"
    assert section["stash_count"] == 0
    assert isinstance(section["active_worktrees"], list)


# ─── End-to-end report ─────────────────────────────────────────────────────

def test_build_daily_reconciliation_report_end_to_end(repo):
    journal_dir = repo / "logs"
    journal_dir.mkdir()
    now = datetime(2026, 8, 5, 20, 0, tzinfo=timezone.utc)
    _write_jsonl(journal_dir / "journal_2026-08-05.jsonl", [
        _trade("2026-08-05T14:00:00+00:00"),
        _outcome("2026-08-05T14:05:00+00:00", result="WIN"),
    ])
    (repo / "docs" / "strategy-rules").mkdir(parents=True)
    (repo / "docs" / "strategy-rules" / "Strategy_Inventory.md").write_text(
        "## Master Table\n\n| Strategy | Verdict |\n|---|---|\n", encoding="utf-8",
    )

    report = dr.build_daily_reconciliation_report(repo_root=repo, journal_dir=journal_dir, now=now)
    assert report["ok"] is True
    assert report["checkpoint_written"] is True
    assert report["section_e_trade_chain_integrity"]["filled_count"] == 1
    text = dr.format_daily_reconciliation_report(report)
    assert "DAILY RECONCILIATION" in text
    assert "TRADE CHAIN" in text

    checkpoint = dr.load_checkpoint(repo)
    assert checkpoint["checkpoint_ts"] == now.isoformat()

    # A second run with no --since should now pick up the checkpoint from the first run.
    report2 = dr.build_daily_reconciliation_report(repo_root=repo, journal_dir=journal_dir, now=now + timedelta(hours=1))
    assert "checkpoint" in report2["since_source"]


def test_build_daily_reconciliation_report_not_a_repo(tmp_path):
    not_repo = tmp_path / "not_a_repo"
    not_repo.mkdir()
    report = dr.build_daily_reconciliation_report(repo_root=not_repo)
    assert report["ok"] is False
