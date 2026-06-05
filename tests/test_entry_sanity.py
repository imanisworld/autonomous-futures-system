"""
tests/test_entry_sanity.py

Locks the entry-sanity guard. Entries are sent as MARKET orders, so they fill at
the LIVE price, not the planned level. When a level (VWAP/ORB/PDH...) is stale or
detached from price — e.g. a session VWAP stranded ~120pt above price after an
overnight feed gap — the bracket lands on the wrong side of the fill and the
trade scratches/loses instantly.

Reproduces the 2026-06-05 03:15 ET MNQ incident: vwap_hold SHORT, entry 30201.25
(= VWAP), stop 30208.75, target 30178.75, while the market was 30080.75. The
target ended up ABOVE the fill, so the bracket no longer straddled the price.
The guard must REJECT such a setup instead of chasing the market fill.
"""
from __future__ import annotations

from strategy.signal_engine import DecisionEngine

straddles = DecisionEngine._entry_bracket_straddles_price


def test_real_incident_short_is_rejected():
    # Exact 2026-06-05 03:15 ET MNQ vwap_hold numbers — target ABOVE the fill.
    assert straddles("SHORT", 30201.25, 30208.75, 30178.75, 30080.75) is False


def test_normal_short_is_allowed():
    # Price between target (below) and stop (above) → valid bracket.
    assert straddles("SHORT", 30201.25, 30208.75, 30178.75, 30200.0) is True


def test_normal_long_is_allowed():
    assert straddles("LONG", 100.0, 95.0, 110.0, 101.0) is True


def test_stale_long_above_target_is_rejected():
    # Price already ran past the target → don't chase.
    assert straddles("LONG", 100.0, 95.0, 110.0, 130.0) is False


def test_long_already_below_stop_is_rejected():
    # Price already at/through the stop side → broken.
    assert straddles("LONG", 100.0, 95.0, 110.0, 90.0) is False


def test_short_already_at_stop_is_rejected():
    assert straddles("SHORT", 100.0, 107.0, 80.0, 108.0) is False


def test_missing_price_does_not_block():
    # No price to check against → don't block here (other gates handle bad data).
    assert straddles("SHORT", 100.0, 107.0, 80.0, None) is True


def test_boundary_price_equal_to_target_is_rejected():
    # price == target is not strictly inside the bracket → reject (no edge fills).
    assert straddles("SHORT", 100.0, 107.0, 80.0, 80.0) is False
