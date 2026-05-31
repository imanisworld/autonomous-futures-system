"""
tests/test_adaptive.py

Tests for the Adaptive Risk Committee layer.

Covers:
  - PayloadAuditor  — flags missing bracket, null trend_strength, bracket mismatch
  - RiskSteward     — flags drawdown breach, daily loss, consecutive losses, tier jump
  - StrategyAnalyst — insufficient sample → no recommendation; negative expectancy → PAUSE
  - Committee       — read-only (no journal mutation), stable JSON on edge cases
  - API endpoints   — /status/adaptive and /status/adaptive/history
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import pytest

from adaptive.models import (
    TradeRecord,
    PAYLOAD_FIX_REQUIRED, PAUSE_STRATEGY, WATCH, REDUCE_SIZE,
    sample_sufficiency,
)
from adaptive.payload_auditor import PayloadAuditor
from adaptive.risk_steward import RiskSteward
from adaptive.strategy_analyst import StrategyAnalyst
from adaptive.ops_monitor import OpsMonitor
from adaptive.committee import AdaptiveCommittee
from adaptive.journal_reader import JournalReader


# ─── Fixtures ────────────────────────────────────────────────────────────────

def _trade(
    *,
    result: Optional[str] = "WIN",
    pnl: float = 100.0,
    strategy: str = "orb_breakout",
    session: str = "new_york",
    contracts: int = 1,
    trend_strength: Optional[str] = "STRONG",
    vwap_value: Optional[float] = 5000.0,
    volume: Optional[int] = 500,
    entry: Optional[float] = 5000.0,
    stop: Optional[float] = 4990.0,
    target: Optional[float] = 5020.0,
    pine_bracket_ignored: bool = False,
    pine_bracket_overridden: bool = False,
    day_offset: int = 0,
) -> TradeRecord:
    day = (date.today() - timedelta(days=day_offset)).isoformat()
    return TradeRecord(
        date=day,
        ts=f"{day}T14:30:00+00:00",
        instrument="MES",
        session=session,
        strategy=strategy,
        direction="LONG",
        contracts=contracts,
        confluence_grade="A",
        entry=entry,
        stop=stop,
        target=target,
        rr_ratio=2.0,
        result=result,
        pnl_dollars=pnl,
        trend_strength=trend_strength,
        vwap_value=vwap_value,
        volume=volume,
        pine_bracket_overridden=pine_bracket_overridden,
        pine_bracket_ignored=pine_bracket_ignored,
    )


def _loss(pnl: float = -100.0, **kw) -> TradeRecord:
    return _trade(result="LOSS", pnl=pnl, **kw)


# ─── sample_sufficiency ───────────────────────────────────────────────────────

def test_sample_sufficiency_thresholds():
    assert sample_sufficiency(0)  == "insufficient_sample"
    assert sample_sufficiency(9)  == "insufficient_sample"
    assert sample_sufficiency(10) == "early_signal"
    assert sample_sufficiency(29) == "early_signal"
    assert sample_sufficiency(30) == "actionable"
    assert sample_sufficiency(99) == "actionable"


# ─── PayloadAuditor ───────────────────────────────────────────────────────────

def test_payload_auditor_empty_trades():
    report = PayloadAuditor().audit([])
    assert report.status == "OK"
    assert report.recommendations == []
    assert report.findings["audited"] == 0


def test_payload_auditor_flags_missing_bracket():
    trades = [_trade(entry=None, stop=None, target=None)] * 3
    report = PayloadAuditor().audit(trades)
    assert report.status == "WARNING"
    codes = [r.code for r in report.recommendations]
    assert PAYLOAD_FIX_REQUIRED in codes
    subj = [r.subject for r in report.recommendations]
    assert "pine_advisory_bracket" in subj
    assert report.findings["missing_bracket"] == 3


def test_payload_auditor_flags_null_trend_strength():
    # 4/5 trades with null trend_strength = 80% > 30% threshold
    trades = [_trade(trend_strength=None)] * 4 + [_trade()]
    report = PayloadAuditor().audit(trades)
    codes = [r.code for r in report.recommendations]
    assert PAYLOAD_FIX_REQUIRED in codes
    subjects = [r.subject for r in report.recommendations]
    assert "trend_strength_field" in subjects


def test_payload_auditor_ok_when_trend_null_below_threshold():
    # Only 1/5 = 20% < 30% threshold — no recommendation
    trades = [_trade(trend_strength=None)] + [_trade()] * 4
    report = PayloadAuditor().audit(trades)
    subjects = [r.subject for r in report.recommendations]
    assert "trend_strength_field" not in subjects


def test_payload_auditor_flags_bracket_mismatch():
    trades = [_trade(pine_bracket_ignored=True)] * 2 + [_trade()] * 5
    report = PayloadAuditor().audit(trades)
    codes = [r.code for r in report.recommendations]
    assert PAYLOAD_FIX_REQUIRED in codes
    subjects = [r.subject for r in report.recommendations]
    assert "pine_strategy_name_mismatch" in subjects
    assert report.findings["pine_bracket_ignored"] == 2


def test_payload_auditor_flags_null_vwap_above_threshold():
    # 6/8 = 75% > 50%
    trades = [_trade(vwap_value=None)] * 6 + [_trade()] * 2
    report = PayloadAuditor().audit(trades)
    subjects = [r.subject for r in report.recommendations]
    assert "vwap_field" in subjects


def test_payload_auditor_ok_when_all_fields_present():
    trades = [_trade()] * 10
    report = PayloadAuditor().audit(trades)
    assert report.status == "OK"
    assert report.recommendations == []


# ─── RiskSteward ─────────────────────────────────────────────────────────────

def test_risk_steward_empty():
    report = RiskSteward().audit([])
    assert report.status == "OK"
    assert report.recommendations == []


def test_risk_steward_flags_drawdown_breach():
    # Start: $1500. 10 big losses → deep drawdown
    steward = RiskSteward(starting_balance=1500.0, max_drawdown_percent=0.20)
    losses = [_loss(pnl=-200.0)] * 10  # -$2000 total
    report = steward.audit(losses)
    assert report.status in ("WARNING", "CRITICAL")
    codes = [r.code for r in report.recommendations]
    assert REDUCE_SIZE in codes


def test_risk_steward_flags_daily_loss_breach():
    # One day: 3 losses totalling -$200 on 1c = exceeds $150 limit
    steward = RiskSteward(starting_balance=1500.0, max_daily_loss_per_contract=150.0)
    losses = [_loss(pnl=-70.0, contracts=1)] * 3  # -$210 on 1 day
    report = steward.audit(losses)
    subjects = [r.subject for r in report.recommendations]
    assert "daily_loss_limit" in subjects


def test_risk_steward_flags_circuit_breaker_streak():
    steward = RiskSteward(circuit_breaker_losses=3)
    trades = [_loss()] * 3 + [_trade()]
    report = steward.audit(trades)
    subjects = [r.subject for r in report.recommendations]
    assert "consecutive_losses" in subjects
    assert report.findings["max_consecutive_losses"] == 3


def test_risk_steward_flags_tier_jump():
    steward = RiskSteward()
    trades = [_trade(contracts=1)] * 5 + [_trade(contracts=4)] * 5
    report = steward.audit(trades)
    subjects = [r.subject for r in report.recommendations]
    assert "contract_tier_scaling" in subjects


def test_risk_steward_healthy_no_recommendations():
    steward = RiskSteward(starting_balance=1500.0)
    trades = [_trade(pnl=50.0)] * 5  # small consistent wins, no issues
    report = steward.audit(trades)
    # Drawdown is zero (always winning), no daily loss breach, no streak
    dd_recs = [r for r in report.recommendations if r.subject == "drawdown"]
    loss_recs = [r for r in report.recommendations if r.subject == "daily_loss_limit"]
    assert dd_recs == []
    assert loss_recs == []


def test_risk_steward_scale_up_eligible():
    steward = RiskSteward(starting_balance=1500.0, max_drawdown_percent=0.20)
    # 30 wins, no losses
    trades = [_trade(pnl=50.0)] * 30
    report = steward.audit(trades)
    keep_recs = [r for r in report.recommendations if r.code == "KEEP_ACTIVE"]
    assert len(keep_recs) >= 1
    assert "scale_up_eligible" in [r.subject for r in keep_recs]


# ─── StrategyAnalyst ─────────────────────────────────────────────────────────

def test_strategy_analyst_insufficient_sample_no_recs():
    # 5 losing trades — below 10 threshold, no recommendation
    trades = [_loss(strategy="orb_breakout")] * 5
    report = StrategyAnalyst().audit(trades)
    recs = [r for r in report.recommendations if r.subject == "orb_breakout"]
    assert recs == [], "Must not recommend on fewer than 10 trades"


def test_strategy_analyst_early_signal_only_watch():
    # 15 trades, bad win rate + PF < 1
    trades = (
        [_trade(strategy="vwap_hold", pnl=50.0)] * 4
        + [_loss(strategy="vwap_hold", pnl=-200.0)] * 11
    )
    report = StrategyAnalyst().audit(trades)
    recs = [r for r in report.recommendations if r.subject == "vwap_hold"]
    # WATCH may appear; PAUSE_STRATEGY must NOT appear at early_signal level
    assert all(r.code != PAUSE_STRATEGY for r in recs)


def test_strategy_analyst_flags_negative_expectancy_at_actionable():
    # 30+ trades with negative expectancy
    trades = (
        [_trade(strategy="pdh_reclaim", pnl=30.0)] * 10
        + [_loss(strategy="pdh_reclaim", pnl=-100.0)] * 25
    )
    report = StrategyAnalyst().audit(trades)
    recs = [r for r in report.recommendations if r.subject == "pdh_reclaim"]
    assert any(r.code == PAUSE_STRATEGY for r in recs)


def test_strategy_analyst_flags_profit_factor_below_1():
    trades = (
        [_trade(strategy="strat_122", pnl=20.0)] * 15
        + [_loss(strategy="strat_122", pnl=-60.0)] * 20
    )
    report = StrategyAnalyst().audit(trades)
    recs = [r for r in report.recommendations if r.subject == "strat_122"]
    assert any(r.code == PAUSE_STRATEGY for r in recs)


def test_strategy_analyst_keep_active_for_strong_performer():
    trades = [_trade(strategy="orb_breakout", pnl=100.0)] * 35
    report = StrategyAnalyst().audit(trades)
    recs = [r for r in report.recommendations if r.subject == "orb_breakout"]
    assert any(r.code == "KEEP_ACTIVE" for r in recs)


def test_strategy_analyst_empty():
    report = StrategyAnalyst().audit([])
    assert report.status == "OK"
    assert report.recommendations == []


# ─── OpsMonitor ──────────────────────────────────────────────────────────────

def test_ops_monitor_healthy(tmp_path):
    report = OpsMonitor(tmp_path).audit(latest_entry_age=60)
    assert report.findings["log_dir_exists"] is True
    assert report.findings["log_dir_writable"] is True


def test_ops_monitor_no_journal_files(tmp_path):
    report = OpsMonitor(tmp_path).audit()
    subjects = [r.subject for r in report.recommendations]
    assert "journal_files" in subjects


def test_ops_monitor_stale_feed_warning(tmp_path):
    # 5 hours old → WARNING
    report = OpsMonitor(tmp_path).audit(latest_entry_age=5 * 3600)
    statuses = [r.subject for r in report.recommendations]
    assert "webhook_feed" in statuses
    assert report.status == "WARNING"


def test_ops_monitor_stale_feed_critical(tmp_path):
    # 25 hours old → CRITICAL
    report = OpsMonitor(tmp_path).audit(latest_entry_age=25 * 3600)
    assert report.status == "CRITICAL"


def test_ops_monitor_missing_log_dir():
    from pathlib import Path
    report = OpsMonitor(Path("/nonexistent/path/xyz")).audit()
    assert report.status == "CRITICAL"
    subjects = [r.subject for r in report.recommendations]
    assert "log_directory" in subjects


# ─── JournalReader ────────────────────────────────────────────────────────────

def _write_journal(log_dir: Path, day: date, entries: list[dict]) -> None:
    path = log_dir / f"journal_{day.isoformat()}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


def _approved_entry(
    strategy: str = "orb_breakout",
    session: str = "new_york",
    ts: str = "2026-05-23T14:30:00+00:00",
    outcome: Optional[dict] = None,
    trend_strength: str = "STRONG",
) -> dict:
    return {
        "ts": ts,
        "instrument": "MES",
        "session": session,
        "decision": "TRADE",
        "reason": "orb_breakout",
        "market_condition": "TRENDING",
        "setup": {
            "strategy": strategy,
            "direction": "LONG",
            "entry": 5000.0,
            "stop": 4990.0,
            "target": 5020.0,
            "rr_ratio": 2.0,
            "contracts": 1,
            "notes": None,
        },
        "risk_check": {"result": "APPROVED", "failed_rule": None, "reason": None},
        "confluence": {"grade": "A", "score": 75, "factors": [], "penalties": []},
        "context": {
            "trend": {"direction": "UP", "strength": trend_strength},
            "vwap": {"value": 5000.0, "price_vs_vwap": "above", "reclaimed": False, "holding": True},
            "volume": {"current_bar": 500, "avg_bar": 400, "relative": 1.25},
        },
        "outcome": outcome,
    }


def _outcome_entry(result: str = "WIN", pnl_dollars: float = 100.0) -> dict:
    return {
        "ts": "2026-05-23T15:00:00+00:00",
        "type": "OUTCOME",
        "instrument": "MES",
        "session": "new_york",
        "outcome": {
            "result": result,
            "entry_price": 5000.0,
            "exit_price": 5020.0 if result == "WIN" else 4990.0,
            "exit_reason": "TARGET_HIT" if result == "WIN" else "STOP_HIT",
            "pnl_ticks": 40 if result == "WIN" else -40,
            "pnl_dollars": pnl_dollars,
            "contracts": 1,
        },
    }


def test_journal_reader_reads_standalone_outcome(tmp_path):
    today = date.today()
    _write_journal(tmp_path, today, [
        _approved_entry(),
        _outcome_entry(result="WIN", pnl_dollars=150.0),
    ])
    reader = JournalReader(tmp_path)
    trades = reader.read_trades(days=1)
    assert len(trades) == 1
    assert trades[0].result == "WIN"
    assert trades[0].pnl_dollars == 150.0


def test_journal_reader_open_trade_has_no_result(tmp_path):
    today = date.today()
    _write_journal(tmp_path, today, [_approved_entry()])
    trades = JournalReader(tmp_path).read_trades(days=1)
    assert len(trades) == 1
    assert trades[0].result is None
    assert trades[0].pnl_dollars is None


def test_journal_reader_no_journal_returns_empty(tmp_path):
    trades = JournalReader(tmp_path).read_trades(days=7)
    assert trades == []


def test_journal_reader_extracts_payload_fields(tmp_path):
    today = date.today()
    _write_journal(tmp_path, today, [
        _approved_entry(trend_strength="STRONG"),
        _outcome_entry(result="WIN"),
    ])
    trades = JournalReader(tmp_path).read_trades(days=1)
    assert trades[0].trend_strength == "STRONG"
    assert trades[0].vwap_value == 5000.0
    assert trades[0].volume == 500


# ─── Committee (orchestration + read-only) ───────────────────────────────────

def test_committee_produces_report_with_no_journal(tmp_path, config):
    committee = AdaptiveCommittee(log_dir=tmp_path, config=config)
    report = committee.run(days=7)
    assert report.overall_status in ("OK", "WARNING", "CRITICAL")
    assert isinstance(report.sample_size, int)
    assert report.sample_sufficiency == "insufficient_sample"
    assert len(report.agents) == 4


def test_committee_does_not_mutate_journal(tmp_path, config):
    today = date.today()
    journal_path = tmp_path / f"journal_{today.isoformat()}.jsonl"
    _write_journal(tmp_path, today, [_approved_entry(), _outcome_entry()])
    mtime_before = journal_path.stat().st_mtime

    AdaptiveCommittee(log_dir=tmp_path, config=config).run(days=1)

    assert journal_path.stat().st_mtime == mtime_before, "Committee must not modify journal files"


def test_committee_persists_json_artifact(tmp_path, config):
    today = date.today()
    committee = AdaptiveCommittee(log_dir=tmp_path, config=config)
    report = committee.run_and_persist(days=7)
    artifact = tmp_path / f"adaptive_review_{today.isoformat()}.json"
    assert artifact.exists()
    data = json.loads(artifact.read_text())
    assert data["date"] == today.isoformat()
    assert data["overall_status"] == report.overall_status
    assert len(data["agents"]) == 4


def test_committee_load_cached_returns_none_when_absent(tmp_path, config):
    committee = AdaptiveCommittee(log_dir=tmp_path, config=config)
    assert committee.load_cached() is None


def test_committee_load_cached_returns_data_after_persist(tmp_path, config):
    committee = AdaptiveCommittee(log_dir=tmp_path, config=config)
    committee.run_and_persist(days=7)
    cached = committee.load_cached()
    assert cached is not None
    assert "overall_status" in cached


def test_committee_load_history_multiple_days(tmp_path, config):
    committee = AdaptiveCommittee(log_dir=tmp_path, config=config)
    # Write two days of artifacts manually
    today = date.today()
    yesterday = today - __import__("datetime").timedelta(days=1)
    for day in [today, yesterday]:
        path = tmp_path / f"adaptive_review_{day.isoformat()}.json"
        path.write_text(json.dumps({
            "date": day.isoformat(),
            "overall_status": "OK",
            "agents": [],
            "top_recommendations": [],
            "sample_size": 0,
            "sample_sufficiency": "insufficient_sample",
        }))
    history = committee.load_history(days=7)
    assert len(history) == 2


def test_committee_status_only_no_trade_entries(tmp_path, config):
    today = date.today()
    no_trade = {
        "ts": "2026-05-23T14:30:00+00:00",
        "instrument": "MES",
        "session": "new_york",
        "decision": "NO_TRADE",
        "reason": "CHOPPY",
    }
    _write_journal(tmp_path, today, [no_trade] * 5)
    committee = AdaptiveCommittee(log_dir=tmp_path, config=config)
    report = committee.run(days=1)
    assert report.sample_size == 0
    assert report.sample_sufficiency == "insufficient_sample"


def test_committee_report_to_dict_is_json_serialisable(tmp_path, config):
    report = AdaptiveCommittee(log_dir=tmp_path, config=config).run(days=1)
    d = report.to_dict()
    # Must serialise without error
    json.dumps(d)
    assert d["overall_status"] in ("OK", "WARNING", "CRITICAL")


def test_committee_with_open_position_does_not_crash(tmp_path, config):
    today = date.today()
    _write_journal(tmp_path, today, [_approved_entry()])  # no outcome = open
    report = AdaptiveCommittee(log_dir=tmp_path, config=config).run(days=1)
    assert report.sample_size == 0  # open trades don't count as resolved


# ─── API endpoints ────────────────────────────────────────────────────────────

def test_adaptive_endpoint_returns_stable_json_no_journal(monkeypatch, tmp_path):
    try:
        from fastapi.testclient import TestClient
        from webhook.app import app
    except ImportError:
        pytest.skip("fastapi[testclient] not installed")

    monkeypatch.setenv("WEBHOOK_SECRET", "test-secret")
    import webhook.app as app_module
    monkeypatch.setattr(app_module._config, "log_dir", str(tmp_path))
    client = TestClient(app)
    resp = client.get("/status/adaptive")
    assert resp.status_code == 200
    data = resp.json()
    assert "overall_status" in data
    assert "sample_size" in data
    assert "agents" in data
    assert data["sample_sufficiency"] == "insufficient_sample"


def test_adaptive_history_endpoint_empty(monkeypatch, tmp_path):
    try:
        from fastapi.testclient import TestClient
        from webhook.app import app
    except ImportError:
        pytest.skip("fastapi[testclient] not installed")

    monkeypatch.setenv("WEBHOOK_SECRET", "test-secret")
    import webhook.app as app_module
    monkeypatch.setattr(app_module._config, "log_dir", str(tmp_path))
    client = TestClient(app)
    resp = client.get("/status/adaptive/history?days=7")
    assert resp.status_code == 200
    data = resp.json()
    assert "days" in data
    assert isinstance(data["days"], list)
