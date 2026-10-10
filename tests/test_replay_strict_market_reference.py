"""Strict replay references must be next same-market bar opens, never signal closes."""
from __future__ import annotations

from types import SimpleNamespace as Bar

import pytest

from replay.replay_engine import _next_executable_market_reference


def _bar(timestamp: str, instrument: str = "MNQ", timeframe: str = "5m", open_price: float = 100.0):
    return Bar(timestamp=timestamp, instrument=instrument, timeframe=timeframe, open=open_price)


def test_adjacent_bar_open_is_executable_reference_not_signal_close() -> None:
    candles = [
        _bar("2026-06-02T09:30:00-04:00", open_price=99.0),
        _bar("2026-06-02T09:35:00-04:00", open_price=104.0),
    ]
    reference, ts = _next_executable_market_reference(candles, 0)
    assert reference == 104.0
    assert ts == "2026-06-02T09:35:00-04:00"


def test_interleaved_market_cannot_supply_another_markets_price() -> None:
    candles = [
        _bar("2026-06-02T09:30:00-04:00", "MNQ", open_price=99.0),
        _bar("2026-06-02T09:35:00-04:00", "MES", open_price=4000.0),
        _bar("2026-06-02T09:35:00-04:00", "MNQ", open_price=104.0),
    ]
    assert _next_executable_market_reference(candles, 0)[0] == 104.0


@pytest.mark.parametrize("later", [
    [],
    [_bar("2026-06-02T09:40:00-04:00", open_price=105.0)],
    [_bar("2026-06-03T09:35:00-04:00", open_price=105.0)],
])
def test_missing_or_gapped_next_bar_is_not_a_fill(later) -> None:
    candles = [_bar("2026-06-02T09:30:00-04:00", open_price=99.0), *later]
    assert _next_executable_market_reference(candles, 0) is None
