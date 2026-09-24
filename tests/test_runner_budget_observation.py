"""Shared-journal daily budget must block EXECUTION, not OBSERVATION.

Defect (proven on the box 2026-09-16): ``webhook/runner.py`` Step 2 returned
``BLOCKED_MAX_TRADES`` / ``BLOCKED_LOSS_LOCKOUT`` *before* the decision engine
ran, so once the shared main journal held ``max_trades_per_day`` approved
TRADE rows (from ANY lane that journals there, including paper_sim proof
lanes) every later bar was invisible: no candidate audit, no journal row, no
setup snapshot, no why-no-trade reason.  The daily-loss cap lives in the
RiskEngine (after evaluation) and never had this problem; it is pinned here
so the two paths stay symmetric.

These tests do not change any limit.  They prove that at the limit a valid
setup is still recorded and execution is still refused.
"""
from __future__ import annotations

import dataclasses
import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from journal.journal_logger import JournalLogger
from tests.test_webhook import _base_payload
from webhook.runner import process_alert

TODAY = date(2026, 5, 23)
_TS = "2026-05-23T14:00:00+00:00"


def _trade_payload(minute: int):
    """A payload that produces a TRADE decision under the ``config`` fixture."""
    ts = datetime(2026, 5, 23, 14, minute, 0, tzinfo=timezone.utc).isoformat()
    return _base_payload(timestamp=ts)


def _seed_approved_trade(journal: JournalLogger, result: str, pnl: float) -> None:
    """One counted, resolved trade row in the SHARED main journal — the exact
    shape ``_compute_daily_state`` counts (decision TRADE + risk APPROVED)."""
    journal._append(
        {
            "ts": _TS,
            "instrument": "MNQ",
            "session": "new_york",
            "decision": "TRADE",
            "reason": "seed",
            "market_condition": "TRENDING",
            "setup": {
                "direction": "LONG", "entry": 19500.0, "stop": 19460.0,
                "target": 19580.0, "rr_ratio": 2.0, "strategy": "orb_reclaim",
                "notes": None,
            },
            "risk_check": {"result": "APPROVED", "failed_rule": None, "reason": None},
            "outcome": None,
        },
        TODAY,
    )
    journal.log_outcome(
        instrument="MNQ", session="new_york", result=result,
        entry_price=19500.0, exit_price=19580.0 if result == "WIN" else 19460.0,
        exit_reason="TARGET_HIT" if result == "WIN" else "STOP_HIT",
        pnl_ticks=pnl / 0.5, pnl_dollars=pnl, for_date=TODAY,
    )


def _rows(log_dir: str) -> list[dict]:
    path = Path(log_dir) / f"journal_{TODAY.isoformat()}.jsonl"
    if not path.exists():
        return []
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    # decision rows only (bar-claim / outcome / block-visibility rows are separate types)
    return [r for r in rows if r.get("decision") is not None]


def _opportunity_rows(log_dir: str) -> list[dict]:
    out = []
    for path in sorted((Path(log_dir) / "opportunities").glob("*.jsonl")):
        out.extend(json.loads(l) for l in path.read_text().splitlines() if l.strip())
    return out


def _assert_setup_produces_trade_when_budget_free(config, tmp_path):
    """Control: the payload used below IS a valid setup on a fresh day."""
    result = process_alert(
        _trade_payload(30), config=config, log_dir=str(tmp_path / "control"), for_date=TODAY
    )
    assert result["decision"] == "TRADE", result
    return result


# ─── max trades/day ──────────────────────────────────────────────────────────

def test_max_trades_reached_setup_still_observed_but_not_executed(config, tmp_path):
    _assert_setup_produces_trade_when_budget_free(config, tmp_path)
    log_dir = str(tmp_path / "logs")
    journal = JournalLogger(log_dir=log_dir)
    for _ in range(config.max_trades_per_day):
        _seed_approved_trade(journal, "WIN", 40.0)
    assert journal.get_daily_state(TODAY).trade_count == config.max_trades_per_day
    before = len(_rows(log_dir))

    result = process_alert(_trade_payload(30), config=config, log_dir=log_dir, for_date=TODAY)

    # Execution layer: blocked, explicit reason, nothing sent.
    assert result["decision"] == "BLOCKED_MAX_TRADES"
    assert result["reason"]
    assert result.get("fill") is None
    assert result["execution_block"]["code"] == "BLOCKED_MAX_TRADES"
    assert result["execution_block"]["trade_count"] == config.max_trades_per_day
    assert result["execution_block"]["limit"] == config.max_trades_per_day

    # Observation layer: the setup the engine found is recorded, not erased.
    assert result["observed_decision"] == "TRADE"
    assert result["candidate"] is not None
    assert result["candidate"]["no_trade_taken"] is True
    assert result["candidate"]["reject_code"] == "BLOCKED_MAX_TRADES"
    assert result["candidate"]["strategy"] and result["candidate"]["entry"]
    assert "BLOCKED_MAX_TRADES" in result["failed_gates"]

    rows = _rows(log_dir)
    new_rows = rows[before:]
    assert len(new_rows) == 1, new_rows  # exactly one journal row, no duplicates
    row = new_rows[0]
    assert row["decision"] == "BLOCKED_MAX_TRADES"
    assert row["observed_decision"] == "TRADE"
    assert row["setup"] is not None and row["setup"]["strategy"]
    assert row["execution_block"]["code"] == "BLOCKED_MAX_TRADES"
    assert row["reason"]
    # Never a counted or intent row.
    assert all(r.get("decision") not in {"TRADE_INTENT"} for r in new_rows)
    assert sum(1 for r in rows if r.get("decision") == "TRADE") == config.max_trades_per_day
    assert journal.get_daily_state(TODAY).trade_count == config.max_trades_per_day
    assert journal.get_daily_state(TODAY).has_open_position is False

    # Candidate audit parity: whatever the unblocked control run persisted to
    # the opportunity store, the blocked run persists too (same writer, same
    # bar), and any lifecycle it records carries the block as the terminal
    # stage — never a RISK_CHECK or an order stage.
    control_opps = _opportunity_rows(str(tmp_path / "control"))
    opps = _opportunity_rows(log_dir)
    control_candidates = [o for o in control_opps if o.get("candidate_id") and not o.get("stage")]
    candidates = [o for o in opps if o.get("candidate_id") and not o.get("stage")]
    assert len(candidates) == len(control_candidates)
    stages = [o for o in opps if o.get("stage")]
    assert all(o.get("stage") == "DECISION_BLOCKED" for o in stages), stages
    assert all(o.get("decision") == "BLOCKED_MAX_TRADES" for o in stages)
    assert len({(o.get("candidate_id"), o.get("stage")) for o in stages}) == len(stages)


def test_max_trades_reached_no_setup_is_distinguishable(config, tmp_path):
    """A budget-exhausted bar with NO setup must not look like a blocked setup."""
    log_dir = str(tmp_path / "logs")
    journal = JournalLogger(log_dir=log_dir)
    for _ in range(config.max_trades_per_day):
        _seed_approved_trade(journal, "WIN", 40.0)

    # DEAD market condition never yields a setup.
    payload = _base_payload(
        timestamp=datetime(2026, 5, 23, 14, 30, tzinfo=timezone.utc).isoformat(),
        market_condition="DEAD",
    )
    result = process_alert(payload, config=config, log_dir=log_dir, for_date=TODAY)

    assert result["decision"] == "BLOCKED_MAX_TRADES"
    assert result["observed_decision"] == "NO_TRADE"
    assert result.get("candidate") is None
    assert result.get("fill") is None
    row = _rows(log_dir)[-1]
    assert row["decision"] == "BLOCKED_MAX_TRADES"
    assert row["observed_decision"] == "NO_TRADE"
    assert row.get("setup") is None
    assert row["observed_reason"]


def test_max_trades_second_bar_does_not_duplicate_or_execute(config, tmp_path):
    log_dir = str(tmp_path / "logs")
    journal = JournalLogger(log_dir=log_dir)
    for _ in range(config.max_trades_per_day):
        _seed_approved_trade(journal, "WIN", 40.0)
    r1 = process_alert(_trade_payload(30), config=config, log_dir=log_dir, for_date=TODAY)
    r2 = process_alert(_trade_payload(45), config=config, log_dir=log_dir, for_date=TODAY)
    assert r1["decision"] == r2["decision"] == "BLOCKED_MAX_TRADES"
    assert r1.get("fill") is None and r2.get("fill") is None
    rows = _rows(log_dir)
    assert [r["decision"] for r in rows if r.get("decision") in {"TRADE", "TRADE_INTENT"}] == ["TRADE"] * config.max_trades_per_day
    blocked_rows = [r for r in rows if r.get("decision") == "BLOCKED_MAX_TRADES"]
    assert len(blocked_rows) == 2
    assert len({r["ts"] for r in blocked_rows}) == 2


# ─── consecutive-loss lockout (same Step-2 architecture) ─────────────────────

def test_loss_lockout_setup_still_observed_but_not_executed(config, tmp_path):
    log_dir = str(tmp_path / "logs")
    journal = JournalLogger(log_dir=log_dir)
    assert config.max_consecutive_losses < config.max_trades_per_day
    for _ in range(config.max_consecutive_losses):
        _seed_approved_trade(journal, "LOSS", -80.0)
    state = journal.get_daily_state(TODAY)
    assert state.consecutive_losses == config.max_consecutive_losses
    assert state.trade_count < config.max_trades_per_day  # lockout, not capacity

    result = process_alert(_trade_payload(30), config=config, log_dir=log_dir, for_date=TODAY)

    assert result["decision"] == "BLOCKED_LOSS_LOCKOUT"
    assert result.get("fill") is None
    assert result["observed_decision"] == "TRADE"
    assert result["candidate"]["reject_code"] == "BLOCKED_LOSS_LOCKOUT"
    assert result["execution_block"]["consecutive_losses"] == config.max_consecutive_losses
    row = _rows(log_dir)[-1]
    assert row["decision"] == "BLOCKED_LOSS_LOCKOUT"
    assert row["setup"] is not None
    assert row["observed_decision"] == "TRADE"
    assert journal.get_daily_state(TODAY).trade_count == config.max_consecutive_losses


# ─── daily-loss cap (RiskEngine path — already observes; pinned) ─────────────

def test_daily_loss_reached_setup_observed_and_execution_rejected(config, tmp_path):
    cfg = dataclasses.replace(config, max_daily_loss=150.0, max_consecutive_losses=9999)
    log_dir = str(tmp_path / "logs")
    journal = JournalLogger(log_dir=log_dir)
    # The cap scales by the contracts of the NEXT setup ($150 × contracts); the
    # fixture caps MNQ at 2 contracts, so a -$300 day is at the limit for any size.
    max_contracts = cfg.max_contracts_per_instrument["MNQ"]
    _seed_approved_trade(journal, "LOSS", -150.0 * max_contracts)
    assert journal.get_daily_state(TODAY).realized_pnl_dollars <= -150.0 * max_contracts
    assert journal.get_daily_state(TODAY).trade_count == 1 < cfg.max_trades_per_day

    result = process_alert(_trade_payload(30), config=cfg, log_dir=log_dir, for_date=TODAY)

    assert result["decision"] == "RISK_REJECTED"
    assert result["risk"]["failed_rule"] == "max_daily_loss"
    assert result["risk"]["reason"]
    assert result.get("fill") is None
    assert result["candidate"]["reject_code"] == "max_daily_loss"
    row = _rows(log_dir)[-1]
    assert row["decision"] == "RISK_REJECTED"
    assert row["setup"] is not None
    assert row["risk_check"]["failed_rule"] == "max_daily_loss"
    assert journal.get_daily_state(TODAY).trade_count == 1


# ─── limits themselves are untouched; second layer still enforces ────────────

def test_risk_engine_still_rejects_at_capacity_and_lockout_and_loss(config):
    """If the runner-level block were ever removed, the RiskEngine layer still
    refuses execution.  Also pins that no numerical limit moved."""
    from risk.risk_engine import RiskEngine, TradeSetup, DailyState

    assert config.max_trades_per_day == 3
    assert config.max_consecutive_losses == 2
    engine = RiskEngine(config=config)
    setup = TradeSetup(
        direction="LONG", entry=19500.0, stop=19460.0, target=19580.0, rr_ratio=2.0,
        strategy="orb_reclaim", instrument="MNQ", session="new_york", notes=None,
        entry_time=datetime(2026, 5, 23, 14, 30, tzinfo=timezone.utc), contracts=1,
        confluence_grade="A",
    )
    base = dict(account_balance=1500.0, account_peak_balance=1500.0)
    at_cap = DailyState(trade_count=config.max_trades_per_day, **base)
    assert engine.validate(setup, at_cap).failed_rule == "daily_trade_limit"
    locked = DailyState(trade_count=1, consecutive_losses=config.max_consecutive_losses, **base)
    assert engine.validate(setup, locked).failed_rule == "consecutive_loss_limit"
    loss_cfg = dataclasses.replace(config, max_daily_loss=150.0)
    lost = DailyState(trade_count=1, realized_pnl_dollars=-150.0, **base)
    assert RiskEngine(config=loss_cfg).validate(setup, lost).failed_rule == "max_daily_loss"


def test_production_limits_and_cap_unchanged():
    import yaml

    rules = yaml.safe_load(Path("risk_rules.yaml").read_text())
    assert rules["daily_limits"]["max_trades_per_day"] == 3
    assert rules["daily_limits"]["max_daily_loss"] == 150
    assert rules["position_rules"]["averaging_down"] is False
    assert rules["order_rules"]["require_stop"] is True
    assert rules["trading_mode"]["live_trading_enabled"] is False


# ─── lane boundaries: the blocked bar touches nothing but the main journal ────

def test_blocked_bar_writes_no_isolated_lane_ledger(config, tmp_path):
    """The reposition returns before every isolated lane (wide-stop ledger,
    MES 1-2-2, MNQ Strat evidence, MES trend-consolidation-break, entry-refresh
    campaign arm, proof lanes) — exactly the set that was unreachable before.
    Prove it by the files a budget-exhausted bar creates."""
    log_dir = tmp_path / "logs"
    journal = JournalLogger(log_dir=str(log_dir))
    for _ in range(config.max_trades_per_day):
        _seed_approved_trade(journal, "WIN", 40.0)
    before = {p.relative_to(log_dir) for p in log_dir.rglob("*") if p.is_file()}

    result = process_alert(_trade_payload(30), config=config, log_dir=str(log_dir), for_date=TODAY)
    assert result["decision"] == "BLOCKED_MAX_TRADES"

    after = {p.relative_to(log_dir) for p in log_dir.rglob("*") if p.is_file()}
    created = {str(p) for p in after - before}
    allowed_prefixes = (
        f"journal_{TODAY.isoformat()}.jsonl",   # main journal (already existed)
        "opportunities/",                        # candidate audit store
        "strategy_context_observations",         # observe-only context feed
        "bars_",                                 # bar history cache
        "latest_webhook",                        # last-payload snapshot
        "contract_identity_observe",             # observe-only contract identity row (#969), written at bar ingest
    )
    unexpected = {p for p in created if not p.startswith(allowed_prefixes)}
    assert not unexpected, f"blocked bar must not touch isolated lane files: {sorted(unexpected)}"
    for forbidden in ("wide_stop", "mes_122", "mnq_strat", "mes_trend", "forward_ab", "shadow", "proof", "inverse"):
        assert not any(forbidden in p for p in created), (forbidden, sorted(created))
