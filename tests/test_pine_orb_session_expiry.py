"""Source-level regression locks for Pine ORB expiry semantics.

TradingView Pine is not compiled in CI, so these checks pin the small safety
contract that prevents a prior-session ORB from remaining in later alert
payloads. Runtime/replay already fail closed outside NY/London (#464); the Pine
source must expire the same ranges at the canonical session boundaries.
"""
from pathlib import Path


PINE = Path("tradingview/risksentinel_context.pine")


def _source() -> str:
    return PINE.read_text()


def _transition_expires(previously_active: bool, active_now: bool) -> bool:
    """Python model of Pine's `not active and active[1]` transition."""
    return previously_active and not active_now


def test_pine_expires_ny_orb_at_canonical_runtime_session_end():
    src = _source()
    assert 'ny_runtime_active = not na(time(timeframe.period, "0930-1700", "America/New_York"))' in src
    reset = """if not ny_runtime_active and ny_runtime_active[1]
    orb_h := na
    orb_l := na
    orb_cnt := 0
    orb_done := false"""
    assert reset in src
    assert src.index(reset) < src.index("orb_status = orb_status_for(orb_h, orb_l)")

    # ORB remains usable through the NY runtime session, then expires before
    # the next observed post-session bar (including the 18:00 CME reopen).
    assert not _transition_expires(True, True)
    assert _transition_expires(True, False)


def test_pine_expires_london_orb_at_canonical_runtime_session_end():
    src = _source()
    assert 'london_runtime_active = not na(time(timeframe.period, "0300-0930", "America/New_York"))' in src
    reset = """if not london_runtime_active and london_runtime_active[1]
    london_orb_h := na
    london_orb_l := na
    london_orb_cnt := 0
    london_orb_done := false"""
    assert reset in src
    assert src.index(reset) < src.index("london_orb_status = orb_status_for(london_orb_h, london_orb_l)")

    assert not _transition_expires(True, True)
    assert _transition_expires(True, False)


def test_alert_payload_uses_expiring_orb_state_directly():
    src = _source()
    # The alert keeps publishing the canonical state variables. Once the
    # transition above clears them, f2j emits null rather than yesterday's range.
    assert '"\\\"orb_high\\\":"           + f2j(orb_h,' in src
    assert '"\\\"orb_low\\\":"            + f2j(orb_l,' in src
    assert '"\\\"london_orb_high\\\":"    + f2j(london_orb_h,' in src
    assert '"\\\"london_orb_low\\\":"     + f2j(london_orb_l,' in src
