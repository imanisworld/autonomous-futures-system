"""Daily live-order preflight and arming gate.

This module is intentionally conservative: live Tradovate orders are allowed
only after today's preflight passed and the operator explicitly armed the bot.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional

from execution.tradovate_supervisor import HEARTBEAT_FRESH_SECONDS, reliability_snapshot
from ops.live_box_guard import live_box_drift_report


DEFAULT_STATE_PATH = Path("logs/live_preflight_state.json")
WORKING_ORDER_STATUSES = {
    "accepted",
    "pending",
    "pendingnew",
    "partiallyfilled",
    "suspended",
    "working",
}


@dataclass(frozen=True)
class PreflightCheck:
    name: str
    ok: bool
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "ok": self.ok, "detail": self.detail}


@dataclass
class LivePreflightState:
    date: str
    armed: bool = False
    armed_at: Optional[str] = None
    armed_by: Optional[str] = None
    disarmed_reason: Optional[str] = "not_armed"
    last_preflight_at: Optional[str] = None
    last_result: Optional[bool] = None
    checks: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "date": self.date,
            "armed": self.armed,
            "armed_at": self.armed_at,
            "armed_by": self.armed_by,
            "disarmed_reason": self.disarmed_reason,
            "last_preflight_at": self.last_preflight_at,
            "last_result": self.last_result,
            "checks": self.checks,
            "ready": self.ready,
            "reason": self.reason,
        }

    @property
    def ready(self) -> bool:
        return self.armed and self.last_result is True and self.date == _today()

    @property
    def reason(self) -> str:
        if self.date != _today():
            return "date_rollover"
        if not self.armed:
            return self.disarmed_reason or "not_armed"
        if self.last_result is not True:
            return "preflight_not_passed"
        return "ready"


def _today() -> str:
    return date.today().isoformat()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _state_path(path: str | Path | None = None) -> Path:
    return Path(path) if path is not None else DEFAULT_STATE_PATH


def _default_state() -> LivePreflightState:
    return LivePreflightState(date=_today())


def load_state(path: str | Path | None = None) -> LivePreflightState:
    state_path = _state_path(path)
    if not state_path.exists():
        return _default_state()
    try:
        raw = json.loads(state_path.read_text())
        state = LivePreflightState(
            date=str(raw.get("date") or _today()),
            armed=bool(raw.get("armed", False)),
            armed_at=raw.get("armed_at"),
            armed_by=raw.get("armed_by"),
            disarmed_reason=raw.get("disarmed_reason"),
            last_preflight_at=raw.get("last_preflight_at"),
            last_result=raw.get("last_result"),
            checks=list(raw.get("checks") or []),
        )
    except Exception:
        return _default_state()
    if state.date != _today():
        state.armed = False
        state.armed_at = None
        state.armed_by = None
        state.disarmed_reason = "date_rollover"
    return state


def save_state(state: LivePreflightState, path: str | Path | None = None) -> None:
    state_path = _state_path(path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{state_path.name}.", dir=state_path.parent)
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(state.as_dict(), fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp_name, state_path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def _check(name: str, ok: bool, detail: str = "") -> PreflightCheck:
    return PreflightCheck(name=name, ok=bool(ok), detail=detail)


def _drift_report() -> dict[str, Any]:
    return live_box_drift_report(
        risk_rules_path=os.getenv("RISK_RULES_PATH", "risk_rules.yaml"),
        log_dir=os.getenv("LOG_DIR", "logs"),
    )


def _parse_iso(value: object) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except (TypeError, ValueError):
        return None


def _order_status(order: dict) -> str:
    return str(
        order.get("ordStatus")
        or order.get("status")
        or order.get("orderStatus")
        or ""
    ).replace("_", "").replace(" ", "").lower()


def _position_qty(position: dict) -> float:
    for key in ("netPos", "netPosition", "qty", "quantity"):
        value = position.get(key)
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return 0.0


def _list_positions(broker) -> list[dict]:
    result = broker._get("/position/list")
    return result if isinstance(result, list) else []


def _list_orders(broker) -> list[dict]:
    result = broker._get("/order/list")
    return result if isinstance(result, list) else []


def run_preflight(
    broker,
    *,
    state_path: str | Path | None = None,
    notify=None,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Run daily preflight and persist the result.

    `broker` is expected to be a TradovateBroker-compatible object. The function
    never places orders; it only reads reliability/account/position/order state.
    """
    state = load_state(state_path)
    checked_at = _now_iso()
    checks: list[PreflightCheck] = []
    snap = reliability_snapshot()
    heartbeat_at = _parse_iso(snap.get("last_successful_heartbeat"))
    current = now or datetime.now(timezone.utc)
    heartbeat_fresh = bool(
        heartbeat_at is not None
        and (current - heartbeat_at).total_seconds() <= HEARTBEAT_FRESH_SECONDS
    )
    checks.append(_check("tradovate_reliability_healthy", snap.get("state") == "HEALTHY", str(snap.get("state"))))
    checks.append(_check("heartbeat_fresh", heartbeat_fresh, str(snap.get("last_successful_heartbeat"))))

    positions: list[dict] = []
    orders: list[dict] = []
    try:
        auth = broker.reliability_heartbeat()
        checks.append(_check("account_readable", getattr(auth, "ok", False), getattr(auth, "detail", "") or getattr(auth, "status", "")))
    except Exception as exc:
        checks.append(_check("account_readable", False, str(exc)))

    try:
        positions = _list_positions(broker)
        checks.append(_check("positions_readable", True, f"{len(positions)} position row(s)"))
    except Exception as exc:
        checks.append(_check("positions_readable", False, str(exc)))

    try:
        orders = _list_orders(broker)
        checks.append(_check("orders_readable", True, f"{len(orders)} order row(s)"))
    except Exception as exc:
        checks.append(_check("orders_readable", False, str(exc)))

    open_positions = [p for p in positions if abs(_position_qty(p)) > 0]
    working_orders = [o for o in orders if _order_status(o) in WORKING_ORDER_STATUSES]
    checks.append(_check("no_open_positions", not open_positions, f"{len(open_positions)} open position(s)"))
    checks.append(_check("no_working_orders", not working_orders, f"{len(working_orders)} working order(s)"))
    drift = _drift_report()
    checks.append(_check("live_box_drift_guard", drift.get("ok") is True, drift.get("summary", "")))

    passed = all(check.ok for check in checks)
    state.date = _today()
    state.last_preflight_at = checked_at
    state.last_result = passed
    state.checks = [check.as_dict() for check in checks]
    if not passed:
        state.armed = False
        state.armed_at = None
        state.armed_by = None
        failed = next((check for check in checks if not check.ok), None)
        state.disarmed_reason = f"preflight_failed:{failed.name if failed else 'unknown'}"
    elif not state.armed:
        state.disarmed_reason = "preflight_passed_not_armed"
    save_state(state, state_path)

    payload = state.as_dict()
    payload["passed"] = passed
    payload["live_box_drift_guard"] = drift
    if notify and not passed:
        notify(f"LIVE PREFLIGHT FAILED: {state.disarmed_reason}. Live orders remain blocked.")
    return payload


def arm_today(*, state_path: str | Path | None = None, notify=None, armed_by: str = "manual") -> dict[str, Any]:
    state = load_state(state_path)
    if state.date != _today() or state.last_result is not True:
        state.armed = False
        state.disarmed_reason = "preflight_required"
        save_state(state, state_path)
        return state.as_dict()
    state.armed = True
    state.armed_at = _now_iso()
    state.armed_by = armed_by
    state.disarmed_reason = None
    save_state(state, state_path)
    if notify:
        notify("LIVE ARMED FOR TODAY: Tradovate preflight passed. Live orders may route while broker health stays green.")
    return state.as_dict()


def disarm(*, reason: str = "manual", state_path: str | Path | None = None, notify=None) -> dict[str, Any]:
    state = load_state(state_path)
    state.armed = False
    state.armed_at = None
    state.armed_by = None
    state.disarmed_reason = reason
    save_state(state, state_path)
    if notify:
        notify(f"LIVE DISARMED: {reason}. Live orders are blocked.")
    return state.as_dict()


def live_order_ready(*, state_path: str | Path | None = None) -> bool:
    return load_state(state_path).ready


def live_order_status(*, state_path: str | Path | None = None) -> dict[str, Any]:
    payload = load_state(state_path).as_dict()
    payload["live_box_drift_guard"] = _drift_report()
    return payload
