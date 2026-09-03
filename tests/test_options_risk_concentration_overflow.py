import math
import sys

from options_manager.risk.concentration import (
    ConcentrationStatus,
    ExposureFact,
    measure_concentration,
)


def _fact(**overrides) -> ExposureFact:
    values = dict(
        ticker="AAPL",
        direction="CALL",
        planned_dollar_risk=100.0,
        full_debit=250.0,
        dte=45,
        expiration="2026-10-16",
        contracts=1,
    )
    values.update(overrides)
    return ExposureFact(**values)


def test_huge_integer_risk_fails_closed_without_raising():
    result = measure_concentration([_fact(planned_dollar_risk=10**10000)])

    assert result.status == ConcentrationStatus.INVALID
    assert result.snapshot is None
    assert "exposures_0_planned_risk_not_numeric" in result.reason_codes


def test_total_planned_risk_overflow_to_inf_fails_closed():
    # Each addend is finite and passes per-fact validation; only the sum
    # overflows. That must never become COMPLETE with an inf total and 0 shares.
    big = sys.float_info.max
    facts = [_fact(ticker="AAPL", planned_dollar_risk=big), _fact(ticker="MSFT", planned_dollar_risk=big)]
    assert math.isinf(big + big)

    result = measure_concentration(facts)

    assert result.status == ConcentrationStatus.INVALID
    assert result.snapshot is None
    assert "total_planned_risk_not_finite" in result.reason_codes


def test_total_full_debit_overflow_to_inf_fails_closed():
    big = sys.float_info.max
    facts = [_fact(ticker="AAPL", full_debit=big), _fact(ticker="MSFT", full_debit=big)]

    result = measure_concentration(facts)

    assert result.status == ConcentrationStatus.INVALID
    assert result.snapshot is None
    assert "total_full_debit_not_finite" in result.reason_codes
