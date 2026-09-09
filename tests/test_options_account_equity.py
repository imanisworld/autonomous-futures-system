from __future__ import annotations

from datetime import datetime, timezone

from alert_ranker.account_equity import (
    build_account_equity_report,
    load_trade_evidence,
    minimum_starting_cash_to_fund_all,
    simulate_account,
)
from alert_ranker.contract_marks import record_contract_mark
from alert_ranker.scorer import ScoreResult
from alert_ranker.storage import ScanStorage

UTC = timezone.utc


def _stamp(text: str) -> datetime:
    return datetime.fromisoformat(text).astimezone(UTC)


def _add_trade(
    storage: ScanStorage,
    *,
    ticker: str,
    timestamp: str,
    status: str = "OPEN",
    entry_ask: float = 2.0,
    entry_bid: float = 1.9,
    planned_risk: float = 50.0,
    outcome: dict | None = None,
    lane: str = "ACTIVE",
) -> int:
    result = ScoreResult(
        ticker=ticker,
        direction="LONG",
        score=8,
        pattern="strat_212",
        raw={},
    )
    when = _stamp(timestamp)
    scan_id = storage.record_scan(
        result,
        source="test",
        alert_sent=False,
        alert_suppression_reason="test",
        timestamp=when,
    )
    return storage.record_shadow_setup(
        result,
        scan_id=scan_id,
        setup_inputs={},
        provider_snapshot={},
        selected_contract={
            "paper_policy_id": "OPTIONS_PAPER_V1",
            "paper_evidence_lane": lane,
            "risk_budget_consumed": lane != "COUNTERFACTUAL",
            "contract": f"{ticker}-TEST",
            "option_mark": entry_ask,
            "option_ask": entry_ask,
            "option_bid": entry_bid,
            "contracts": 1,
            "planned_risk_dollars": planned_risk,
        },
        outcome=outcome or {},
        status=status,
        timestamp=when,
    )


def test_swing_replay_includes_unrealized_path_and_overnight_gap(tmp_path):
    storage = ScanStorage(tmp_path / "scanner.sqlite")
    shadow_id = _add_trade(
        storage,
        ticker="SPY",
        timestamp="2026-09-08T15:00:00+00:00",
        status="WIN",
        entry_ask=2.0,
        entry_bid=1.9,
        planned_risk=50.0,
        outcome={
            "resolved_at": "2026-09-09T15:00:00+00:00",
            "exit_mark": 2.6,
        },
    )
    for stamp, bid in (
        ("2026-09-08T15:00:00+00:00", 1.9),
        ("2026-09-08T19:55:00+00:00", 1.7),
        ("2026-09-09T13:35:00+00:00", 1.5),
        ("2026-09-09T15:00:00+00:00", 2.6),
    ):
        record_contract_mark(
            storage,
            shadow_id=shadow_id,
            option_symbol="SPY-TEST",
            timestamp=_stamp(stamp),
            bid=bid,
            ask=bid + 0.1,
        )

    trades, issues = load_trade_evidence(storage)
    assert issues == {}
    result = simulate_account(
        trades,
        starting_balance=1500.0,
        as_of=_stamp("2026-09-09T15:00:00+00:00"),
    )

    assert result.ending_equity == 1560.0
    assert result.realized_pnl == 60.0
    assert result.unrealized_pnl == 0.0
    assert result.max_drawdown_dollars == 50.0
    assert result.overnight_holds == 1
    assert result.overnight_position_nights == 1
    assert result.max_overnight_positions == 1
    assert result.max_overnight_gap_loss_dollars == 20.0


def test_parallel_bankrolls_show_when_cash_blocks_an_otherwise_valid_trade(tmp_path):
    storage = ScanStorage(tmp_path / "scanner.sqlite")
    _add_trade(
        storage,
        ticker="AAPL",
        timestamp="2026-09-08T14:00:00+00:00",
        status="WIN",
        entry_ask=10.0,
        entry_bid=9.9,
        planned_risk=250.0,
        outcome={"resolved_at": "2026-09-08T16:00:00+00:00", "exit_mark": 10.0},
    )
    _add_trade(
        storage,
        ticker="MSFT",
        timestamp="2026-09-08T14:30:00+00:00",
        status="WIN",
        entry_ask=10.0,
        entry_bid=9.9,
        planned_risk=250.0,
        outcome={"resolved_at": "2026-09-08T16:30:00+00:00", "exit_mark": 10.0},
    )

    trades, issues = load_trade_evidence(storage)
    assert issues == {}
    assert minimum_starting_cash_to_fund_all(trades) == 2000.0

    small = simulate_account(trades, starting_balance=1500.0)
    larger = simulate_account(trades, starting_balance=2500.0)
    assert small.trades_funded == 1
    assert small.trades_blocked_capital == 1
    assert larger.trades_funded == 2
    assert larger.trades_blocked_capital == 0
    assert larger.max_capital_deployed == 2000.0
    assert larger.max_planned_risk_open == 500.0


def test_open_swing_marks_reduce_current_equity_before_trade_is_closed(tmp_path):
    storage = ScanStorage(tmp_path / "scanner.sqlite")
    shadow_id = _add_trade(
        storage,
        ticker="NVDA",
        timestamp="2026-09-08T15:00:00+00:00",
        entry_ask=2.0,
        entry_bid=1.9,
        planned_risk=50.0,
    )
    record_contract_mark(
        storage,
        shadow_id=shadow_id,
        option_symbol="NVDA-TEST",
        timestamp=_stamp("2026-09-08T15:00:00+00:00"),
        bid=1.9,
        ask=2.0,
    )
    record_contract_mark(
        storage,
        shadow_id=shadow_id,
        option_symbol="NVDA-TEST",
        timestamp=_stamp("2026-09-09T14:00:00+00:00"),
        bid=1.6,
        ask=1.7,
    )

    trades, _ = load_trade_evidence(storage)
    result = simulate_account(
        trades,
        starting_balance=1500.0,
        as_of=_stamp("2026-09-09T14:00:00+00:00"),
    )
    assert result.ending_equity == 1460.0
    assert result.unrealized_pnl == -40.0
    assert result.realized_pnl == 0.0
    assert result.open_positions_at_end == 1
    assert result.overnight_holds == 1


def test_report_excludes_counterfactuals_and_runs_1500_2500_5000_ladder(tmp_path):
    storage = ScanStorage(tmp_path / "scanner.sqlite")
    _add_trade(
        storage,
        ticker="SPY",
        timestamp="2026-09-08T14:00:00+00:00",
        status="WIN",
        outcome={"resolved_at": "2026-09-08T15:00:00+00:00", "exit_mark": 2.1},
    )
    _add_trade(
        storage,
        ticker="QQQ",
        timestamp="2026-09-08T14:05:00+00:00",
        status="LOSS",
        outcome={"resolved_at": "2026-09-08T15:00:00+00:00", "exit_mark": 1.5},
        lane="COUNTERFACTUAL",
    )

    report = build_account_equity_report(
        storage,
        as_of=_stamp("2026-09-08T15:00:00+00:00"),
    )
    assert report["active_v1_trades_loaded"] == 1
    assert report["capital_ceiling"] == 5000.0
    assert report["capital_ceiling_is_allocation_not_drawdown"] is True
    assert [scenario["starting_balance"] for scenario in report["scenarios"]] == [
        1500.0,
        2500.0,
        5000.0,
    ]
    assert report["methodology"]["counterfactual_rows"] == "EXCLUDED"
    assert report["methodology"]["unrealized_pnl_in_equity"] is True
