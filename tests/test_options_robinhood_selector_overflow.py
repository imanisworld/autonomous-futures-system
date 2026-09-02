from options_manager.adapters.robinhood_selector import build_selector_candidates


RETRIEVED = "2026-09-02T13:26:10+00:00"
INSTRUMENT = {
    "id": "contract-1",
    "chain_symbol": "SPY",
    "expiration_date": "2026-10-16",
    "strike_price": "760.0000",
    "type": "call",
    "state": "active",
    "tradability": "tradable",
}
QUOTE = {
    "instrument_id": "contract-1",
    "ask_price": "16.500000",
    "bid_price": "16.380000",
    "mark_price": "16.440000",
    "implied_volatility": "0.129566",
    "delta": "0.569061",
    "theta": "-0.196118",
    "open_interest": 7683,
    "volume": "inf",
    "updated_at": "2026-09-01T20:14:59Z",
}


def test_integer_normalization_overflow_is_rejected_not_raised():
    result = build_selector_candidates(
        [INSTRUMENT],
        [QUOTE],
        ticker="SPY",
        direction="CALL",
        retrieved_at=RETRIEVED,
    )

    assert result.records == ()
    assert result.rejected
    assert result.rejected[0].reason_code == "non_finite_volume"
