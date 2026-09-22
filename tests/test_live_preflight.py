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


def test_armed_preflight_becomes_not_ready_when_runtime_drifts(monkeypatch, tmp_path):
    state_path = tmp_path / "preflight.json"
    monkeypatch.setattr(live_preflight, "reliability_snapshot", _healthy_snapshot)
    drift = {"ok": True, "summary": "guard ok"}
    monkeypatch.setattr(live_preflight, "live_box_drift_report", lambda **_: dict(drift))

    result = live_preflight.run_preflight(FakeBroker(), state_path=state_path)
    assert result["passed"] is True
    armed = live_preflight.arm_today(state_path=state_path)
    assert armed["ready"] is True
    assert live_preflight.live_order_ready(state_path=state_path) is True

    drift.update(ok=False, status="error", summary="runtime drift detected")

    assert live_preflight.live_order_ready(state_path=state_path) is False
    status = live_preflight.live_order_status(state_path=state_path)
    assert status["ready"] is False
    assert status["reason"] == "runtime_drift"
    assert status["live_box_drift_guard"]["ok"] is False


def test_live_order_ready_fails_closed_when_drift_guard_raises(monkeypatch, tmp_path):
    state_path = tmp_path / "preflight.json"
    state = live_preflight.LivePreflightState(
        date=live_preflight._today(),
        armed=True,
        last_result=True,
        disarmed_reason=None,
    )
    live_preflight.save_state(state, state_path)

    def boom(**_kwargs):
        raise RuntimeError("unexpected guard failure")

    monkeypatch.setattr(live_preflight, "live_box_drift_report", boom)

    assert live_preflight.live_order_ready(state_path=state_path) is False
    status = live_preflight.live_order_status(state_path=state_path)
    assert status["ready"] is False
    assert status["reason"] == "runtime_drift"
    assert status["live_box_drift_guard"]["status"] == "error"


def test_unarmed_state_does_not_need_runtime_drift_check(monkeypatch, tmp_path):
    state_path = tmp_path / "preflight.json"
    calls = {"count": 0}

    def counted(**_kwargs):
        calls["count"] += 1
        return {"ok": True, "summary": "guard ok"}

    monkeypatch.setattr(live_preflight, "live_box_drift_report", counted)

    assert live_preflight.live_order_ready(state_path=state_path) is False
    assert calls["count"] == 0


class RawStateBroker(FakeBroker):
    def __init__(self, *, position_payload=None, order_payload=None):
        self.position_payload = [] if position_payload is None else position_payload
        self.order_payload = [] if order_payload is None else order_payload
        self.heartbeat = AuthResult(AUTH_HEALTHY)

    def _get(self, path):
        if path == "/position/list":
            return self.position_payload
        if path == "/order/list":
            return self.order_payload
        raise AssertionError(path)


def test_preflight_blocks_non_list_position_state(monkeypatch, tmp_path):
    state_path = tmp_path / "preflight.json"
    monkeypatch.setattr(live_preflight, "reliability_snapshot", _healthy_snapshot)
    monkeypatch.setattr(
        live_preflight,
        "live_box_drift_report",
        lambda **_: {"ok": True, "summary": "guard ok"},
    )

    result = live_preflight.run_preflight(
        RawStateBroker(position_payload={"netPos": 0}),
        state_path=state_path,
    )

    assert result["passed"] is False
    assert result["reason"] == "preflight_failed:positions_readable"
    by_name = {row["name"]: row for row in result["checks"]}
    assert by_name["positions_readable"]["ok"] is False


def test_preflight_blocks_non_list_order_state(monkeypatch, tmp_path):
    state_path = tmp_path / "preflight.json"
    monkeypatch.setattr(live_preflight, "reliability_snapshot", _healthy_snapshot)
    monkeypatch.setattr(
        live_preflight,
        "live_box_drift_report",
        lambda **_: {"ok": True, "summary": "guard ok"},
    )

    result = live_preflight.run_preflight(
        RawStateBroker(order_payload={"ordStatus": "Filled"}),
        state_path=state_path,
    )

    assert result["passed"] is False
    assert result["reason"] == "preflight_failed:orders_readable"
    by_name = {row["name"]: row for row in result["checks"]}
    assert by_name["orders_readable"]["ok"] is False


def test_preflight_blocks_malformed_rows_in_broker_state(monkeypatch, tmp_path):
    state_path = tmp_path / "preflight.json"
    monkeypatch.setattr(live_preflight, "reliability_snapshot", _healthy_snapshot)
    monkeypatch.setattr(
        live_preflight,
        "live_box_drift_report",
        lambda **_: {"ok": True, "summary": "guard ok"},
    )

    result = live_preflight.run_preflight(
        RawStateBroker(position_payload=["not-an-object"]),
        state_path=state_path,
    )

    assert result["passed"] is False
    assert result["reason"] == "preflight_failed:positions_readable"


def test_preflight_blocks_position_row_without_quantity(monkeypatch, tmp_path):
    state_path = tmp_path / "preflight.json"
    monkeypatch.setattr(live_preflight, "reliability_snapshot", _healthy_snapshot)
    monkeypatch.setattr(
        live_preflight,
        "live_box_drift_report",
        lambda **_: {"ok": True, "summary": "guard ok"},
    )

    result = live_preflight.run_preflight(
        FakeBroker(positions=[{"contractId": 12345}]),
        state_path=state_path,
    )

    assert result["passed"] is False
    assert result["reason"] == "preflight_failed:positions_readable"


def test_preflight_blocks_position_row_with_unparseable_quantity(monkeypatch, tmp_path):
    state_path = tmp_path / "preflight.json"
    monkeypatch.setattr(live_preflight, "reliability_snapshot", _healthy_snapshot)
    monkeypatch.setattr(
        live_preflight,
        "live_box_drift_report",
        lambda **_: {"ok": True, "summary": "guard ok"},
    )

    result = live_preflight.run_preflight(
        FakeBroker(positions=[{"netPos": "unknown"}]),
        state_path=state_path,
    )

    assert result["passed"] is False
    assert result["reason"] == "preflight_failed:positions_readable"


def test_preflight_blocks_order_row_without_status(monkeypatch, tmp_path):
    state_path = tmp_path / "preflight.json"
    monkeypatch.setattr(live_preflight, "reliability_snapshot", _healthy_snapshot)
    monkeypatch.setattr(
        live_preflight,
        "live_box_drift_report",
        lambda **_: {"ok": True, "summary": "guard ok"},
    )

    result = live_preflight.run_preflight(
        FakeBroker(orders=[{"id": 999}]),
        state_path=state_path,
    )

    assert result["passed"] is False
    assert result["reason"] == "preflight_failed:orders_readable"


def test_preflight_blocks_unknown_nonterminal_order_status(monkeypatch, tmp_path):
    state_path = tmp_path / "preflight.json"
    monkeypatch.setattr(live_preflight, "reliability_snapshot", _healthy_snapshot)
    monkeypatch.setattr(
        live_preflight,
        "live_box_drift_report",
        lambda **_: {"ok": True, "summary": "guard ok"},
    )

    result = live_preflight.run_preflight(
        FakeBroker(orders=[{"ordStatus": "FutureBrokerStatus"}]),
        state_path=state_path,
    )

    assert result["passed"] is False
    assert result["reason"] == "preflight_failed:no_working_orders"


def test_preflight_allows_explicit_terminal_order_status(monkeypatch, tmp_path):
    state_path = tmp_path / "preflight.json"
    monkeypatch.setattr(live_preflight, "reliability_snapshot", _healthy_snapshot)
    monkeypatch.setattr(
        live_preflight,
        "live_box_drift_report",
        lambda **_: {"ok": True, "summary": "guard ok"},
    )

    result = live_preflight.run_preflight(
        FakeBroker(orders=[{"ordStatus": "Filled"}]),
        state_path=state_path,
    )

    assert result["passed"] is True


import pytest


@pytest.mark.parametrize("quantity", ["NaN", "Infinity", "-Infinity"])
def test_preflight_blocks_nonfinite_position_quantity(
    monkeypatch, tmp_path, quantity
):
    state_path = tmp_path / "preflight.json"
    monkeypatch.setattr(live_preflight, "reliability_snapshot", _healthy_snapshot)
    monkeypatch.setattr(
        live_preflight,
        "live_box_drift_report",
        lambda **_: {"ok": True, "summary": "guard ok"},
    )

    result = live_preflight.run_preflight(
        FakeBroker(positions=[{"netPos": quantity}]),
        state_path=state_path,
    )

    assert result["passed"] is False
    assert result["reason"] == "preflight_failed:positions_readable"
    by_name = {row["name"]: row for row in result["checks"]}
    assert by_name["positions_readable"]["ok"] is False
    assert "non-finite" in by_name["positions_readable"]["detail"]
