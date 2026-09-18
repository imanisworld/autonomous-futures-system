from datetime import datetime, timezone

import pytest

from scripts.options_trigger_trade_timestamp_audit import (
    CanonicalTrade,
    TriggerTradeAuditError,
    canonical_trade_payload,
    eligible_trade_ohlc,
    first_crossing_trade,
    minute_price_eligible,
    parse_trade,
    timestamp_ns,
    trades_in_window,
)

UTC = timezone.utc


def _trade(
    *,
    timestamp: str,
    price: float,
    conditions=("@",),
    tape="C",
    trade_id="1",
):
    return CanonicalTrade(
        symbol="AMZN",
        timestamp=timestamp,
        timestamp_ns=timestamp_ns(timestamp),
        price=price,
        size=100,
        exchange="D",
        conditions=tuple(conditions),
        tape=tape,
        trade_id=trade_id,
    )


def test_minute_price_condition_filter_uses_strictest_rule():
    assert minute_price_eligible(tape="C", conditions=["@"]) is True
    assert minute_price_eligible(tape="C", conditions=["F"]) is True
    assert minute_price_eligible(tape="C", conditions=["@", "4"]) is False
    assert minute_price_eligible(tape="C", conditions=["@", "I"]) is False
    assert minute_price_eligible(tape="A", conditions=[]) is True
    assert minute_price_eligible(tape="A", conditions=[" "]) is True
    assert minute_price_eligible(tape="A", conditions=[" ", "F"]) is True
    assert minute_price_eligible(tape="A", conditions=[" ", "I"]) is False
    with pytest.raises(TriggerTradeAuditError, match="empty condition"):
        minute_price_eligible(tape="C", conditions=[])
    with pytest.raises(TriggerTradeAuditError, match="unknown trade condition"):
        minute_price_eligible(tape="C", conditions=["UNKNOWN"])
    with pytest.raises(TriggerTradeAuditError, match="unknown SIP tape"):
        minute_price_eligible(tape="?", conditions=["@"])


def test_first_crossing_skips_earlier_non_price_forming_trade():
    start = datetime(2026, 9, 9, 14, 45, tzinfo=UTC)
    end = datetime(2026, 9, 9, 14, 50, tzinfo=UTC)
    trades = [
        _trade(
            timestamp="2026-09-09T14:45:01.000000000Z",
            price=252.70,
            conditions=("@", "4"),
            trade_id="1",
        ),
        _trade(
            timestamp="2026-09-09T14:45:02.000000000Z",
            price=252.60,
            conditions=("@",),
            trade_id="2",
        ),
        _trade(
            timestamp="2026-09-09T14:45:03.123456789Z",
            price=252.65,
            conditions=("@",),
            trade_id="3",
        ),
        _trade(
            timestamp="2026-09-09T14:45:03.123456790Z",
            price=252.66,
            conditions=("@",),
            trade_id="4",
        ),
    ]
    crossing, eligible = first_crossing_trade(
        trades=trades,
        direction="LONG",
        trigger_level=252.65,
        window_start=start,
        window_end=end,
    )
    assert [item.trade_id for item in eligible] == ["2", "3", "4"]
    assert crossing is not None
    assert crossing.trade_id == "4"
    assert crossing.timestamp_ns == timestamp_ns("2026-09-09T14:45:03.123456790Z")


def test_first_crossing_short_is_symmetric_and_window_end_is_exclusive():
    start = datetime(2026, 9, 9, 18, 0, tzinfo=UTC)
    end = datetime(2026, 9, 9, 18, 5, tzinfo=UTC)
    trades = [
        _trade(
            timestamp="2026-09-09T18:00:01.000000000Z",
            price=252.10,
            trade_id="1",
        ),
        _trade(
            timestamp="2026-09-09T18:00:02.000000000Z",
            price=252.055,
            trade_id="2",
        ),
        _trade(
            timestamp="2026-09-09T18:00:03.000000000Z",
            price=252.05,
            trade_id="3",
        ),
        _trade(
            timestamp="2026-09-09T18:05:00.000000000Z",
            price=251.00,
            trade_id="4",
        ),
    ]
    crossing, eligible = first_crossing_trade(
        trades=trades,
        direction="SHORT",
        trigger_level=252.055,
        window_start=start,
        window_end=end,
    )
    assert [item.trade_id for item in eligible] == ["1", "2", "3"]
    assert crossing is not None
    assert crossing.trade_id == "3"


def test_nanosecond_timestamp_parser_preserves_full_precision():
    value = "2026-09-09T14:46:33.160755814Z"
    parsed = timestamp_ns(value)
    assert parsed % 1_000_000_000 == 160755814


@pytest.mark.parametrize(
    "value",
    [
        "",
        "2026-09-09T14:46:33+00:00",
        "not-a-time",
        None,
    ],
)
def test_invalid_trade_timestamp_fails_closed(value):
    with pytest.raises(TriggerTradeAuditError, match="invalid SIP timestamp"):
        timestamp_ns(value)


def test_parse_trade_rejects_nonpositive_or_malformed_values():
    with pytest.raises(TriggerTradeAuditError):
        parse_trade(
            "AMZN",
            {"t": "2026-09-09T14:46:33.160755814Z", "p": 0, "s": 100},
        )
    with pytest.raises(TriggerTradeAuditError):
        parse_trade(
            "AMZN",
            {
                "t": "2026-09-09T14:46:33.160755814Z",
                "p": 252.65,
                "s": 100,
                "c": "not-a-list",
            },
        )


def test_canonical_trade_payload_is_sorted_and_deduplicated():
    later = _trade(
        timestamp="2026-09-09T14:46:34.000000000Z",
        price=252.66,
        trade_id="2",
    )
    earlier = _trade(
        timestamp="2026-09-09T14:46:33.000000000Z",
        price=252.65,
        trade_id="1",
    )
    payload = canonical_trade_payload([later, earlier, earlier])
    lines = payload.splitlines()
    assert len(lines) == 2
    assert b'"trade_id":"1"' in lines[0]
    assert b'"trade_id":"2"' in lines[1]


def test_raw_trade_window_is_start_inclusive_end_exclusive():
    start = datetime(2026, 9, 9, 14, 45, tzinfo=UTC)
    end = datetime(2026, 9, 9, 14, 50, tzinfo=UTC)
    rows = trades_in_window(
        [
            _trade(
                timestamp="2026-09-09T14:44:59.999999999Z",
                price=252.0,
                trade_id="before",
            ),
            _trade(
                timestamp="2026-09-09T14:45:00.000000000Z",
                price=252.1,
                trade_id="start",
            ),
            _trade(
                timestamp="2026-09-09T14:49:59.999999999Z",
                price=252.2,
                trade_id="inside",
            ),
            _trade(
                timestamp="2026-09-09T14:50:00.000000000Z",
                price=252.3,
                trade_id="end",
            ),
        ],
        window_start=start,
        window_end=end,
    )
    assert [row.trade_id for row in rows] == ["start", "inside"]


def test_eligible_trade_ohlc_reconstructs_price_forming_sequence():
    rows = [
        _trade(
            timestamp="2026-09-09T14:45:00.100000000Z",
            price=252.475,
            trade_id="1",
        ),
        _trade(
            timestamp="2026-09-09T14:45:01.100000000Z",
            price=252.69,
            trade_id="2",
        ),
        _trade(
            timestamp="2026-09-09T14:45:02.100000000Z",
            price=252.10,
            trade_id="3",
        ),
        _trade(
            timestamp="2026-09-09T14:49:59.100000000Z",
            price=252.2931,
            trade_id="4",
        ),
    ]
    assert eligible_trade_ohlc(rows) == {
        "open": 252.475,
        "high": 252.69,
        "low": 252.10,
        "close": 252.2931,
    }


def test_eligible_trade_ohlc_fails_closed_when_empty():
    with pytest.raises(TriggerTradeAuditError, match="no minute-price-eligible trades"):
        eligible_trade_ohlc([])
