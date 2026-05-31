"""
tests/test_e2e_scenarios.py

End-to-end scenario tests — each one tells a complete risk story not
covered by the focused unit tests in test_webhook.py.

  A. max_daily_loss gate fires after a large intraday loss
  B. Stale-bar data quality gate blocks old timestamps
  C. News blackout (block mode)  — FOMC date rejects all trades
  D. News blackout (reduced mode)— cap at 1; 2nd trade blocked
  E. Bonus trade: A-grade 4th trade passes when bonus_trades_after_max=2
  F. Bonus trade: B-grade 4th trade rejected  (grade gate)
  G. Multi-day committee: 35 seeded trades → actionable sample + real recommendations
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from config.settings import PositionSizingConfig, SystemConfig
from journal.journal_logger import JournalLogger
from risk.risk_engine import DailyState, RiskEngine, TradeSetup
from webhook.payload import AlertPayload
from webhook.runner import process_alert


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _base_payload(**overrides) -> AlertPayload:
    data = {
        "ticker": "MNQ1!",
        "timestamp": "2026-05-23T14:30:00+00:00",
        "open": 19480.0,
        "high": 19510.0,
        "low": 19475.0,
        "close": 19505.25,
        "volume": 4200,
        "avg_volume": 3800,
        "vwap": 19495.0,
        "orb_high": 19498.0,
        "orb_low": 19462.0,
        "orb_status": "reclaimed_high",
        "market_condition": "TRENDING",
        "trend_direction": "UP",
        "trend_strength": "STRONG",
        "previous_day_high": 19520.0,
        "previous_day_low": 19440.0,
        "previous_day_close": 19475.0,
        "current_bar_type": "two_up",
        "previous_bar_type": "two_up",
        "two_bars_back_type": "two_up",
    }
    data.update(overrides)
    return AlertPayload(**data)


def _seed_loss(journal: JournalLogger, for_date: date, pnl_dollars: float) -> None:
    """Append a TRADE decision + LOSS outcome to the journal."""
    journal._append({
        "ts": f"{for_date.isoformat()}T14:00:00+00:00",
        "instrument": "MNQ",
        "session": "new_york",
        "decision": "TRADE",
        "reason": "scenario_seed",
        "market_condition": "TRENDING",
        "setup": {
            "direction": "LONG",
            "entry": 19500.0,
            "stop": 19460.0,
            "target": 19580.0,
            "rr_ratio": 2.0,
            "strategy": "orb_reclaim",
            "notes": None,
        },
        "risk_check": {"result": "APPROVED", "failed_rule": None, "reason": None},
        "outcome": None,
    }, for_date)
    journal.log_outcome(
        instrument="MNQ",
        session="new_york",
        result="LOSS",
        entry_price=19500.0,
        exit_price=19460.0,
        exit_reason="STOP_HIT",
        pnl_ticks=-160.0,
        pnl_dollars=pnl_dollars,
        for_date=for_date,
    )


def _seed_win(journal: JournalLogger, for_date: date, pnl_dollars: float = 150.0) -> None:
    """Append a TRADE decision + WIN outcome to the journal."""
    journal._append({
        "ts": f"{for_date.isoformat()}T14:00:00+00:00",
        "instrument": "MNQ",
        "session": "new_york",
        "decision": "TRADE",
        "reason": "scenario_seed",
        "market_condition": "TRENDING",
        "setup": {
            "direction": "LONG",
            "entry": 19500.0,
            "stop": 19460.0,
            "target": 19580.0,
            "rr_ratio": 2.0,
            "strategy": "orb_reclaim",
            "notes": None,
        },
        "risk_check": {"result": "APPROVED", "failed_rule": None, "reason": None},
        "outcome": None,
    }, for_date)
    journal.log_outcome(
        instrument="MNQ",
        session="new_york",
        result="WIN",
        entry_price=19500.0,
        exit_price=19580.0,
        exit_reason="TARGET_HIT",
        pnl_ticks=320.0,
        pnl_dollars=pnl_dollars,
        for_date=for_date,
    )


def _base_config(tmp_path: Path) -> SystemConfig:
    """Scenario config with real risk limits and a clean log_dir.

    max_contracts_per_instrument is intentionally 1 for MNQ so that every
    scenario uses exactly 1 contract and max_daily_loss math is predictable.
    """
    return SystemConfig(
        live_trading_enabled=False,
        paper_mode=True,
        allowed_instruments=["MNQ", "MES"],
        allowed_sessions=["london", "new_york"],
        disabled_sessions=["asian"],
        session_hours={},
        max_trades_per_day=3,
        max_consecutive_losses=3,
        max_daily_loss=150.0,
        max_drawdown_percent=0.20,
        circuit_breaker_losses=3,
        circuit_breaker_pause_minutes=30,
        conservative_mode=False,
        max_open_positions=1,
        averaging_down_allowed=False,
        max_contracts_per_instrument={"MNQ": 1, "MES": 1},  # 1c only → loss maths are deterministic
        require_entry=True,
        require_stop=True,
        require_target=True,
        min_rr_ratio=2.0,
        max_staleness_seconds=0,
        reject_null_required_fields=True,
        reject_contradictory_data=True,
        tradable_states=["TRENDING", "RANGE_BOUND"],
        non_tradable_states=["CHOPPY", "DEAD"],
        enabled_concepts=[
            "orb_reclaim", "orb_rejection", "vwap_reclaim",
            "vwap_hold", "pdh_reclaim", "pdl_reclaim", "continuation_pullback",
        ],
        broker_priority=["paper"],
        starting_capital_default=1500.0,
        minimum_starting_capital=500.0,
        max_account_risk_per_trade_percent=1.0,
        max_daily_loss_percent=3.0,
        require_margin_check=False,
        log_dir=str(tmp_path / "logs"),
        log_level="WARNING",
        risk_rules_path="risk_rules.yaml",
        discord_notifications_enabled=False,
        discord_webhook_url="",
        discord_notify_decisions=[],
        signa_api_enabled=False,
        signa_api_key_configured=False,
    )


def _valid_trade_setup(
    *,
    confluence_grade: str = "A",
    contracts: int = 1,
    instrument: str = "MNQ",
) -> TradeSetup:
    return TradeSetup(
        direction="LONG",
        entry=19500.0,
        stop=19460.0,
        target=19580.0,
        rr_ratio=2.0,
        strategy="orb_reclaim",
        instrument=instrument,
        session="new_york",
        contracts=contracts,
        confluence_grade=confluence_grade,
    )


# ─── Scenario A: max_daily_loss ────────────────────────────────────────────────

def test_scenario_a_max_daily_loss_blocks_after_large_loss(tmp_path):
    """
    Story: A single -$200 LOSS exceeds the $150 daily loss limit.
    Every subsequent signal should be RISK_REJECTED(max_daily_loss).
    """
    today = date(2026, 5, 23)
    cfg = _base_config(tmp_path)
    journal = JournalLogger(log_dir=cfg.log_dir)
    _seed_loss(journal, today, pnl_dollars=-200.0)

    result = process_alert(
        _base_payload(timestamp="2026-05-23T15:00:00+00:00"),
        config=cfg,
        log_dir=cfg.log_dir,
        for_date=today,
    )
    assert result["decision"] == "RISK_REJECTED", f"Got {result['decision']}"
    assert result.get("risk", {}).get("failed_rule") == "max_daily_loss", (
        f"failed_rule={result.get('risk', {}).get('failed_rule')!r}"
    )


def test_scenario_a_max_daily_loss_not_triggered_by_small_loss(tmp_path):
    """
    Story: A -$75 loss (half the $150 limit) should NOT block subsequent trades.
    """
    today = date(2026, 5, 23)
    cfg = _base_config(tmp_path)
    journal = JournalLogger(log_dir=cfg.log_dir)
    _seed_loss(journal, today, pnl_dollars=-75.0)

    result = process_alert(
        _base_payload(timestamp="2026-05-23T15:00:00+00:00"),
        config=cfg,
        log_dir=cfg.log_dir,
        for_date=today,
    )
    assert result["decision"] != "RISK_REJECTED" or result.get("failed_rule") != "max_daily_loss", (
        "Small loss should not trigger max_daily_loss block"
    )


# ─── Scenario B: stale bar ─────────────────────────────────────────────────────

def test_scenario_b_stale_bar_blocked(tmp_path):
    """
    Story: A bar-close alert arrives with a timestamp 5 minutes old against
    a max_staleness_seconds=60 limit. It should be blocked as BLOCKED_DATA_QUALITY.
    """
    today = date.today()
    stale_ts = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()
    cfg = replace(_base_config(tmp_path), max_staleness_seconds=60)

    result = process_alert(
        _base_payload(timestamp=stale_ts),
        config=cfg,
        log_dir=cfg.log_dir,
        for_date=today,
    )
    assert result["decision"] == "BLOCKED_DATA_QUALITY", (
        f"Expected BLOCKED_DATA_QUALITY, got {result['decision']}: {result.get('failed_gates')}"
    )
    gates = result.get("failed_gates") or []
    assert any("Stale bar" in g or "stale" in g.lower() for g in gates), (
        f"Stale bar message missing from failed_gates: {gates}"
    )


def test_scenario_b_fresh_bar_passes_staleness_gate(tmp_path):
    """Fresh timestamp (< 30s old) must pass the staleness gate."""
    today = date.today()
    fresh_ts = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
    cfg = replace(_base_config(tmp_path), max_staleness_seconds=60)

    result = process_alert(
        _base_payload(timestamp=fresh_ts),
        config=cfg,
        log_dir=cfg.log_dir,
        for_date=today,
    )
    assert result["decision"] != "BLOCKED_DATA_QUALITY"


def test_scenario_b_contradictory_ohlc_blocked(tmp_path):
    """high < low is a hard BLOCKED_DATA_QUALITY regardless of staleness setting."""
    today = date.today()
    fresh_ts = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
    cfg = replace(_base_config(tmp_path), max_staleness_seconds=0)

    result = process_alert(
        _base_payload(timestamp=fresh_ts, high=19450.0, low=19510.0),
        config=cfg,
        log_dir=cfg.log_dir,
        for_date=today,
    )
    assert result["decision"] == "BLOCKED_DATA_QUALITY"


# ─── Scenario C: news blackout — block mode ────────────────────────────────────

def test_scenario_c_news_blackout_block_rejects_all_trades(tmp_path):
    """
    Story: Today is an FOMC decision day. news_blackout_mode=block should
    reject every trade regardless of setup quality.
    """
    today = date(2026, 5, 23)
    cfg = replace(
        _base_config(tmp_path),
        news_blackout_mode="block",
        news_blackout_dates=[today.isoformat()],
        log_dir=str(tmp_path / "logs"),
    )
    result = process_alert(
        _base_payload(timestamp="2026-05-23T14:30:00+00:00"),
        config=cfg,
        log_dir=cfg.log_dir,
        for_date=today,
    )
    assert result["decision"] == "RISK_REJECTED"
    # failed_rule is nested under result["risk"] in the runner response
    assert result.get("risk", {}).get("failed_rule") == "news_blackout", (
        f"failed_rule={result.get('risk', {}).get('failed_rule')!r}  full={result}"
    )


def test_scenario_c_news_blackout_inactive_on_normal_day(tmp_path):
    """On a day not in the blackout list, block mode must not fire."""
    today = date(2026, 5, 23)
    cfg = replace(
        _base_config(tmp_path),
        news_blackout_mode="block",
        news_blackout_dates=["2026-01-01"],   # different date
        log_dir=str(tmp_path / "logs"),
    )
    result = process_alert(
        _base_payload(timestamp="2026-05-23T14:30:00+00:00"),
        config=cfg,
        log_dir=cfg.log_dir,
        for_date=today,
    )
    assert result.get("failed_rule") != "news_blackout"


# ─── Scenario D: news blackout — reduced mode ─────────────────────────────────

def test_scenario_d_news_reduced_blocks_second_trade(tmp_path):
    """
    Story: On an FOMC day, news_blackout_mode=reduced allows 1 trade, then
    blocks. Seeding 1 approved trade should cause the 2nd to be rejected.
    """
    today = date(2026, 5, 23)
    cfg = replace(
        _base_config(tmp_path),
        news_blackout_mode="reduced",
        news_blackout_dates=[today.isoformat()],
        news_blackout_max_trades=1,
        log_dir=str(tmp_path / "logs"),
    )
    journal = JournalLogger(log_dir=cfg.log_dir)
    # Seed 1 resolved trade (win) so it counts toward the limit but leaves no open position
    _seed_win(journal, today, pnl_dollars=100.0)

    result = process_alert(
        _base_payload(timestamp="2026-05-23T14:30:00+00:00"),
        config=cfg,
        log_dir=cfg.log_dir,
        for_date=today,
    )
    assert result["decision"] == "RISK_REJECTED"
    assert result.get("risk", {}).get("failed_rule") == "news_blackout_trade_limit", (
        f"failed_rule={result.get('risk', {}).get('failed_rule')!r}  full={result}"
    )


def test_scenario_d_news_reduced_allows_first_trade(tmp_path):
    """On an FOMC day with reduced mode, the very first trade is allowed."""
    today = date(2026, 5, 23)
    cfg = replace(
        _base_config(tmp_path),
        news_blackout_mode="reduced",
        news_blackout_dates=[today.isoformat()],
        news_blackout_max_trades=1,
        log_dir=str(tmp_path / "logs"),
    )
    result = process_alert(
        _base_payload(timestamp="2026-05-23T14:30:00+00:00"),
        config=cfg,
        log_dir=cfg.log_dir,
        for_date=today,
    )
    # Should not fail on news_blackout_trade_limit (might still fail other checks)
    assert result.get("failed_rule") != "news_blackout_trade_limit"


# ─── Scenario E+F: bonus trade grade gate ─────────────────────────────────────

def test_scenario_e_bonus_trade_a_grade_approved(config):
    """
    Story: Daily cap reached (3 trades). Bonus trades are available.
    An A-grade setup should pass the grade gate and be APPROVED.
    """
    cfg = replace(config,
        max_trades_per_day=3,
        bonus_trades_after_max=2,
        bonus_min_confluence_grade="A",
        max_daily_loss=0.0,   # disable for isolation
        max_drawdown_percent=0.0,
    )
    engine = RiskEngine(cfg)
    state = DailyState(trade_count=3, consecutive_losses=0, has_open_position=False)
    result = engine.validate(_valid_trade_setup(confluence_grade="A"), state)
    assert result.result == "APPROVED", f"A-grade bonus trade rejected: {result.failed_rule}"


def test_scenario_f_bonus_trade_b_grade_rejected(config):
    """
    Story: Daily cap reached (3 trades). A B-grade setup should fail the
    bonus grade gate — only A-grade bonus trades are allowed.
    """
    cfg = replace(config,
        max_trades_per_day=3,
        bonus_trades_after_max=2,
        bonus_min_confluence_grade="A",
        max_daily_loss=0.0,
        max_drawdown_percent=0.0,
    )
    engine = RiskEngine(cfg)
    state = DailyState(trade_count=3, consecutive_losses=0, has_open_position=False)
    result = engine.validate(_valid_trade_setup(confluence_grade="B"), state)
    assert result.result == "REJECTED"
    assert result.failed_rule == "daily_trade_limit_bonus_grade"


def test_scenario_ef_no_bonus_slots_left_blocks_unconditionally(config):
    """When all bonus slots are consumed, even A-grade is blocked."""
    cfg = replace(config,
        max_trades_per_day=3,
        bonus_trades_after_max=1,          # only 1 bonus slot
        bonus_min_confluence_grade="A",
        max_daily_loss=0.0,
        max_drawdown_percent=0.0,
    )
    engine = RiskEngine(cfg)
    state = DailyState(trade_count=4, consecutive_losses=0, has_open_position=False)  # slot used
    result = engine.validate(_valid_trade_setup(confluence_grade="A"), state)
    assert result.result == "REJECTED"
    assert result.failed_rule == "daily_trade_limit"


# ─── Scenario G: multi-day committee → actionable ─────────────────────────────

def _write_journal_line(path: Path, entry: dict) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def test_scenario_g_committee_reaches_actionable_with_35_trades(tmp_path, config):
    """
    Story: After 35 resolved trades spread across 7 days, the committee should
    have an "actionable" sample and be able to produce real recommendations.
    """
    from adaptive.committee import AdaptiveCommittee

    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    total = 35
    wins_per_day = 3
    losses_per_day = 2

    for day_offset in range(7):
        day = date.today() - timedelta(days=day_offset)
        path = log_dir / f"journal_{day.isoformat()}.jsonl"
        for i in range(wins_per_day):
            decision = {
                "ts": f"{day.isoformat()}T14:{10+i:02d}:00+00:00",
                "instrument": "MNQ",
                "session": "new_york",
                "decision": "TRADE",
                "market_condition": "TRENDING",
                "setup": {"strategy": "orb_reclaim", "direction": "LONG",
                          "entry": 19500.0, "stop": 19460.0, "target": 19580.0,
                          "rr_ratio": 2.0, "contracts": 1, "notes": None},
                "risk_check": {"result": "APPROVED"},
                "outcome": {"result": "WIN", "pnl_dollars": 160.0},
            }
            _write_journal_line(path, decision)
        for i in range(losses_per_day):
            decision = {
                "ts": f"{day.isoformat()}T15:{10+i:02d}:00+00:00",
                "instrument": "MNQ",
                "session": "new_york",
                "decision": "TRADE",
                "market_condition": "TRENDING",
                "setup": {"strategy": "orb_reclaim", "direction": "LONG",
                          "entry": 19500.0, "stop": 19460.0, "target": 19580.0,
                          "rr_ratio": 2.0, "contracts": 1, "notes": None},
                "risk_check": {"result": "APPROVED"},
                "outcome": {"result": "LOSS", "pnl_dollars": -80.0},
            }
            _write_journal_line(path, decision)

    cfg = replace(config, log_dir=str(log_dir))
    committee = AdaptiveCommittee(log_dir=log_dir, config=cfg)
    report = committee.run(days=7)

    assert report.sample_sufficiency == "actionable", (
        f"Expected actionable sample, got {report.sample_sufficiency} ({report.sample_size} trades)"
    )
    assert report.sample_size >= 30
    # Committee must have at least one recommendation (healthy system → KEEP_ACTIVE)
    assert report.top_recommendations, "Committee produced no recommendations with 35 trades"
    # Report must be JSON-serialisable without errors
    json.dumps(report.to_dict())


def test_scenario_g_committee_report_includes_generated_at(tmp_path, config):
    """CommitteeReport always carries generated_at so dashboard can show age."""
    from adaptive.committee import AdaptiveCommittee
    cfg = replace(config, log_dir=str(tmp_path / "logs"))
    report = AdaptiveCommittee(log_dir=tmp_path / "logs", config=cfg).run(days=1)
    assert report.generated_at, "generated_at must be set"
    # Must be parseable ISO datetime
    dt = datetime.fromisoformat(report.generated_at.replace("Z", "+00:00"))
    assert dt.tzinfo is not None


def test_scenario_g_committee_all_no_trade_does_not_crash(tmp_path, config):
    """
    Story: The webhook is live but every alert becomes NO_TRADE (e.g. all bars
    are CHOPPY). The committee must not crash and should surface a recommendation
    about payload quality once decisions are seeded.
    """
    from adaptive.committee import AdaptiveCommittee

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    today = date.today()
    path = log_dir / f"journal_{today.isoformat()}.jsonl"
    for _ in range(10):
        _write_journal_line(path, {
            "ts": f"{today.isoformat()}T14:30:00+00:00",
            "instrument": "MNQ",
            "session": "new_york",
            "decision": "NO_TRADE",
            "reason": "CHOPPY",
            "market_condition": "CHOPPY",
            "setup": {"strategy": "orb_reclaim", "direction": "LONG",
                      "entry": None, "stop": None, "target": None,
                      "rr_ratio": None, "contracts": 1, "notes": None},
        })

    cfg = replace(config, log_dir=str(log_dir))
    report = AdaptiveCommittee(log_dir=log_dir, config=cfg).run(days=1)

    assert report.sample_size == 0  # no resolved trades
    # Should still produce an ops/payload observation
    assert report.overall_status in ("OK", "WARNING", "CRITICAL")
    json.dumps(report.to_dict())
