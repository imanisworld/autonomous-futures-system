from __future__ import annotations

import math

import pytest

from options_manager.risk.concentration import (
    ConcentrationStatus,
    CorrelationRiskBucket,
    ExposureFact,
    exposure_fact_from_risk_telemetry,
    measure_concentration,
)
from options_manager.risk.telemetry import RiskTelemetrySnapshot


def _fact(**overrides) -> ExposureFact:
    values = dict(
        ticker="AAPL",
        direction="CALL",
        planned_dollar_risk=100.0,
        full_debit=250.0,
        dte=45,
        expiration="2026-10-16",
        contracts=1,
        correlation_risk=(),
        sector="Technology",
        industry="Consumer Electronics",
        index_overlap=("SPY", "QQQ"),
        is_candidate=False,
    )
    values.update(overrides)
    return ExposureFact(**values)


def _risk_snapshot(**overrides) -> RiskTelemetrySnapshot:
    values = dict(
        ticker="AAPL",
        direction="CALL",
        setup_type="strat_212",
        timeframe="30m",
        observed_at="2026-09-02T10:03:00-04:00",
        plan_status="triggered",
        actionable=True,
        max_contracts=2,
        planned_stop_risk_per_contract=100.0,
        planned_total_trade_risk=200.0,
        full_debit_per_contract=250.0,
        full_debit_total=500.0,
        aggregate_planned_open_risk=300.0,
        projected_aggregate_planned_open_risk=500.0,
        aggregate_full_debit=700.0,
        projected_aggregate_full_debit=1200.0,
        open_position_count=3,
        correlation_risk=(("mega_cap_tech", 500.0),),
        stated_max_dollar_risk=300.0,
        max_trade_risk_dollars=300.0,
        max_aggregate_open_risk_dollars=1500.0,
        entry_trigger=230.0,
        underlying_invalidation=226.0,
        distance_to_invalidation=4.0,
        target_1=236.0,
        target_2=242.0,
        rr_1=1.5,
        rr_2=3.0,
        expiration="2026-10-16",
        dte=44,
        strike=230.0,
        premium=2.5,
        premium_stop=1.5,
        bid=2.4,
        ask=2.6,
        spread_percent=0.08,
        volume=1000,
        open_interest=5000,
        iv_event_risk="CLEAR",
        theta_risk="ACCEPTABLE",
        trade_style="swing",
    )
    values.update(overrides)
    return RiskTelemetrySnapshot(**values)


def _bucket(mapping, name):
    return next(bucket for bucket in mapping if bucket.name == name)


def test_empty_portfolio_is_complete_zero_measurement():
    result = measure_concentration([])
    assert result.status == ConcentrationStatus.COMPLETE
    assert result.snapshot is not None
    assert result.snapshot.total_planned_dollar_risk == 0
    assert result.snapshot.total_full_debit == 0
    assert result.snapshot.position_count == 0
    assert result.snapshot.contract_count == 0
    assert result.snapshot.by_correlation_group == ()
    assert result.snapshot.correlation_risk_reported is False


def test_bridge_copies_reconciled_risk_facts_without_recomputing_them():
    risk = _risk_snapshot(
        planned_total_trade_risk=217.35,
        full_debit_total=511.25,
        max_contracts=3,
        dte=37,
        expiration="2026-10-09",
    )
    fact = exposure_fact_from_risk_telemetry(
        risk,
        sector="Technology",
        industry="Consumer Electronics",
        index_overlap=("SPY", "QQQ"),
        is_candidate=True,
    )
    assert fact.ticker == "AAPL"
    assert fact.direction == "CALL"
    assert fact.planned_dollar_risk == 217.35
    assert fact.full_debit == 511.25
    assert fact.contracts == 3
    assert fact.dte == 37
    assert fact.expiration == "2026-10-09"
    assert fact.index_overlap == ("SPY", "QQQ")
    assert fact.is_candidate is True


def test_bridge_carries_canonical_correlation_risk_tuple_verbatim():
    canonical = (("mega_cap_tech", 350.0), ("semis", 125.5))
    fact = exposure_fact_from_risk_telemetry(_risk_snapshot(correlation_risk=canonical))
    assert fact.correlation_risk == canonical


def test_bridge_has_no_correlation_group_kwarg():
    # One authority: the gate's tuple is the only correlation source. A caller
    # must not be able to inject a fresh per-position label here.
    with pytest.raises(TypeError):
        exposure_fact_from_risk_telemetry(_risk_snapshot(), correlation_group="x")  # type: ignore[call-arg]


def test_bridge_output_flows_through_same_fail_closed_concentration_validation():
    fact = exposure_fact_from_risk_telemetry(
        _risk_snapshot(planned_total_trade_risk=math.nan),
        index_overlap=("SPY",),
    )
    result = measure_concentration([fact])
    assert result.status == ConcentrationStatus.INVALID
    assert result.snapshot is None
    assert "exposures_0_planned_risk_not_finite" in result.reason_codes


def test_aggregates_ticker_and_direction_without_position_cap():
    result = measure_concentration(
        [
            _fact(planned_dollar_risk=100, full_debit=250, contracts=1),
            _fact(planned_dollar_risk=200, full_debit=500, contracts=2, is_candidate=True),
            _fact(
                ticker="MSFT",
                direction="PUT",
                planned_dollar_risk=50,
                full_debit=150,
                contracts=1,
                sector="Technology",
                industry="Software",
                index_overlap=("SPY", "QQQ"),
            ),
        ]
    )
    assert result.complete
    snap = result.snapshot
    assert snap is not None
    assert snap.total_planned_dollar_risk == 350
    assert snap.total_full_debit == 900
    assert snap.position_count == 3
    assert snap.contract_count == 4
    assert snap.candidate_count == 1
    aapl = _bucket(snap.by_ticker, "AAPL")
    assert aapl.planned_dollar_risk == 300
    assert aapl.position_count == 2
    assert aapl.contract_count == 3
    assert aapl.share_of_planned_risk == pytest.approx(300 / 350)
    calls = _bucket(snap.by_direction, "CALL")
    assert calls.planned_dollar_risk == 300
    tech = _bucket(snap.by_sector, "TECHNOLOGY")
    assert tech.planned_dollar_risk == 350


# --- correlation risk: reported from the gate's tuple, never re-aggregated ---


def test_correlation_groups_are_reported_from_candidate_canonical_tuple():
    # Open positions carry their own (older) projections; the candidate's
    # projection is the gate's view of the whole book at evaluation time.
    open_a = _fact(planned_dollar_risk=100, correlation_risk=(("MEGA_CAP_TECH", 100.0),))
    open_b = _fact(
        ticker="MSFT",
        planned_dollar_risk=50,
        correlation_risk=(("MEGA_CAP_TECH", 150.0),),
    )
    candidate = _fact(
        ticker="NVDA",
        planned_dollar_risk=200,
        is_candidate=True,
        correlation_risk=(("mega_cap_tech", 150.0), ("semis", 200.0)),
    )
    result = measure_concentration([open_a, open_b, candidate])
    assert result.complete
    snap = result.snapshot
    assert snap is not None
    assert snap.correlation_risk_reported is True
    assert snap.by_correlation_group == (
        CorrelationRiskBucket("MEGA_CAP_TECH", 150.0, pytest.approx(150 / 350)),
        CorrelationRiskBucket("SEMIS", 200.0, pytest.approx(200 / 350)),
    )


def test_correlation_tuples_are_never_summed_across_facts():
    # Two open facts each carrying the same portfolio-level projection. Summing
    # them would report 600; the module must not aggregate.
    tuple_ = (("MEGA_CAP_TECH", 300.0),)
    facts = [
        _fact(planned_dollar_risk=100, correlation_risk=tuple_),
        _fact(ticker="MSFT", planned_dollar_risk=200, correlation_risk=tuple_),
    ]
    result = measure_concentration(facts)
    assert result.complete
    assert result.snapshot is not None
    # No candidate -> no authority selected -> nothing reported, nothing invented.
    assert result.snapshot.by_correlation_group == ()
    assert result.snapshot.correlation_risk_reported is False

    facts[1] = _fact(ticker="MSFT", planned_dollar_risk=200, correlation_risk=tuple_, is_candidate=True)
    result = measure_concentration(facts)
    assert result.complete
    assert result.snapshot is not None
    assert [(b.name, b.planned_dollar_risk) for b in result.snapshot.by_correlation_group] == [
        ("MEGA_CAP_TECH", 300.0)
    ]


def test_identical_candidate_tuples_agree_but_differing_ones_fail_closed():
    tuple_ = (("MEGA_CAP_TECH", 300.0),)
    ok = measure_concentration(
        [
            _fact(planned_dollar_risk=100, is_candidate=True, correlation_risk=tuple_),
            _fact(ticker="MSFT", planned_dollar_risk=200, is_candidate=True, correlation_risk=tuple_),
        ]
    )
    assert ok.complete

    bad = measure_concentration(
        [
            _fact(planned_dollar_risk=100, is_candidate=True, correlation_risk=tuple_),
            _fact(
                ticker="MSFT",
                planned_dollar_risk=200,
                is_candidate=True,
                correlation_risk=(("MEGA_CAP_TECH", 250.0),),
            ),
        ]
    )
    assert bad.status == ConcentrationStatus.INVALID
    assert bad.snapshot is None
    assert "correlation_risk_conflict" in bad.reason_codes


def test_canonical_group_exceeding_supplied_total_fails_closed():
    # The gate's projection covered positions the caller did not supply.
    result = measure_concentration(
        [_fact(planned_dollar_risk=100, is_candidate=True, correlation_risk=(("MEGA_CAP_TECH", 350.0),))]
    )
    assert result.status == ConcentrationStatus.INVALID
    assert result.snapshot is None
    assert "correlation_risk_MEGA_CAP_TECH_exceeds_total_planned_risk" in result.reason_codes


def test_candidate_with_empty_canonical_tuple_reports_no_groups_but_is_reported():
    result = measure_concentration([_fact(is_candidate=True, correlation_risk=())])
    assert result.complete
    assert result.snapshot is not None
    assert result.snapshot.by_correlation_group == ()
    assert result.snapshot.correlation_risk_reported is True


@pytest.mark.parametrize(
    "bad,reason",
    [
        ("mega_cap_tech", "exposures_0_correlation_risk_not_sequence"),
        (123, "exposures_0_correlation_risk_not_sequence"),
        ((("mega_cap_tech",),), "exposures_0_correlation_risk_0_malformed"),
        (((123, 1.0),), "exposures_0_correlation_risk_0_group_not_string"),
        (((" ", 1.0),), "exposures_0_correlation_risk_0_group_missing"),
        ((("g", math.nan),), "exposures_0_correlation_risk_0_not_finite"),
        ((("g", math.inf),), "exposures_0_correlation_risk_0_not_finite"),
        ((("g", -1.0),), "exposures_0_correlation_risk_0_negative"),
        ((("g", "x"),), "exposures_0_correlation_risk_0_not_numeric"),
        ((("Tech", 1.0), ("tech", 2.0)), "exposures_0_correlation_risk_1_group_case_collision"),
    ],
)
def test_malformed_canonical_correlation_tuple_fails_closed(bad, reason):
    result = measure_concentration([_fact(correlation_risk=bad)])
    assert result.status == ConcentrationStatus.INVALID
    assert result.snapshot is None
    assert reason in result.reason_codes


# --- label normalization ---


def test_sector_industry_labels_are_case_normalized_into_one_bucket():
    result = measure_concentration(
        [
            _fact(planned_dollar_risk=100, sector="Technology", industry="Software"),
            _fact(ticker="MSFT", planned_dollar_risk=200, sector="technology", industry=" SOFTWARE "),
            _fact(ticker="nvda", planned_dollar_risk=50, sector="TECHNOLOGY", industry="software"),
        ]
    )
    assert result.complete
    snap = result.snapshot
    assert snap is not None
    assert [b.name for b in snap.by_sector] == ["TECHNOLOGY"]
    assert snap.by_sector[0].planned_dollar_risk == 350
    assert snap.by_sector[0].position_count == 3
    assert [b.name for b in snap.by_industry] == ["SOFTWARE"]
    assert snap.by_industry[0].planned_dollar_risk == 350
    assert [b.name for b in snap.by_ticker] == ["AAPL", "MSFT", "NVDA"]


def test_index_overlap_is_multi_valued_not_allocation():
    result = measure_concentration([_fact(index_overlap=("spy", "QQQ"))])
    snap = result.snapshot
    assert snap is not None
    assert _bucket(snap.by_index_overlap, "SPY").share_of_planned_risk == 1
    assert _bucket(snap.by_index_overlap, "QQQ").share_of_planned_risk == 1


def test_unknown_labels_are_counted_not_inferred():
    result = measure_concentration(
        [
            _fact(sector="", industry="", index_overlap=()),
            _fact(ticker="MSFT", sector="Technology", industry=None),
        ]
    )
    snap = result.snapshot
    assert snap is not None
    assert snap.unknown_sector_count == 1
    assert snap.unknown_industry_count == 2
    assert snap.unknown_index_overlap_count == 1


@pytest.mark.parametrize(
    "dte,expected",
    [
        (0, "0DTE"),
        (1, "1-7DTE"),
        (7, "1-7DTE"),
        (8, "8-30DTE"),
        (30, "8-30DTE"),
        (31, "31-60DTE"),
        (60, "31-60DTE"),
        (61, "61+DTE"),
    ],
)
def test_dte_buckets(dte, expected):
    result = measure_concentration([_fact(dte=dte)])
    snap = result.snapshot
    assert snap is not None
    assert [bucket.name for bucket in snap.by_dte_bucket] == [expected]


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf, "nan", "inf"])
def test_nonfinite_planned_risk_fails_closed(bad):
    result = measure_concentration([_fact(planned_dollar_risk=bad)])
    assert result.status == ConcentrationStatus.INVALID
    assert result.snapshot is None
    assert any("planned_risk_not_finite" in reason for reason in result.reason_codes)


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf, "nan", "inf"])
def test_nonfinite_full_debit_fails_closed(bad):
    result = measure_concentration([_fact(full_debit=bad)])
    assert result.status == ConcentrationStatus.INVALID
    assert result.snapshot is None
    assert any("full_debit_not_finite" in reason for reason in result.reason_codes)


def test_negative_values_fail_closed():
    result = measure_concentration([_fact(planned_dollar_risk=-1, full_debit=-1)])
    assert result.status == ConcentrationStatus.INVALID
    assert "exposures_0_planned_risk_negative" in result.reason_codes
    assert "exposures_0_full_debit_negative" in result.reason_codes


@pytest.mark.parametrize(
    "overrides,reason",
    [
        ({"ticker": ""}, "exposures_0_ticker_missing"),
        ({"ticker": None}, "exposures_0_ticker_missing"),
        ({"direction": "SIDEWAYS"}, "exposures_0_direction_invalid"),
        ({"expiration": ""}, "exposures_0_expiration_missing"),
        ({"expiration": None}, "exposures_0_expiration_missing"),
        ({"dte": -1}, "exposures_0_dte_invalid"),
        ({"dte": True}, "exposures_0_dte_invalid"),
        ({"contracts": 0}, "exposures_0_contracts_invalid"),
        ({"contracts": True}, "exposures_0_contracts_invalid"),
        ({"is_candidate": 1}, "exposures_0_is_candidate_not_bool"),
        ({"index_overlap": ("SPY", "SPY")}, "exposures_0_index_overlap_duplicate"),
        ({"index_overlap": ("spy", "SPY")}, "exposures_0_index_overlap_duplicate"),
        ({"index_overlap": ("",)}, "exposures_0_index_overlap_empty"),
        ({"index_overlap": "SPY"}, "exposures_0_index_overlap_not_sequence"),
        ({"index_overlap": (123,)}, "exposures_0_index_overlap_member_not_string"),
        ({"sector": 123}, "exposures_0_sector_not_string"),
        ({"industry": 123}, "exposures_0_industry_not_string"),
    ],
)
def test_malformed_fact_fails_closed(overrides, reason):
    result = measure_concentration([_fact(**overrides)])
    assert result.status == ConcentrationStatus.INVALID
    assert result.snapshot is None
    assert reason in result.reason_codes


def test_noniterable_exposures_fail_closed():
    result = measure_concentration(None)
    assert result.status == ConcentrationStatus.INVALID
    assert result.snapshot is None
    assert result.reason_codes == ("exposures_not_iterable",)


def test_wrong_exposure_type_fails_closed():
    result = measure_concentration([{"ticker": "AAPL"}])
    assert result.status == ConcentrationStatus.INVALID
    assert result.snapshot is None
    assert result.reason_codes == ("exposures_0_wrong_type",)


def test_position_count_is_measurement_only_even_at_large_count():
    facts = [
        _fact(
            ticker=f"T{i}",
            planned_dollar_risk=10,
            full_debit=20,
            index_overlap=(),
            sector="",
            industry="",
        )
        for i in range(50)
    ]
    result = measure_concentration(facts)
    assert result.complete
    assert result.snapshot is not None
    assert result.snapshot.position_count == 50
    assert result.snapshot.total_planned_dollar_risk == 500


def test_zero_risk_and_debit_have_zero_shares_not_divide_by_zero():
    result = measure_concentration([_fact(planned_dollar_risk=0, full_debit=0)])
    snap = result.snapshot
    assert snap is not None
    bucket = _bucket(snap.by_ticker, "AAPL")
    assert bucket.share_of_planned_risk == 0
    assert bucket.share_of_full_debit == 0
