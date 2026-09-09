"""resolve_via_broker must surface PaperBroker's refusal (#508), never report
an unheld position as OPEN / $0."""
from __future__ import annotations

from datetime import datetime, timedelta

from scripts.vwap_hold_evidence_package import resolve_via_broker


def _bars(t0: datetime, *hl: tuple[float, float]) -> list[dict]:
    return [{"ts": t0 + timedelta(minutes=5 * (i + 1)), "high": h, "low": lo} for i, (h, lo) in enumerate(hl)]


def test_fill_beyond_its_own_stop_is_cancelled_not_open():
    t0 = datetime(2026, 1, 5, 14, 45)
    arm = {"direction": "SHORT", "stop": 100.0, "target": 90.0}
    # SHORT filled at 110: the buy stop at 100 is already below the fill.
    res = resolve_via_broker(arm, 110.0, t0, _bars(t0, (112.0, 108.0), (111.0, 95.0)), "static")
    assert res["outcome"] == "CANCELLED"
    assert res["exit_reason"] == "ENTRY_BRACKET_INVALID_AT_FILL"
    assert res["no_fill_reason"] == "ENTRY_BRACKET_INVALID_AT_FILL"
    assert res["pnl"] == 0.0 and res["resolving_ts"] is None


def test_fill_inside_its_bracket_still_resolves_through_the_broker():
    t0 = datetime(2026, 1, 5, 14, 45)
    arm = {"direction": "SHORT", "stop": 100.0, "target": 90.0}
    res = resolve_via_broker(arm, 96.0, t0, _bars(t0, (97.0, 94.0), (95.0, 89.0)), "static")
    assert res["outcome"] == "WIN" and res["exit_reason"] == "TARGET_HIT"
    assert res["pnl"] > 0


def test_unresolved_open_position_is_still_reported_open():
    t0 = datetime(2026, 1, 5, 14, 45)
    arm = {"direction": "LONG", "stop": 90.0, "target": 110.0}
    res = resolve_via_broker(arm, 100.0, t0, _bars(t0, (101.0, 99.0)), "static")
    assert res["outcome"] == "OPEN" and res["pnl"] == 0.0
