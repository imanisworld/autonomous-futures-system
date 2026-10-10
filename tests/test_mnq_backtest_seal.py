"""MNQ June 29 sealed-holdout guard: synthetic metadata only, no bar fetch."""
from datetime import date

import pytest

from research.mnq_backtest_seal import (
    LAST_UNSEALED_MNQ_SESSION,
    MNQSealViolation,
    validate_mnq_backtest_rows,
    validate_mnq_backtest_window,
)


@pytest.mark.parametrize("ticker", [
    "MNQ", "MNQM6", "MNQM2026", "CME_MINI:MNQU2026", "MNQ1!"
])
def test_last_unsealed_session_is_allowed(ticker):
    assert LAST_UNSEALED_MNQ_SESSION == date(2026, 6, 26)
    validate_mnq_backtest_window(
        ticker=ticker, start_session="2025-03-17",
        end_session="2026-06-26",
    )


@pytest.mark.parametrize("end", [
    "2026-06-27", "2026-06-28", "2026-06-29",
    "2026-07-01", "2026-09-11", "2026-10-10",
])
def test_any_ambiguous_or_postseal_requested_end_is_refused(end):
    with pytest.raises(MNQSealViolation, match="sealed"):
        validate_mnq_backtest_window(
            ticker="MNQU6", start_session="2025-03-01", end_session=end
        )


def test_inverted_window_and_unknown_symbol_fail_closed():
    with pytest.raises(MNQSealViolation, match="inverted"):
        validate_mnq_backtest_window(
            ticker="MNQM6", start_session="2026-06-26",
            end_session="2026-06-25",
        )
    with pytest.raises(MNQSealViolation, match="explicitly"):
        validate_mnq_backtest_window(
            ticker="ESM6", start_session="2026-01-01",
            end_session="2026-03-01",
        )


@pytest.mark.parametrize("date_str", [
    "2026-06-28", "2026-06-29", "2026-09-11"
])
def test_one_protected_row_rejects_entire_dataset(date_str):
    rows = [
        {"ticker": "MNQM6", "session_end_date": "2026-06-26"},
        {"ticker": "MNQU6", "session_end_date": date_str},
    ]
    with pytest.raises(MNQSealViolation, match="CONTAMINATED_POST_SEAL"):
        validate_mnq_backtest_rows(rows)


def test_presealed_rows_pass_without_auto_trimming():
    rows = [
        {"ticker": "MNQH6", "session_end_date": "2026-03-13"},
        {"ticker": "CME_MINI:MNQM2026", "session_end_date": "2026-06-26"},
    ]
    assert validate_mnq_backtest_rows(rows) == 2
    assert len(rows) == 2


@pytest.mark.parametrize("bad_row", [
    {"ticker": "MNQZ6"},
    {"session_end_date": "2026-06-26"},
    {"ticker": "MNQM6", "session_end_date": "not-a-date"},
    {"ticker": "ESM6", "session_end_date": "2026-06-26"},
])
def test_missing_source_identity_or_session_fails_closed(bad_row):
    with pytest.raises(MNQSealViolation):
        validate_mnq_backtest_rows([bad_row])


def test_empty_dataset_does_not_count_as_valid():
    with pytest.raises(MNQSealViolation, match="empty"):
        validate_mnq_backtest_rows([])


def test_unknown_end_before_fetch_is_not_assumed_safe():
    with pytest.raises(MNQSealViolation):
        validate_mnq_backtest_window(
            ticker="MNQ", start_session="2025-01-01", end_session=None
        )
