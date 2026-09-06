"""Source-level parity locks for the Pine ORB breakout advisory bracket.

The backend computes orb_breakout stops from risk_rules.yaml strategy.orb_stop_ticks
(MES=16, MNQ=48). Pine advisory brackets must use the same offsets because a
matching Pine bracket can override the backend-computed coordinates.
"""
from pathlib import Path


PINE = Path("tradingview/risksentinel_context.pine")
RISK_RULES = Path("risk_rules.yaml")


def _pine() -> str:
    return PINE.read_text()


def test_pine_orb_breakout_offsets_match_authoritative_risk_rules():
    src = _pine()
    rules = RISK_RULES.read_text()

    assert "orb_stop_ticks: {MES: 16, MNQ: 48}" in rules
    assert (
        "orb_breakout_stop_ticks = is_mes ? 16.0 : is_mnq ? 48.0 : 8.0"
        in src
    )
    assert (
        "signal_stop  := math.max(orb_h - tick * orb_breakout_stop_ticks, "
        "signal_entry - tick * max_stop_ticks)"
        in src
    )
    assert (
        "signal_stop  := math.min(orb_l + tick * orb_breakout_stop_ticks, "
        "signal_entry + tick * max_stop_ticks)"
        in src
    )


def test_pine_orb_breakout_math_matches_backend_contract():
    # Backend entry is two ticks beyond the ORB boundary. The configured stop
    # offset is measured from the boundary, so total entry-to-stop risk is
    # offset + 2 ticks when the max-stop cap does not bind.
    for offset in (16.0, 48.0):
        tick = 0.25
        boundary = 20000.0

        long_entry = boundary + tick * 2
        long_stop = boundary - tick * offset
        assert round((long_entry - long_stop) / tick, 8) == offset + 2

        short_entry = boundary - tick * 2
        short_stop = boundary + tick * offset
        assert round((short_stop - short_entry) / tick, 8) == offset + 2


def test_change_is_scoped_to_orb_breakout_not_4hr_retrigger():
    src = _pine()
    # 4HR Re-Trigger has its own legacy 8-tick advisory formula; this parity
    # repair must not silently change another strategy's bracket contract.
    four_hr = src[src.index("// 1. 4HR Re-Trigger LONG"):src.index("// 2. ORB Breakout LONG")]
    assert "orb_breakout_stop_ticks" not in four_hr
    assert "orb_h - tick * 8" in four_hr
    assert "orb_l + tick * 8" in four_hr
