from __future__ import annotations

from options_manager.adapters.robinhood_readonly import (
    normalize_account_summary,
    normalize_option_quote,
    normalize_portfolio_positions,
    normalize_underlying_quote,
)


def test_huge_integer_provider_value_does_not_raise_or_get_fabricated() -> None:
    quote = normalize_option_quote({"volume": 10**10000, "open_interest": 10**10000})
    assert quote.volume is None
    assert quote.open_interest is None


def test_fractional_integer_fields_are_not_truncated() -> None:
    quote = normalize_option_quote({"dte": "44.5", "volume": "3.5", "open_interest": 7.25})
    assert quote.dte is None
    assert quote.volume is None
    assert quote.open_interest is None


def test_nonfinite_and_boolean_numeric_fields_remain_unresolved() -> None:
    quote = normalize_option_quote(
        {
            "mark_price": "nan",
            "bid_price": "inf",
            "ask_price": "-inf",
            "volume": True,
            "open_interest": False,
        }
    )
    assert quote.premium is None
    assert quote.bid is None
    assert quote.ask is None
    assert quote.volume is None
    assert quote.open_interest is None
    assert quote.spread_percent is None


def test_other_readonly_numeric_mappers_share_the_same_fail_closed_behavior() -> None:
    snapshot = normalize_underlying_quote({"mark_price": 10**10000})
    summary = normalize_account_summary({"cash": float("inf")})
    positions = normalize_portfolio_positions(
        [{"symbol": "AAPL", "quantity": float("nan"), "current_price": 10**10000}]
    )
    assert snapshot.spot_price is None
    assert summary.cash is None
    assert len(positions) == 1
    assert positions[0].quantity is None
    assert positions[0].current_price is None
