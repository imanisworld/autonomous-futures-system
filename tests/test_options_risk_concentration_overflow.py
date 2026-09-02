from options_manager.risk.concentration import (
    ConcentrationStatus,
    ExposureFact,
    measure_concentration,
)


def test_huge_integer_risk_fails_closed_without_raising():
    fact = ExposureFact(
        ticker="AAPL",
        direction="CALL",
        planned_dollar_risk=10**10000,
        full_debit=250.0,
        dte=45,
        expiration="2026-10-16",
        contracts=1,
    )

    result = measure_concentration([fact])

    assert result.status == ConcentrationStatus.INVALID
    assert result.snapshot is None
    assert "exposures_0_planned_risk_not_numeric" in result.reason_codes
