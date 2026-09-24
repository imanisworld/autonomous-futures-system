"""Overnight high/low and RTH open: Pine emits them, the payload keeps them,
and the two shadow-only observers that need them can fire.

Before this change `strategy/shadow_setups.py::_overnight_sweep_reclaim` and
`_gap_fill` read `overnight_high` / `overnight_low` / `rth_open` from
`state.raw`, but the Pine payload never carried those keys and the pydantic
model dropped unknown keys, so both observers returned None on every bar
(box journals: 0 rows ever). Shadow-only; no order path.
"""
from pathlib import Path

from strategy.shadow_setups import _gap_fill, _overnight_sweep_reclaim
from webhook.payload import AlertPayload
from webhook.state_builder import build_market_state

PINE = Path("tradingview/risksentinel_context.pine")


def _source() -> str:
    return PINE.read_text()


# ── Pine source locks ────────────────────────────────────────────────────────

def test_pine_tracks_overnight_range_and_rth_open():
    src = _source()
    assert 'ovn_active = not na(time(timeframe.period, "1800-0930", "America/New_York"))' in src
    assert "ovn_start  = ovn_active and not ovn_active[1]" in src
    # RTH open is captured on the NY session's first bar (same ny_start the ORB uses).
    assert "if ny_start\n    rth_open := open" in src
    # Fail-closed expiry at the canonical NY runtime session end, like the ORB.
    expiry = """if not ny_runtime_active and ny_runtime_active[1]
    ovn_h    := na
    ovn_l    := na
    rth_open := na"""
    assert expiry in src
    # The block must come after ny_start / ny_runtime_active are defined.
    assert src.index("ny_start = in_ny and not in_ny[1]") < src.index("ovn_active =")


def test_pine_emits_the_three_keys_in_the_payload():
    src = _source()
    for line in (
        'msg := msg + "\\"overnight_high\\":" + f2j(ovn_h, "#.##") + ","',
        'msg := msg + "\\"overnight_low\\":"  + f2j(ovn_l, "#.##") + ","',
        'msg := msg + "\\"rth_open\\":"       + f2j(rth_open, "#.##") + ","',
    ):
        assert line in src


# ── Payload → state.raw ──────────────────────────────────────────────────────

def _payload(**extra) -> AlertPayload:
    base = dict(
        ticker="MNQ1!", timestamp="2026-09-24T14:35:00Z", timeframe="15",
        open=20000.0, high=20030.0, low=19990.0, close=20010.0,
        previous_day_high=20050.0, previous_day_low=19900.0, previous_day_close=19980.0,
        session="new_york",
    )
    base.update(extra)
    return AlertPayload(**base)


def test_payload_model_keeps_the_new_keys():
    raw = _payload(overnight_high=20040.0, overnight_low=19950.0, rth_open=20002.0).model_dump()
    assert raw["overnight_high"] == 20040.0
    assert raw["overnight_low"] == 19950.0
    assert raw["rth_open"] == 20002.0


def test_payload_model_defaults_to_none_when_absent():
    raw = _payload().model_dump()
    assert raw["overnight_high"] is None and raw["overnight_low"] is None and raw["rth_open"] is None


# ── The observers can now fire ───────────────────────────────────────────────

def test_overnight_high_sweep_reclaim_fires_from_payload():
    # Bar pokes above the overnight high (20025) and closes back below it.
    state = build_market_state(_payload(
        high=20030.0, close=20010.0, overnight_high=20025.0, overnight_low=19950.0,
    ))
    cand = _overnight_sweep_reclaim(state)
    assert cand is not None
    assert cand.strategy == "ovn_high_sweep_reclaim"
    assert cand.direction == "SHORT"


def test_overnight_observer_is_none_without_the_keys():
    state = build_market_state(_payload(high=20030.0, close=20010.0))
    assert _overnight_sweep_reclaim(state) is None


def test_gap_fill_fires_from_payload():
    # Gap up ≥ 8 pts over previous close (19980 → 20002) and the bar closes below the open.
    state = build_market_state(_payload(rth_open=20002.0, close=19995.0, high=20005.0))
    cand = _gap_fill(state)
    assert cand is not None
    assert cand.strategy == "gap_fill"
    assert cand.direction == "SHORT"
    assert cand.target == 19980.0


def test_gap_fill_is_none_without_rth_open():
    state = build_market_state(_payload(close=19995.0))
    assert _gap_fill(state) is None
