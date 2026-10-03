"""OPTIONS_PAPER_SPXW_V1 policy: cohorts, risk, multiplier, fail-closed."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from alert_ranker import paper_spxw_v1 as spxw
from alert_ranker import paper_v1 as equity_v1

NY = ZoneInfo("America/New_York")
NOW = datetime(2026, 9, 29, 10, 0, tzinfo=NY)


def _quote(**overrides):
    base = dict(
        symbol="SPXW260929C05800000",
        option_type="CALL",
        strike=5800.0,
        bid=1.40,
        ask=1.50,
        mid=1.45,
        volume=1200,
        open_interest=5000,
        delta=0.40,
        gamma=0.01,
        theta=-0.05,
        implied_volatility=0.20,
        quote_timestamp="2026-09-29T13:59:30+00:00",
        source="test",
        stale=False,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_spxw_allows_0dte_and_tags_cohort():
    decision = spxw.choose_expiration(["2026-09-29", "2026-10-03"], NOW)
    assert decision.valid
    assert decision.expiry.dte == 0
    assert decision.expiry.cohort == spxw.COHORT_0DTE
    assert decision.expiry.bucket == "DTE_0"


def test_spxw_tags_1_plus_dte():
    decision = spxw.choose_expiration(["2026-10-03"], NOW)
    assert decision.valid
    assert decision.expiry.dte == 4
    assert decision.expiry.cohort == spxw.COHORT_1_PLUS
    assert decision.expiry.bucket == "DTE_1_PLUS"


def test_invalid_expiration_rejected():
    decision = spxw.choose_expiration(["2099-01-16", "not-a-date"], NOW)
    assert not decision.valid
    assert decision.status == "DATA_INVALID"


def test_stale_and_missing_quotes_fail_closed():
    stale = spxw.choose_contract(
        (_quote(stale=True),),
        option_type="CALL",
        underlying_price=5800.0,
    )
    assert not stale.valid
    assert "stale_quote" in stale.reason

    bad_ba = spxw.choose_contract(
        (_quote(bid=0.0, ask=1.5),),
        option_type="CALL",
        underlying_price=5800.0,
    )
    assert not bad_ba.valid
    assert "no_liquid_contract" in bad_ba.reason


def test_premium_risk_uses_contract_multiplier_100():
    expiry = spxw.choose_expiration(["2026-09-29"], NOW).expiry
    contract = spxw.choose_contract(
        (_quote(),), option_type="CALL", underlying_price=5800.0
    ).contract
    fields, reason = spxw.build_spxw_contract_fields(
        expiry=expiry,
        contract=contract,
        underlying_invalidation=5780.0,
        target_1=5850.0,
        aggregate_open_risk=0.0,
    )
    assert reason == ""
    assert fields["paper_policy_id"] == spxw.POLICY_ID
    assert fields["signal_underlying"] == "SPX"
    assert fields["contract_root"] == "SPXW"
    assert fields["contract_multiplier"] == 100
    assert fields["option_mark"] == 1.50
    # 25% adverse from ask 1.50 → stop 1.125; risk = (1.50-1.125)*100 = 37.5
    assert fields["premium_stop"] == 1.125
    assert fields["planned_risk_dollars"] == 37.5
    assert fields["spread_cost_dollars"] == 10.0  # (1.50-1.40)*100
    assert fields["dte_cohort"] == spxw.COHORT_0DTE
    assert fields["max_trade_planned_risk"] == 300.0
    assert fields["max_aggregate_open_planned_risk"] == 1000.0


def test_aggregate_risk_is_lane_local_cap():
    expiry = spxw.choose_expiration(["2026-09-29"], NOW).expiry
    contract = spxw.choose_contract(
        (_quote(),), option_type="CALL", underlying_price=5800.0
    ).contract
    fields, reason = spxw.build_spxw_contract_fields(
        expiry=expiry,
        contract=contract,
        underlying_invalidation=5780.0,
        target_1=5850.0,
        aggregate_open_risk=980.0,
    )
    assert fields is None
    assert reason.startswith("aggregate_risk_cap_exceeded:")


def test_entry_gates_require_triggered_setup():
    gate = spxw.evaluate_entry_gates(
        setup_status="WATCH",
        direction="LONG",
        price=5800.0,
        invalidation=5780.0,
        target_1=5850.0,
    )
    assert gate is not None
    assert gate["paper_policy_status"] == "DATA_INVALID"
    assert gate["paper_policy_reason"] == "setup_not_triggered"


def test_equity_v1_constants_unchanged():
    """SPXW lane must not loosen equity OPTIONS_PAPER_V1 DTE / risk rules."""
    assert equity_v1.MIN_DTE == 14
    assert equity_v1.MAX_TRADE_RISK_DOLLARS == 300.0
    assert equity_v1.MAX_AGGREGATE_OPEN_RISK_DOLLARS == 1000.0
    assert equity_v1.MAX_SPREAD_PERCENT == 10.0
    assert equity_v1.MIN_OPTION_VOLUME == 100
    assert equity_v1.MIN_OPEN_INTEREST == 500
    assert equity_v1.POLICY_ID == "OPTIONS_PAPER_V1"
    # Equity V1 still rejects <14 DTE
    equity_decision = equity_v1.choose_expiration(["2026-09-29", "2026-10-03"], NOW)
    assert not equity_decision.valid
