"""Pinned-account reads and a durable inter-process inverse DEMO submit latch."""
from __future__ import annotations

import json
import math
import os
import time
from pathlib import Path

from execution.live_preflight import _order_status


def assert_account_clear(broker) -> None:
    """Only positive, well-formed broker evidence permits another submission."""
    if not broker._authenticate():
        raise ValueError("inverse_demo_auth_unverified")
    reason = broker._verify_account_for_order()
    if reason:
        raise ValueError(reason)
    positions = broker._get("/position/list")
    orders = broker._get("/order/list")
    if not isinstance(positions, list) or not isinstance(orders, list):
        raise ValueError("inverse_demo_account_state_unreadable")
    for position in positions:
        if not isinstance(position, dict) or "netPos" not in position:
            raise ValueError("inverse_demo_position_state_unreadable")
        qty = float(position["netPos"])
        if not math.isfinite(qty) or qty != 0:
            raise ValueError("inverse_demo_open_position_or_unknown_quantity")
    for order in orders:
        if not isinstance(order, dict) or _order_status(order) not in {
            "filled", "cancelled", "canceled", "rejected", "expired", "completed",
        }:
            raise ValueError("inverse_demo_working_order_or_unknown_status")
    with broker._client_order_lock:
        if "AMBIGUOUS" in broker._client_order_registry.values():
            raise ValueError("inverse_demo_ambiguous_prior_submit")


def reserve_submit(log_dir, broker, client_order_id: str) -> Path:
    """Persist before sending. An orphaned latch requires broker reconciliation.

    It is deliberately outside the evidence epoch: changing epochs or restarting
    cannot erase an uncertain submission. Exclusive create serializes workers.
    """
    path = Path(log_dir) / "inverse_demo_submit_pending.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as stream:
            json.dump({"client_order_id": client_order_id}, stream)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError as exc:
        raise ValueError("inverse_demo_unresolved_prior_submit: reconcile pending latch") from exc
    try:
        assert_account_clear(broker)
    except Exception:
        path.unlink()  # no submission has occurred
        raise
    return path


def release_journaled_submit(path: Path, journal, for_date, client_order_id, order_ids) -> None:
    # JournalLogger intentionally swallows append errors. Read back ownership
    # and IDs before releasing the latch so a failed append cannot look durable.
    position = journal.get_open_position(for_date)
    if (
        not position or position.get("client_order_id") != client_order_id
        or position.get("order_ids") != order_ids
    ):
        raise ValueError("inverse_demo_journal_confirmation_missing; submit latch retained")
    with journal._journal_path(for_date).open("r") as stream:
        os.fsync(stream.fileno())
    path.unlink()


def flatten_owned_position(broker) -> dict:
    """Liquidate the recorded inverse contract; retain children until flat."""
    result = dict(cancelled_orders=False, close_sent=False, close_order_id=None,
                  close_fill_price=None, flat_confirmed=False, position_was=None)
    ids = broker._last_order_ids or {}
    position = broker._last_position
    try:
        pin = os.getenv("TRADOVATE_EXPECTED_ACCOUNT_ID", "").strip()
        if (
            broker.config.env != "demo" or os.getenv("TRADOVATE_ENV", "").strip().lower() != "demo"
            or os.getenv("LIVE_TRADING_ENABLED", "").strip().lower() != "false"
            or not pin.isascii() or not pin.isdecimal()
            or int(pin) <= 0 or broker.config.expected_account_id != int(pin)
            or not position or not ids.get("contract_id")
        ):
            raise ValueError("inverse_demo_flatten_identity_unverified")
        if not broker._authenticate() or broker._verify_account_for_order():
            raise ValueError("inverse_demo_flatten_account_unverified")

        def quantity() -> float:
            raw = broker._get("/position/list")
            if not isinstance(raw, list):
                raise ValueError("inverse_demo_flatten_positions_unreadable")
            total = 0.0
            for row in raw:
                if not isinstance(row, dict) or row.get("accountId") is None or "netPos" not in row:
                    raise ValueError("inverse_demo_flatten_position_malformed")
                qty = float(row["netPos"])
                if not math.isfinite(qty) or (qty != 0 and row.get("contractId") is None):
                    raise ValueError("inverse_demo_flatten_quantity_unreadable")
                if row["accountId"] == broker._account_id and row.get("contractId") == ids["contract_id"]:
                    total += qty
            return total

        current = quantity()
        if current != 0:
            expected_qty = position.quantity * (1 if position.direction == "LONG" else -1)
            if current != expected_qty:
                raise ValueError("inverse_demo_flatten_position_changed")
            result["position_was"] = dict(instrument=position.instrument,
                                          direction=position.direction, qty=position.quantity)
            response = broker._post("/order/liquidateposition", {
                "accountId": broker._account_id, "contractId": ids["contract_id"], "admin": False,
            })
            if not isinstance(response, dict) or any(response.get(key) for key in (
                "failureReason", "failureText", "errorText", "errorCode",
            )) or not response.get("orderId"):
                raise ValueError("inverse_demo_liquidation_rejected_or_unconfirmed")
            result["close_sent"] = True
            result["close_order_id"] = response["orderId"]
            for attempt in range(6):
                if quantity() == 0:
                    result["flat_confirmed"] = True
                    break
                if attempt < 5:
                    time.sleep(0.4)
            if not result["flat_confirmed"]:
                raise ValueError("inverse_demo_liquidation_not_confirmed_flat")
            result["close_fill_price"] = broker._entry_fill_price(response["orderId"], position.instrument)
        else:
            result["flat_confirmed"] = True
        # Only known children of this position; never account-wide cancellation.
        count = broker._cancel_oso(ids.get("target"), ids.get("stop"))
        result["cancelled_orders"] = count > 0
        result["cancelled_count"] = count
        broker._last_position = None
        broker._last_order_ids = None
    except Exception as exc:
        result["error"] = str(exc)
    return result
