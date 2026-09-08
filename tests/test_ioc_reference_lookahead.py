"""Guardrail from the 2026-09-07 VWAP Hold reconciliation: a research IOC fill
must not reference a price that is only known after the decision time.

`ioc_fill(field="close")` checks the ARRIVAL bar's close, which on 5m bars is
printed 5 minutes after the decision-bar close that replay/production use.
The result now reports that look-ahead and `assert_decision_time_reference`
refuses it. The package's own behaviour (prices, statuses) is unchanged so the
2026-07-26 cells and the reconciliation still reproduce.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from scripts.vwap_hold_evidence_package import (
    LookaheadError,
    assert_decision_time_reference,
    ioc_fill,
)

T0 = datetime(2026, 3, 2, 14, 30)


def _bars(n: int = 4, start: datetime = T0, minutes: int = 5) -> list[dict]:
    out = []
    px = 19500.0
    for i in range(n):
        ts = start + timedelta(minutes=minutes * i)
        out.append({"ts": ts, "open": px + i, "high": px + i + 2, "low": px + i - 2, "close": px + i + 1})
    return out


def _arm(entry: float = 19500.0, direction: str = "LONG") -> dict:
    return {"armed_at": T0, "direction": direction, "entry": entry}


def test_open_reference_is_decision_time():
    fill = ioc_fill(_arm(), _bars(), "open")
    assert fill["status"] == "FILLED"
    assert fill["reference_ts"] == T0
    assert fill["lookahead_minutes"] == 0.0
    assert assert_decision_time_reference(fill) is fill


def test_close_reference_is_one_bar_lookahead_and_refused():
    fill = ioc_fill(_arm(), _bars(), "close")
    assert fill["status"] == "FILLED"
    assert fill["reference_ts"] == T0 + timedelta(minutes=5)
    assert fill["lookahead_minutes"] == 5.0
    with pytest.raises(LookaheadError, match="5 min after the decision"):
        assert_decision_time_reference(fill)


def test_bar_length_is_inferred_from_the_series():
    fill = ioc_fill(_arm(), _bars(minutes=15), "close")
    assert fill["lookahead_minutes"] == 15.0


def test_unfilled_result_also_carries_reference_timing():
    # LONG with market far above entry + 32 ticks -> unmarketable
    fill = ioc_fill(_arm(entry=19400.0), _bars(), "close")
    assert fill["status"] == "ENTRY_NOT_FILLED"
    assert fill["lookahead_minutes"] == 5.0
    with pytest.raises(LookaheadError):
        assert_decision_time_reference(fill)


def test_same_bar_decision_close_passes_with_explicit_decision_ts():
    # The reconciliation's honest reference: the signal bar's own last 5m bar,
    # whose close IS the decision-bar close. Reference_ts == decision_ts -> ok.
    bars = _bars(start=T0 - timedelta(minutes=5))
    fill = ioc_fill({**_arm(), "armed_at": T0 - timedelta(minutes=5)}, bars, "close")
    assert fill["reference_ts"] == T0
    assert assert_decision_time_reference(fill, decision_ts=T0) is fill


def test_explicit_decision_ts_earlier_than_reference_is_refused():
    fill = ioc_fill(_arm(), _bars(), "open")
    with pytest.raises(LookaheadError):
        assert_decision_time_reference(fill, decision_ts=T0 - timedelta(minutes=10))


def test_no_data_passes_through():
    fill = ioc_fill(_arm(), [], "close")
    assert fill["status"] == "NO_DATA"
    assert assert_decision_time_reference(fill) is fill


def test_result_without_reference_ts_is_refused():
    with pytest.raises(LookaheadError, match="no reference_ts"):
        assert_decision_time_reference({"status": "FILLED"})
