from __future__ import annotations

from datetime import datetime, timezone
from execution import live_preflight
from execution.tradovate_broker import AUTH_HEALTHY, AuthResult


class FakeBroker:
    def __init__(self, *, positions=None, orders=None, heartbeat=None):
        self.positions = list(positions or [])
        self.orders = list(orders or [])
        self.heartbeat = heartbeat or AuthResult(AUTH_HEALTHY)

    def reliability_heartbeat(self):
        return self.heartbeat

    def _get(self, path):
        if path == "/position/list":
            return self.positions
        if path == "/order/list":
            return self.orders
        raise AssertionError(path)


def _healthy_snapshot():
    return {
        "state": "HEALTHY",
        "ready": True,
        "last_successful_heartbeat": datetime.now(timezone.utc).isoformat(),
    }


def test_preflight_passes_then_arm_allows_live_orders(monkeypatch, tmp_path):
    state_path = tmp_path / "preflight.json"
    monkeypatch.setattr(live_preflight, "reliability_snapshot", _healthy_snapshot)
    monkeypatch.setattr(live_preflight, "live_box_drift_report", lambda **_: {"ok": True, "summary": "guard ok"})

    result = live_preflight.run_preflight(FakeBroker(), state_path=state_path)

    assert result["passed"] is True
    assert result["ready"] is False
    assert result["reason"] == "preflight_passed_not_armed"

    armed = live_preflight.arm_today(state_path=state_path)

    assert armed["ready"] is True
    assert live_preflight.live_order_ready(state_path=state_path) is True


def test_preflight_failure_disarms_and_reports_failed_check(monkeypatch, tmp_path):
    state_path = tmp_path / "preflight.json"
    monkeypatch.setattr(live_preflight, "reliability_snapshot", _healthy_snapshot)
    monkeypatch.setattr(live_preflight, "live_box_drift_report", lambda **_: {"ok": True, "summary": "guard ok"})

    result = live_preflight.run_preflight(
        FakeBroker(positions=[{"netPos": 1}]),
        state_path=state_path,
    )

    assert result["passed"] is False
    assert result["ready"] is False
    assert result["reason"] == "preflight_failed:no_open_positions"
    assert live_preflight.live_order_ready(state_path=state_path) is False


def test_arm_requires_today_preflight(monkeypatch, tmp_path):
    state_path = tmp_path / "preflight.json"

    result = live_preflight.arm_today(state_path=state_path)

    assert result["ready"] is False
    assert result["reason"] == "preflight_required"


def test_working_order_blocks_preflight(monkeypatch, tmp_path):
    state_path = tmp_path / "preflight.json"
    monkeypatch.setattr(live_preflight, "reliability_snapshot", _healthy_snapshot)
    monkeypatch.setattr(live_preflight, "live_box_drift_report", lambda **_: {"ok": True, "summary": "guard ok"})

    result = live_preflight.run_preflight(
        FakeBroker(orders=[{"ordStatus": "Working"}]),
        state_path=state_path,
    )

    assert result["passed"] is False
    assert result["reason"] == "preflight_failed:no_working_orders"


def test_drift_guard_blocks_preflight(monkeypatch, tmp_path):
    state_path = tmp_path / "preflight.json"
    monkeypatch.setattr(live_preflight, "reliability_snapshot", _healthy_snapshot)
    monkeypatch.setattr(
        live_preflight,
        "live_box_drift_report",
        lambda **_: {"ok": False, "status": "error", "summary": "branch mismatch"},
    )

    result = live_preflight.run_preflight(FakeBroker(), state_path=state_path)

    assert result["passed"] is False
    assert result["reason"] == "preflight_failed:live_box_drift_guard"
    assert result["live_box_drift_guard"]["summary"] == "branch mismatch"
