"""Unit tests for the read-only Public option timestamp probe."""

from __future__ import annotations

from datetime import date, datetime, timezone

from alert_ranker.market_data import (
    PUBLIC_OPTION_CHAIN_SOURCE,
    OptionChain,
    OptionContractQuote,
)
from scripts.options_public_quote_timestamp_probe import (
    choose_probe_expiration,
    summarize_chain_timestamps,
)


def _contract(**overrides) -> OptionContractQuote:
    values = dict(
        symbol="SPY261120C00550000",
        option_type="CALL",
        strike=550.0,
        bid=4.8,
        ask=5.0,
        mid=4.9,
        last=4.9,
        volume=500,
        open_interest=1500,
        delta=0.5,
        implied_volatility=0.25,
        quote_timestamp="2026-09-18T14:00:00+00:00",
        bid_timestamp="2026-09-18T14:00:00+00:00",
        ask_timestamp="2026-09-18T14:00:01+00:00",
        source=PUBLIC_OPTION_CHAIN_SOURCE,
    )
    values.update(overrides)
    return OptionContractQuote(**values)


def test_probe_expiration_prefers_nearest_45_plus():
    result = choose_probe_expiration(
        ["2026-10-02", "2026-11-06", "2026-12-18"],
        today=date(2026, 9, 18),
        min_dte=14,
        preferred_min_dte=45,
    )
    assert result == "2026-11-06"


def test_probe_expiration_falls_back_to_nearest_min_dte():
    result = choose_probe_expiration(
        ["2026-09-25", "2026-10-02", "2026-10-30"],
        today=date(2026, 9, 18),
        min_dte=14,
        preferred_min_dte=45,
    )
    assert result == "2026-10-02"


def test_summary_proves_fresh_executable_timestamp_coverage_for_capture():
    chain = OptionChain(
        underlying="SPY",
        expiration="2026-11-20",
        calls=(_contract(),),
        puts=(),
        error=None,
    )
    summary = summarize_chain_timestamps(
        chain,
        received_at=datetime(2026, 9, 18, 14, 1, tzinfo=timezone.utc),
        max_quote_age_seconds=900,
    )
    assert summary.quoted_contracts == 1
    assert summary.executable_timestamp_count == 1
    assert summary.missing_executable_timestamp_count == 0
    assert summary.fresh_executable_timestamp_count == 1
    assert summary.stale_executable_timestamp_count == 0
    assert summary.all_quoted_have_executable_timestamp is True
    assert summary.all_executable_timestamps_fresh is True
    assert summary.source_values == (PUBLIC_OPTION_CHAIN_SOURCE,)


def test_summary_blocks_when_executable_timestamp_is_missing():
    chain = OptionChain(
        underlying="SPY",
        expiration="2026-11-20",
        calls=(_contract(quote_timestamp=None, ask_timestamp=None),),
        puts=(),
        error=None,
    )
    summary = summarize_chain_timestamps(
        chain,
        received_at=datetime(2026, 9, 18, 14, 1, tzinfo=timezone.utc),
        max_quote_age_seconds=900,
    )
    assert summary.quoted_contracts == 1
    assert summary.executable_timestamp_count == 0
    assert summary.missing_executable_timestamp_count == 1
    assert summary.all_quoted_have_executable_timestamp is False


def test_summary_marks_stale_and_future_executable_timestamps():
    chain = OptionChain(
        underlying="SPY",
        expiration="2026-11-20",
        calls=(
            _contract(symbol="STALE", quote_timestamp="2026-09-18T13:40:00+00:00"),
            _contract(symbol="FUTURE", quote_timestamp="2026-09-18T14:02:00+00:00"),
        ),
        puts=(),
        error=None,
    )
    summary = summarize_chain_timestamps(
        chain,
        received_at=datetime(2026, 9, 18, 14, 1, tzinfo=timezone.utc),
        max_quote_age_seconds=900,
    )
    assert summary.executable_timestamp_count == 2
    assert summary.stale_executable_timestamp_count == 1
    assert summary.future_executable_timestamp_count == 1
    assert summary.all_executable_timestamps_fresh is False


def test_unquoted_contract_does_not_count_as_executable_quote_coverage():
    chain = OptionChain(
        underlying="SPY",
        expiration="2026-11-20",
        calls=(_contract(bid=None, ask=None, quote_timestamp=None),),
        puts=(),
        error=None,
    )
    summary = summarize_chain_timestamps(
        chain,
        received_at=datetime(2026, 9, 18, 14, 1, tzinfo=timezone.utc),
        max_quote_age_seconds=900,
    )
    assert summary.total_contracts == 1
    assert summary.quoted_contracts == 0
    assert summary.all_quoted_have_executable_timestamp is False
