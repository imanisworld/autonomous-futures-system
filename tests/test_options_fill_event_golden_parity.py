"""Golden parity for exit event -> retained quote -> executable fill.

All inputs are frozen fixture bytes/values. Replay and forward independently
run the same pure production primitives. This proves mechanics only; the
nonzero fee/slippage case is a formula fixture, not an approved runtime policy.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path

import pytest

from options_manager.config import OptionsManagerConfig
from options_manager.contract_quality import ContractQualityResult
from options_manager.fill_realism import (
    UnderlyingBar,
    first_executable_retained_quote,
    resolve_exit_trigger,
)
from options_manager.models import OptionTradePacket
from options_manager.paper_sim import simulate_round_trip
from options_manager.quotes.replay import contract_market_snapshot_from_retained_quote
from options_manager.quotes.retention import (
    QuoteRetentionInput,
    quote_record_json,
    retain_quote,
    retention_rule_from_mapping,
)
from options_manager.risk_gate import RiskGateResult

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
        decision_ts="2026-09-18T14:00:05+00:00",
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


def _packet() -> OptionTradePacket:
    return OptionTradePacket(
        ticker="SPY",
        direction="CALL",
        entry_price=550.0,
        price_target=560.0,
        signa_score=80,

        signa_grade="A",
        signa_bias="BULLISH",
        gex_regime="NEUTRAL",
        gex_wall_above=None,
        gex_wall_below=None,
        contract_strike=550.0,
        contract_expiry=date(2026, 11, 20),
        max_premium=10.0,
        max_contracts=1,
        account_tag="golden_parity",
        source="frozen_fill_event_fixture",
        created_at=datetime(2026, 9, 18, 14, 0, tzinfo=timezone.utc),
        status="PENDING",
        rejection_reason=None,
    )


def _fill(
    entry_payload: bytes,
    exit_payload: bytes,
    *,
    slippage_percent: float = 0.0,
    per_contract_fee: float = 0.0,
):
    risk = RiskGateResult(
        approved=True,
        status="APPROVED",
        failed_rule=None,
        reason="",
        warnings=[],
    )
    quality = ContractQualityResult(
        approved=True,
        status="APPROVED",
        failed_rule=None,
        reason="",
        warnings=[],
    )

    cfg = replace(
        OptionsManagerConfig(),
        paper_sim_require_approved_risk=False,
        paper_sim_require_approved_quality=False,
        paper_sim_entry_fill="ASK",
        paper_sim_exit_fill="BID",
        paper_sim_slippage_percent=slippage_percent,
        paper_sim_per_contract_fee=per_contract_fee,
    )
    return simulate_round_trip(
        _packet(),
        contract_market_snapshot_from_retained_quote(entry_payload),
        contract_market_snapshot_from_retained_quote(exit_payload),
        risk,
        quality,
        cfg,
    )


def _run_event(
    *,
    bar: UnderlyingBar,
    stop_level: float,
    target_level: float,
    trigger_ts: str,
    exit_payloads: list[bytes],
    slippage_percent: float = 0.0,
    per_contract_fee: float = 0.0,
):
    trigger = resolve_exit_trigger(
        direction="CALL",
        bar=bar,
        stop_level=stop_level,
        target_level=target_level,
    )
    if not trigger.triggered:
        return trigger, None, None

    choice = first_executable_retained_quote(
        exit_payloads,
        trigger_ts=trigger_ts,
    )
    if choice.status != "FOUND":
        return trigger, choice, None


    assert choice.payload is not None
    result = _fill(
        _quote(),
        choice.payload,
        slippage_percent=slippage_percent,
        per_contract_fee=per_contract_fee,
    )
    return trigger, choice, result


def _assert_parity(**kwargs):
    replay = _run_event(**kwargs)
    forward = _run_event(**kwargs)
    assert replay == forward
    return replay


def test_same_bar_target_and_stop_is_stop_first_through_fill_consumer():
    exit_quote = _quote(
        bid=4.4,
        ask=4.6,
        quote_ts="2026-09-18T14:01:01+00:00",
        decision_ts="2026-09-18T14:01:05+00:00",
    )
    trigger, choice, fill = _assert_parity(
        bar=UnderlyingBar(open=100.0, high=111.0, low=94.0),
        stop_level=95.0,
        target_level=110.0,
        trigger_ts="2026-09-18T14:01:00+00:00",
        exit_payloads=[exit_quote],
    )

    assert trigger.reason == "STOP"
    assert trigger.same_bar_both_hit is True
    assert choice.status == "FOUND"
    assert fill.status == "SIMULATED"
    assert fill.simulated_exit_price == 4.4


def test_gap_stop_uses_earliest_posttrigger_executable_quote():
    pretrigger = _quote(
        bid=4.9,
        ask=5.1,
        quote_ts="2026-09-18T14:00:59+00:00",
        decision_ts="2026-09-18T14:01:00+00:00",
    )
    later = _quote(
        bid=4.0,
        ask=4.2,
        quote_ts="2026-09-18T14:01:20+00:00",
        decision_ts="2026-09-18T14:01:25+00:00",
    )
    earliest = _quote(
        bid=4.2,
        ask=4.4,
        quote_ts="2026-09-18T14:01:05+00:00",
        decision_ts="2026-09-18T14:01:10+00:00",
    )
    trigger, choice, fill = _assert_parity(
        bar=UnderlyingBar(open=94.0, high=112.0, low=93.0),
        stop_level=95.0,
        target_level=110.0,
        trigger_ts="2026-09-18T14:01:00+00:00",
        exit_payloads=[pretrigger, later, earliest],
    )

    assert trigger.reason == "STOP_GAP"
    assert choice.index == 2
    assert fill.status == "SIMULATED"
    assert fill.simulated_exit_price == 4.2


def test_no_posttrigger_executable_quote_is_no_fill_not_synthetic_fill():
    stale = _quote(
        quote_ts="2026-09-18T13:40:00+00:00",
        decision_ts="2026-09-18T14:01:05+00:00",
    )

    wide = _quote(
        bid=1.0,
        ask=2.0,
        quote_ts="2026-09-18T14:01:01+00:00",
        decision_ts="2026-09-18T14:01:05+00:00",
    )
    trigger, choice, fill = _assert_parity(
        bar=UnderlyingBar(open=100.0, high=101.0, low=94.0),
        stop_level=95.0,
        target_level=110.0,
        trigger_ts="2026-09-18T14:01:00+00:00",
        exit_payloads=[stale, wide],
    )

    assert trigger.reason == "STOP"
    assert choice.status == "NO_FILL"
    assert choice.reason_code == "no_executable_quote_after_trigger"
    assert fill is None


def test_entry_exit_slippage_fee_formula_has_golden_parity_without_policy_claim():
    exit_quote = _quote(
        bid=5.8,
        ask=6.0,
        quote_ts="2026-09-18T14:01:01+00:00",
        decision_ts="2026-09-18T14:01:05+00:00",
    )
    _, _, fill = _assert_parity(
        bar=UnderlyingBar(open=100.0, high=111.0, low=99.0),
        stop_level=95.0,
        target_level=110.0,
        trigger_ts="2026-09-18T14:01:00+00:00",
        exit_payloads=[exit_quote],
        slippage_percent=0.5,
        per_contract_fee=0.65,
    )

    assert fill.status == "SIMULATED"
    assert fill.simulated_entry_price == pytest.approx(5.025)

    assert fill.simulated_exit_price == pytest.approx(5.771)
    assert fill.simulated_gross_pnl == pytest.approx(74.6)
    assert fill.simulated_fees == pytest.approx(1.30)
    assert fill.simulated_net_pnl == pytest.approx(73.30)


def test_malformed_retained_exit_data_fails_closed_identically():
    trigger, choice, fill = _assert_parity(
        bar=UnderlyingBar(open=100.0, high=101.0, low=94.0),
        stop_level=95.0,
        target_level=110.0,
        trigger_ts="2026-09-18T14:01:00+00:00",
        exit_payloads=[b"{not-json}\n"],
    )
    assert trigger.reason == "STOP"
    assert choice.status == "INVALID_DATA"
    assert choice.reason_code == "retained_quote_parse_failed"
    assert fill is None
