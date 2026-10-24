"""Synthetic Tradovate response clock tests. No broker authentication or network calls."""
from execution.tradovate_broker import TradovateBroker, _broker_utc_timestamp


def test_order_timestamp_is_utc_or_unknown():
    assert _broker_utc_timestamp("2026-09-08T10:00:00-04:00") == "2026-09-08T14:00:00+00:00"
    assert _broker_utc_timestamp("2026-09-08T14:00:00Z") == "2026-09-08T14:00:00+00:00"
    for invalid in ("2026-09-08T14:00:00", "garbage", "", None, 123):
        assert _broker_utc_timestamp(invalid) is None


def test_existing_order_and_fill_reads_capture_broker_timestamps_without_extra_api_calls():
    broker = object.__new__(TradovateBroker)
    broker._entry_timing = {"order_type": "Limit"}
    broker._account_id = 123
    calls = []

    def from_fixture(path):
        calls.append(path)
        if path == "/order/item?id=11":
            return {"id": 11, "timestamp": "2026-09-08T14:05:01Z", "ordStatus": "Filled"}
        if path == "/fill/list?accountId=123":
            return [
                {"orderId": 99, "qty": 1, "price": 18_000.0, "timestamp": "2026-09-07T09:00:00Z"},
                {"orderId": 11, "qty": 1, "price": 20_000.5, "timestamp": "2026-09-08T14:05:03Z"},
                {"orderId": 11, "qty": 1, "price": 20_000.25, "timestamp": "2026-09-08T14:05:02Z"},
            ]
        raise AssertionError("unrecognized GET")

    broker._get = from_fixture
    assert broker._entry_status(11, retries=1, delay=0) == "filled"
    assert broker._entry_fill_price(11, "MNQ") == 20_000.375
    assert broker._entry_timing == {
        "order_type": "Limit",
        "broker_order_created_at_utc": "2026-09-08T14:05:01+00:00",
        "broker_first_fill_at_utc": "2026-09-08T14:05:02+00:00",
        "broker_last_fill_at_utc": "2026-09-08T14:05:03+00:00",
    }
    assert calls == ["/order/item?id=11", "/fill/list?accountId=123"]


def test_mismatched_order_identity_does_not_create_false_order_timestamp():
    broker = object.__new__(TradovateBroker)
    broker._entry_timing = {"order_type": "Limit"}
    broker._get = lambda path: {
        "id": 88, "timestamp": "2026-09-08T14:05:01Z", "ordStatus": "Filled"
    }
    broker._entry_status(11, retries=1, delay=0)
    assert "broker_order_created_at_utc" not in broker._entry_timing
