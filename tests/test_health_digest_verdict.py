"""Health-digest verdict: an open position with zero working orders is NAKED
and must escalate to ALERT — never a mere informational note.

The MES 2026-07-21 orphan sat open ~36h (children Day-expired, 0 working
orders) while the digest reported "OK" with "position OPEN" as a note. These
lock in the escalation ladder: 0 working → ALERT, unknown → WARN,
bracketed hold → informational note only.
"""
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "health_digest", Path(__file__).resolve().parents[1] / "scripts" / "health_digest.py"
)
health_digest = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_spec and health_digest)

evaluate_health = health_digest.evaluate_health


def _base(**over):
    checks = {
        "service_ok": True,
        "broker_reachable": True,
        "auth_state": "HEALTHY",
        "errors_today": 0,
        "disk_pct": 40.0,
        "position_flat": True,
        "working_orders": 0,
    }
    checks.update(over)
    return checks


def test_flat_is_ok_regardless_of_working_orders():
    v = evaluate_health(_base(position_flat=True, working_orders=0))
    assert v["status"] == "OK"


def test_open_position_with_zero_working_orders_is_alert():
    v = evaluate_health(_base(position_flat=False, working_orders=0))
    assert v["status"] == "ALERT"
    assert any("NAKED" in p for p in v["problems"])


def test_open_position_with_unknown_protection_is_warn():
    v = evaluate_health(_base(position_flat=False, working_orders=None))
    assert v["status"] == "WARN"
    assert any("unknown" in p for p in v["problems"])


def test_open_position_with_working_bracket_is_informational():
    v = evaluate_health(_base(position_flat=False, working_orders=2))
    assert v["status"] == "OK"
    assert any("position OPEN" in n for n in v["notes"])
    assert not v["problems"]
