from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import sqlite3

from alert_ranker.non_strat_coverage import NonStratEvent, OBSERVER_VERSION
from options_manager.config import OptionsManagerConfig
from options_manager.contract_quality import ContractMarketSnapshot
from options_manager.non_strat_paper_journal import NonStratPaperJournal
from options_manager.non_strat_paper_track import (
    NonStratPaperPlan,
    prepare_non_strat_paper_candidate,
    simulate_non_strat_round_trip,
)


NOW = datetime.now(timezone.utc)


def _event():
    return NonStratEvent(
        observer_id="OPTIONS_NON_STRAT_COVERAGE",
        observer_version=OBSERVER_VERSION,
        symbol="AAPL",
        timeframe="5m",
        session_date=NOW.date().isoformat(),
        bar_start=(NOW - timedelta(minutes=25)).isoformat(),
        bar_close=(NOW - timedelta(minutes=20)).isoformat(),
        family="PDH_RECLAIM_LONG",
        direction="LONG",
        level_name="PDH",
        level_value=99.5,
        trigger_price=100.0,
        vwap=99.7,
        ema20=99.0,
        volume_ratio=1.4,
        spy_trend="bullish",
        qqq_trend="bullish",
        market_aligned=True,
        earliest_sip_visibility=(NOW - timedelta(minutes=4)).isoformat(),
        source_rule="test",
        episode_id="episode-journal-1",
    )


def _snapshot(*, bid=1.0, ask=1.1, contract="AAPL_TEST_CALL"):
    return ContractMarketSnapshot(
        ticker="AAPL",
        contract_symbol=contract,
        bid=bid,
        ask=ask,
        last=(bid + ask) / 2,
        volume=500,
        open_interest=2000,
        implied_volatility=0.5,
        delta=0.5,
        theta=-0.03,
        underlying_price=100.0,
        quote_timestamp=NOW - timedelta(minutes=2),
        provider="polygon",
        is_snapshot_complete=True,
    )


def _config():
    return OptionsManagerConfig(
        risk_max_premium=3.0,
        risk_max_contracts=2,
        risk_max_total_premium_dollars=300.0,
        risk_min_dte_days=14,
        quality_max_spread_percent=20.0,
        quality_min_option_volume=100,
        quality_min_open_interest=500,
        quality_max_quote_age_seconds=900,
        broker_boundary_enabled=True,
        broker_boundary_max_contracts=2,
        broker_boundary_max_notional=300.0,
        broker_boundary_max_limit_price=3.0,
        live_options_trading_enabled=False,
    )


def _prepared():
    return prepare_non_strat_paper_candidate(
        NonStratPaperPlan(
            event=_event(),
            underlying_invalidation=98.0,
            underlying_target=104.0,
            geometry_rule_id="PDH_RECLAIM_LONG:v1",
            source_references=("docs/options-non-strat-coverage-observer.md#frozen-ns-v01-raw-families",),
            contract_strike=100.0,
            contract_expiry=date.today() + timedelta(days=45),
            entry_snapshot=_snapshot(),
        ),
        _config(),
    )


def test_journal_records_preparation_once_without_mutating_it(tmp_path):
    journal = NonStratPaperJournal(tmp_path / "track.sqlite")
    prepared = _prepared()
    assert prepared.status == "INTERNAL_READY"

    first = journal.record_preparation(prepared)
    second = journal.record_preparation(prepared)
    assert first.status == "RECORDED"
    assert second.status == "DUPLICATE"

    conn = sqlite3.connect(tmp_path / "track.sqlite")
    row = conn.execute(
        """SELECT status, webull_submit_allowed, webull_block_reason,
                  contract_symbol, quantity, event_trigger_price,
                  decision_underlying_price, geometry_rule_id,
                  source_references_json
           FROM non_strat_paper_preparations"""
    ).fetchone()
    conn.close()
    assert row[0] == "INTERNAL_READY"
    assert row[1] == 0
    assert row[2] == "webull_round_trip_lifecycle_unproven"
    assert row[3] == "AAPL_TEST_CALL"
    assert row[4] == 1
    assert row[5] == 100.0
    assert row[6] == 100.0
    assert row[7] == "PDH_RECLAIM_LONG:v1"
    assert "options-non-strat-coverage-observer" in row[8]


def test_round_trip_requires_preparation_row_and_is_append_only(tmp_path):
    prepared = _prepared()
    result = simulate_non_strat_round_trip(
        prepared,
        _snapshot(bid=1.5, ask=1.6),
        _config(),
    )
    assert result.status == "SIMULATED"

    journal = NonStratPaperJournal(tmp_path / "track.sqlite")
    missing = journal.record_round_trip(result)
    assert missing.status == "REJECTED"
    assert missing.reason == "preparation_not_recorded"

    assert journal.record_preparation(prepared).status == "RECORDED"
    first = journal.record_round_trip(result)
    second = journal.record_round_trip(result)
    assert first.status == "RECORDED"
    assert second.status == "DUPLICATE"

    conn = sqlite3.connect(tmp_path / "track.sqlite")
    row = conn.execute(
        """SELECT simulated_entry_price, simulated_exit_price,
                  simulated_net_pnl
           FROM non_strat_paper_results"""
    ).fetchone()
    conn.close()
    assert row == (1.1, 1.5, 40.0)


def test_journal_source_has_no_update_delete_or_secret_fields():
    source = Path("options_manager/non_strat_paper_journal.py").read_text().upper()
    assert "UPDATE " not in source
    assert "DELETE " not in source
    for forbidden in (
        "WEBULL_SANDBOX_APP_KEY",
        "WEBULL_SANDBOX_APP_SECRET",
        "ACCOUNT_ID",
        "ACCESS_TOKEN",
        "REFRESH_TOKEN",
    ):
        assert forbidden not in source