"""classify_no_fill_reason: pure mapping, no execution behavior involved."""
from __future__ import annotations

from execution.no_fill_taxonomy import (
    ALL_REASONS,
    NO_FILL_BROKER_REJECTED,
    NO_FILL_LIMIT_TOO_PASSIVE,
    NO_FILL_PRICE_MOVED_AWAY,
    NO_FILL_SESSION_OR_RISK_CANCEL,
    NO_FILL_UNKNOWN,
    classify_no_fill_reason,
)


def test_entry_not_filled_dead_is_price_moved_away():
    assert classify_no_fill_reason("ENTRY_NOT_FILLED", entry_status="dead") == NO_FILL_PRICE_MOVED_AWAY


def test_entry_not_filled_working_is_limit_too_passive():
    assert classify_no_fill_reason("ENTRY_NOT_FILLED", entry_status="working") == NO_FILL_LIMIT_TOO_PASSIVE


def test_entry_not_filled_without_status_is_unknown():
    assert classify_no_fill_reason("ENTRY_NOT_FILLED") == NO_FILL_UNKNOWN


def test_broker_rejection_reasons_map_to_broker_rejected():
    for reason in ("TRADOVATE_REJECTED", "TRADOVATE_NO_ORDER_ID", "BROKER_NOT_READY", "TRADOVATE_AUTH_FAILED"):
        assert classify_no_fill_reason(reason) == NO_FILL_BROKER_REJECTED


def test_session_or_risk_cancel_reasons():
    for reason in ("LIVE_TRADING_NOT_ENABLED", "LIVE_PREFLIGHT_NOT_ARMED"):
        assert classify_no_fill_reason(reason) == NO_FILL_SESSION_OR_RISK_CANCEL


def test_none_or_empty_reason_is_unknown():
    assert classify_no_fill_reason(None) == NO_FILL_UNKNOWN
    assert classify_no_fill_reason("") == NO_FILL_UNKNOWN


def test_unrecognized_reason_defaults_to_unknown_not_a_guess():
    assert classify_no_fill_reason("SOMETHING_NEW_NOBODY_MAPPED_YET") == NO_FILL_UNKNOWN


def test_case_insensitive():
    assert classify_no_fill_reason("tradovate_rejected") == NO_FILL_BROKER_REJECTED


def test_all_reasons_tuple_is_exactly_the_eight_requested_buckets():
    assert len(ALL_REASONS) == 8
    assert NO_FILL_UNKNOWN in ALL_REASONS
