from datetime import datetime, timezone

from scripts.options_212r_trigger_source_probe import _age_seconds, _is_future

UTC = timezone.utc


def test_quote_age_uses_response_receipt_time():
    received = datetime(2026, 9, 18, 18, 22, 5, tzinfo=UTC)
    assert _age_seconds(received, "2026-09-18T18:22:03Z") == 2.0
    assert _is_future(received, "2026-09-18T18:22:03Z") is False


def test_future_provider_timestamp_is_explicit():
    received = datetime(2026, 9, 18, 18, 22, 5, tzinfo=UTC)
    assert _age_seconds(received, "2026-09-18T18:22:06Z") == -1.0
    assert _is_future(received, "2026-09-18T18:22:06Z") is True


def test_missing_timestamp_remains_missing():
    received = datetime(2026, 9, 18, 18, 22, 5, tzinfo=UTC)
    assert _age_seconds(received, None) is None
    assert _is_future(received, None) is None
