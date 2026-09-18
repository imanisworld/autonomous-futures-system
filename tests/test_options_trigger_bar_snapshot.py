from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json

import pytest

from alert_ranker.causal_bars import Bar, MINUTE_5
from scripts.options_trigger_bar_snapshot import (
    SNAPSHOT_ID,
    SNAPSHOT_VERSION,
    canonical_bar_payload,
    payload_summary,
)

UTC = timezone.utc


def _bar(minute: int, *, close: float = 100.0) -> Bar:
    start = datetime(2026, 9, 18, 13, 30 + minute, tzinfo=UTC)
    return Bar(
        start=start,
        open=close - 0.5,
        high=close + 1.0,
        low=close - 1.0,
        close=close,
        volume=1000.0 + minute,
        vwap=close - 0.1,
    )


def test_snapshot_identity_is_explicit():
    assert SNAPSHOT_ID == "OPTIONS_TRIGGER_BAR_SNAPSHOT"
    assert SNAPSHOT_VERSION == "trigger-bars-v0.1"


def test_canonical_payload_is_order_independent():
    a = _bar(0, close=100.0)
    b = _bar(5, close=101.0)
    first = canonical_bar_payload(
        {"SPY": [b, a], "QQQ": [a]},
        timeframe=MINUTE_5,
    )
    second = canonical_bar_payload(
        {"QQQ": [a], "SPY": [a, b]},
        timeframe=MINUTE_5,
    )
    assert first == second

    rows = [json.loads(line) for line in first.splitlines()]
    assert [(row["symbol"], row["start"]) for row in rows] == sorted(
        (row["symbol"], row["start"]) for row in rows
    )


def test_duplicate_symbol_timestamp_fails_closed():
    with pytest.raises(ValueError, match="duplicate bar key"):
        canonical_bar_payload(
            {"SPY": [_bar(0), _bar(0)]},
            timeframe=MINUTE_5,
        )


def test_payload_summary_hashes_exact_bytes():
    payload = canonical_bar_payload(
        {"SPY": [_bar(0), _bar(5)], "QQQ": [_bar(0)]},
        timeframe=MINUTE_5,
    )
    summary = payload_summary(payload)
    assert summary["rows"] == 3
    assert summary["rows_by_symbol"] == {"QQQ": 1, "SPY": 2}
    assert summary["sha256"] == hashlib.sha256(payload).hexdigest()
    assert summary["first_bar_start"] == "2026-09-18T13:30:00+00:00"
    assert summary["last_bar_start"] == "2026-09-18T13:35:00+00:00"


def test_nonfinite_bar_value_refuses_canonical_json():
    bad = Bar(
        start=datetime(2026, 9, 18, 13, 30, tzinfo=UTC),
        open=100.0,
        high=float("nan"),
        low=99.0,
        close=100.0,
        volume=1000.0,
        vwap=100.0,
    )
    with pytest.raises(ValueError):
        canonical_bar_payload({"SPY": [bad]}, timeframe=MINUTE_5)
