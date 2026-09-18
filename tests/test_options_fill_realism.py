"""Tests for offline options executable-fill event realism."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from options_manager.fill_realism import (
    UnderlyingBar,
    first_executable_retained_quote,
    resolve_exit_trigger,
)
from options_manager.quotes.replay import contract_market_snapshot_from_retained_quote
from options_manager.quotes.retention import (
    QuoteRetentionInput,
    quote_record_json,
    retain_quote,
    retention_rule_from_mapping,
)

RULE_PATH = Path("options_manager/quotes/quote_retention_rule_v1.json")


def _quote(**overrides) -> bytes:
    raw = json.loads(RULE_PATH.read_text())
    rule = retention_rule_from_mapping(raw)
    rule_sha = hashlib.sha256(RULE_PATH.read_bytes()).hexdigest()
    values = dict(
        contract_id="SPY261120C00550000",
        underlying="SPY",
        expiration="2026-11-20",
        strike=550.0,
        right="CALL",
        bid=4.8,
        ask=5.0,
        quote_ts="2026-09-18T14:00:00+00:00",
        decision_ts="2026-09-18T14:01:00+00:00",
        source="fixture:option_chain_snapshot",
        volume=500,
        open_interest=1500,
        delta=0.5,
        iv=0.25,
    )
    values.update(overrides)
    record = retain_quote(
        QuoteRetentionInput(**values),
        rule=rule,
        rule_sha256=rule_sha,
    )
    return quote_record_json(record).encode("utf-8")


def test_call_same_bar_target_and_stop_resolves_stop_first():
    result = resolve_exit_trigger(
        direction="CALL",
        bar=UnderlyingBar(open=100.0, high=111.0, low=94.0),
        stop_level=95.0,
        target_level=110.0,
    )
    assert result.reason == "STOP"
    assert result.same_bar_both_hit is True
    assert result.detail == "same_bar_both_hit_stop_first"


def test_put_same_bar_target_and_stop_resolves_stop_first():
    result = resolve_exit_trigger(
        direction="PUT",
        bar=UnderlyingBar(open=100.0, high=106.0, low=89.0),
        stop_level=105.0,
        target_level=90.0,
    )
    assert result.reason == "STOP"
    assert result.same_bar_both_hit is True


def test_call_gap_through_stop_is_classified_before_intrabar_target():
    result = resolve_exit_trigger(
        direction="CALL",
        bar=UnderlyingBar(open=94.0, high=112.0, low=93.0),
        stop_level=95.0,
        target_level=110.0,
    )
    assert result.reason == "STOP_GAP"
    assert result.detail == "open_through_stop"


def test_put_gap_through_stop_is_classified_before_intrabar_target():
    result = resolve_exit_trigger(
        direction="PUT",
        bar=UnderlyingBar(open=106.0, high=107.0, low=88.0),
        stop_level=105.0,
        target_level=90.0,
    )
    assert result.reason == "STOP_GAP"


def test_target_only_bar_resolves_target():
    result = resolve_exit_trigger(
        direction="CALL",
        bar=UnderlyingBar(open=100.0, high=111.0, low=99.0),
        stop_level=95.0,
        target_level=110.0,
    )
    assert result.reason == "TARGET"
    assert result.same_bar_both_hit is False


def test_nonfinite_bar_or_level_fails_closed():
    result = resolve_exit_trigger(
        direction="CALL",
        bar=UnderlyingBar(open=100.0, high=float("nan"), low=99.0),
        stop_level=95.0,
        target_level=110.0,
    )
    assert result.reason == "INVALID"
    assert result.triggered is False


def test_first_executable_quote_skips_non_ok_rows_without_reconstruction():
    missing = _quote(quote_ts=None)
    stale = _quote(quote_ts="2026-09-18T13:40:00+00:00")
    wide = _quote(bid=1.0, ask=2.0)
    ok = _quote(bid=4.7, ask=4.9)

    choice = first_executable_retained_quote([missing, stale, wide, ok])

    assert choice.status == "FOUND"
    assert choice.reason_code == "first_executable_quote"
    assert choice.index == 3
    assert choice.payload == ok

    snapshot = contract_market_snapshot_from_retained_quote(choice.payload)
    assert snapshot.bid == 4.7
    assert snapshot.ask == 4.9


def test_no_executable_quote_is_explicit_no_fill():
    choice = first_executable_retained_quote(
        [
            _quote(quote_ts=None),
            _quote(quote_ts="2026-09-18T13:40:00+00:00"),
            _quote(bid=1.0, ask=2.0),
        ]
    )
    assert choice.status == "NO_FILL"
    assert choice.reason_code == "no_executable_quote_after_trigger"
    assert choice.payload is None


def test_no_quote_after_trigger_is_explicit_no_fill():
    choice = first_executable_retained_quote([])
    assert choice.status == "NO_FILL"
    assert choice.reason_code == "no_quote_after_trigger"


def test_malformed_retained_quote_bytes_fail_closed_not_skipped():
    choice = first_executable_retained_quote([b"{not-json}\n", _quote()])
    assert choice.status == "INVALID_DATA"
    assert choice.reason_code == "retained_quote_parse_failed"
    assert choice.index == 0
    assert choice.payload is None



def test_bar_open_outside_range_fails_closed():
    result = resolve_exit_trigger(
        direction="CALL",
        bar=UnderlyingBar(open=112.0, high=111.0, low=99.0),
        stop_level=95.0,
        target_level=110.0,
    )
    assert result.reason == "INVALID"
    assert result.detail == "bar_open_outside_range"


def test_invalid_directional_stop_target_geometry_fails_closed():
    call = resolve_exit_trigger(
        direction="CALL",
        bar=UnderlyingBar(open=100.0, high=101.0, low=99.0),
        stop_level=110.0,
        target_level=95.0,
    )
    put = resolve_exit_trigger(
        direction="PUT",
        bar=UnderlyingBar(open=100.0, high=101.0, low=99.0),
        stop_level=90.0,
        target_level=105.0,
    )
    assert call.reason == "INVALID"
    assert call.detail == "invalid_call_stop_target_geometry"
    assert put.reason == "INVALID"
    assert put.detail == "invalid_put_stop_target_geometry"
