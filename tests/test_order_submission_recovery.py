"""Durable Tradovate submission recovery. Synthetic broker only.

Evidence tests go through TradovateBroker.submission_evidence and the
startup reconciler. Each mutant test patches a production function, proves
the safety assertion fails, restores the function, and proves it holds.
"""
from __future__ import annotations

import asyncio
import json
from multiprocessing import Process, Queue

import pytest

from execution.broker_interface import BracketOrder
from execution.order_submission_store import (
    AMBIGUOUS,
    CANCELLED,
    NO_FILL,
    OPEN,
    RECONCILE_REQUIRED,
    SUBMITTED,
    OrderSubmissionStore,
    SubmissionIntegrityError,
    SubmissionStoreError,
    request_fingerprint,
)
from execution.tradovate_broker import TradovateBroker
from tests.test_tradovate_entry_fill import _broker


def _order(client_order_id=None):
    return BracketOrder(
        instrument="MES", direction="LONG", entry=7559.5, stop=7557.0, target=7574.5,
        rr_ratio=6.0, strategy="orb_breakout", client_order_id=client_order_id,
    )


def _spec(client_id="AFS-1", instrument="MNQ", direction="LONG", quantity=1, entry=100.0):
    return {
        "client_order_id": client_id,
        "account_env": "demo",
        "account_id": 999,
        "contract": f"{instrument}H6",
        "instrument": instrument,
        "direction": direction,
        "quantity": quantity,
        "entry": entry,
        "stop": entry - 4,
        "target": entry + 8,
    }


def _store(tmp_path):
    return OrderSubmissionStore(tmp_path / "submissions")


def _router(books):
    """Fake GET router. Unknown paths fail closed instead of inventing a flat book."""

    def _get(path, **kwargs):
        if path.startswith("/order/list"):
            return books["orders"]
        if path.startswith("/order/item"):
            wanted = str(path.split("id=", 1)[-1])
            for row in books["orders"]:
                if str(row.get("id")) == wanted:
                    return row
            raise TimeoutError(f"order item {wanted} unreadable")
        if path.startswith("/fill/list"):
            if "fills" not in books:
                raise TimeoutError("fill history unavailable")
            return books["fills"]
        if path.startswith("/execution/list"):
            if "executions" not in books:
                raise TimeoutError("execution history unavailable")
            return books["executions"]
        if path.startswith("/position/list"):
            if "positions" not in books:
                raise TimeoutError("position unavailable")
            return books["positions"]
        if path.startswith("/contract/item"):
            return {"id": 7, "name": books.get("contract", "MNQH6")}
        raise TimeoutError(path)

    return _get


def _bind(monkeypatch, broker, books):
    monkeypatch.setattr(broker, "_get", _router(books))
    return broker


def _recover(monkeypatch, tmp_path, broker):
    from webhook.reconciler import reconcile_submissions_before_entries

    return reconcile_submissions_before_entries(
        object(), str(tmp_path / "journal"), broker=broker,
    )


def test_ack_lost_after_broker_accept_is_not_retried(monkeypatch, tmp_path):
    """Server accepted, then the ack raised. A new broker must not send again."""
    monkeypatch.setenv("AFS_ORDER_SUBMISSION_DIR", str(tmp_path / "submissions"))
    calls = {"n": 0}

    def place(path, body, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise TimeoutError("ack lost after accept")
        return {"orderId": 1, "oso1Id": 2, "oso2Id": 3}

    broker = _broker(monkeypatch)
    monkeypatch.setattr(broker, "_post", place)
    first = broker.execute_bracket(_order(client_order_id="AFS-ack"))
    assert first.result == "UNKNOWN"
    restarted = _broker(monkeypatch)
    monkeypatch.setattr(restarted, "_post", place)
    TradovateBroker._reset_client_order_registry()
    second = restarted.execute_bracket(_order(client_order_id="AFS-ack"))
    assert second.result == "UNKNOWN"
    assert second.exit_reason == "SUBMIT_AMBIGUOUS_UNRECONCILED"
    assert calls["n"] == 1


def test_pre_send_crash_is_not_retried_and_empty_list_does_not_clear(monkeypatch, tmp_path):
    """Durable intent with no HTTP call stays unresolved. An empty order list is not absence."""
    monkeypatch.setenv("AFS_ORDER_SUBMISSION_DIR", str(tmp_path / "submissions"))
    monkeypatch.setenv("BROKER", "tradovate")
    store = OrderSubmissionStore()
    spec = _spec("AFS-pre")
    assert store.reserve_initial_send(spec)["may_send"] is True
    assert OrderSubmissionStore().reserve_initial_send(spec)["may_send"] is False
    broker = _bind(monkeypatch, _broker(monkeypatch), {"orders": []})
    _recover(monkeypatch, tmp_path, broker)
    assert store.get("AFS-pre")["state"] == RECONCILE_REQUIRED
    assert store.entry_block()["entries_blocked"] is True


def test_lost_ack_is_not_cleared_by_an_empty_order_list(monkeypatch, tmp_path):
    """A lost placeOSO ack plus an empty /order/list stays reconcile-required."""
    monkeypatch.setenv("AFS_ORDER_SUBMISSION_DIR", str(tmp_path / "submissions"))
    monkeypatch.setenv("BROKER", "tradovate")
    broker = _broker(monkeypatch)

    def place(path, body, **kwargs):
        raise TimeoutError("ack lost after accept")

    monkeypatch.setattr(broker, "_post", place)
    fill = broker.execute_bracket(_order(client_order_id="AFS-lost"))
    assert fill.result == "UNKNOWN"
    _bind(monkeypatch, broker, {"orders": []})
    _recover(monkeypatch, tmp_path, broker)
    row = OrderSubmissionStore().get("AFS-lost")
    assert row["state"] == RECONCILE_REQUIRED
    assert OrderSubmissionStore().entry_block()["entries_blocked"] is True


def test_startup_adopts_filled_order_from_custom_tag_and_position(monkeypatch, tmp_path):
    """No open journal row. A filled custom-tag order with a live stop becomes OPEN."""
    monkeypatch.setenv("AFS_ORDER_SUBMISSION_DIR", str(tmp_path / "submissions"))
    monkeypatch.setenv("BROKER", "tradovate")
    store = OrderSubmissionStore()
    store.reserve_initial_send(_spec("AFS-recover"))
    broker = _bind(monkeypatch, _broker(monkeypatch), {
        "orders": [
            {
                "id": 41, "customTag": "AFS-recover", "ordStatus": "Filled",
                "filledQty": 1, "orderType": "Market",
            },
            {"id": 42, "parentId": 41, "ordStatus": "Working", "orderType": "Stop"},
            {"id": 43, "parentId": 41, "ordStatus": "Working", "orderType": "Limit"},
        ],
        "fills": [{"orderId": 41, "qty": 1, "price": 100}],
        "executions": [{"orderId": 41, "qty": 1}],
        "positions": [{"netPos": 1, "netPrice": 100, "contractId": 7}],
    })
    result = _recover(monkeypatch, tmp_path, broker)
    assert result["action"] == "none"
    adopted = store.get("AFS-recover")
    assert adopted["state"] == OPEN
    assert adopted["parent_id"] == 41
    assert adopted["stop_id"] == 42
    _recover(monkeypatch, tmp_path, broker)
    again = store.get("AFS-recover")
    assert again["state"] == OPEN
    assert sum(1 for row in again["history"] if row["to"] == OPEN) == 1


def test_completed_round_trip_closes_from_broker_evidence(monkeypatch, tmp_path):
    monkeypatch.setenv("AFS_ORDER_SUBMISSION_DIR", str(tmp_path / "submissions"))
    monkeypatch.setenv("BROKER", "tradovate")
    store = OrderSubmissionStore()
    store.reserve_initial_send(_spec("AFS-round"))
    broker = _bind(monkeypatch, _broker(monkeypatch), {
        "orders": [
            {"id": 3, "clOrdId": "AFS-round", "ordStatus": "Filled", "filledQty": 1},
            {"id": 4, "parentId": 3, "ordStatus": "Filled", "orderType": "Stop", "filledQty": 1},
            {"id": 5, "parentId": 3, "ordStatus": "Canceled", "orderType": "Limit", "filledQty": 0},
        ],
        "fills": [{"orderId": 3, "qty": 1}, {"orderId": 4, "qty": 1}],
        "executions": [{"orderId": 3, "qty": 1}, {"orderId": 4, "qty": 1}],
        "positions": [],
    })
    _recover(monkeypatch, tmp_path, broker)
    assert store.get("AFS-round")["state"] == "CLOSED"
    assert store.reserve_initial_send(_spec("AFS-round"))["may_send"] is False
    _recover(monkeypatch, tmp_path, broker)
    closes = [row for row in store.get("AFS-round")["history"] if row["to"] == "CLOSED"]
    assert len(closes) == 1


def test_pre_ack_startup_clears_only_with_order_fill_execution_and_position(monkeypatch, tmp_path):
    """Startup with no journal row clears a pre-send record only after every read is empty."""
    monkeypatch.setenv("AFS_ORDER_SUBMISSION_DIR", str(tmp_path / "submissions"))
    monkeypatch.setenv("BROKER", "tradovate")
    store = OrderSubmissionStore()
    store.reserve_initial_send(_spec("AFS-proof"))
    broker = _bind(monkeypatch, _broker(monkeypatch), {
        "orders": [],
        "fills": [],
        "executions": [],
        "positions": [],
    })
    result = _recover(monkeypatch, tmp_path, broker)
    assert result["action"] == "none"
    assert store.get("AFS-proof")["state"] == NO_FILL
    assert store.entry_block() is None


def test_reconciler_queries_broker_before_the_first_sleep(monkeypatch, tmp_path):
    """The loop supplies a broker and reconciles before it sleeps."""
    from webhook import reconciler

    order = []
    sentinel = object()

    def factory():
        order.append("broker")
        return sentinel

    def recover(config, log_dir, broker=None, **kwargs):
        order.append(("reconcile", broker))
        return {"action": "none", "submissions": {"unresolved": []}}

    async def sleep(_seconds):
        order.append("sleep")
        raise asyncio.CancelledError()

    monkeypatch.setenv("BROKER", "tradovate")
    monkeypatch.setattr(reconciler, "broker_for_submission_recovery", factory)
    monkeypatch.setattr(reconciler, "reconcile_open_position", recover)
    monkeypatch.setattr(reconciler.asyncio, "sleep", sleep)
    asyncio.run(reconciler.run_reconciler_loop(object(), str(tmp_path), interval_s=30))
    assert order == ["broker", ("reconcile", sentinel), "sleep"]


def test_same_id_mismatch_fingerprint_does_not_send(tmp_path):
    store = _store(tmp_path)
    store.reserve_initial_send(_spec("AFS-fp", entry=100.0))
    other = _spec("AFS-fp", entry=200.0)
    with pytest.raises(SubmissionIntegrityError):
        store.reserve_initial_send(other)


def test_cross_process_duplicate_sends_once(tmp_path):
    directory = tmp_path / "submissions"

    def worker(queue):
        store = OrderSubmissionStore(directory)
        try:
            result = store.reserve_initial_send(_spec("AFS-race"))
            queue.put(result["may_send"])
        except Exception as exc:  # pragma: no cover - surfaced to the parent
            queue.put(repr(exc))

    queue = Queue()
    first = Process(target=worker, args=(queue,))
    second = Process(target=worker, args=(queue,))
    first.start()
    second.start()
    first.join(5)
    second.join(5)
    seen = [queue.get(timeout=2), queue.get(timeout=2)]
    assert sorted(seen) == [False, True]


def test_repeated_lookup_failure_never_clears(tmp_path):
    store = _store(tmp_path)
    store.reserve_initial_send(_spec("AFS-down"))
    store.mark_reconcile_required("AFS-down", "timeout")
    for _ in range(3):
        row = store.reconcile_with_evidence("AFS-down", {"error": "broker down", "identity_linked": False})
        assert row["state"] == RECONCILE_REQUIRED
    fresh = OrderSubmissionStore(store.directory)
    assert fresh.get("AFS-down")["state"] == RECONCILE_REQUIRED
    assert fresh.reserve_initial_send(_spec("AFS-down"))["may_send"] is False


def test_definite_rejection_clears_but_missing_id_does_not(monkeypatch, tmp_path):
    monkeypatch.setenv("AFS_ORDER_SUBMISSION_DIR", str(tmp_path / "submissions"))
    broker = _broker(monkeypatch)

    def reject(path, body, **kwargs):
        return {"failureReason": "Rejected", "failureText": "no such contract"}

    monkeypatch.setattr(broker, "_post", reject)
    fill = broker.execute_bracket(_order(client_order_id="AFS-rej"))
    assert fill.result == "CANCELLED"
    assert fill.exit_reason == "TRADOVATE_REJECTED"
    assert OrderSubmissionStore().get("AFS-rej")["state"] == "REJECTED"

    broker2 = _broker(monkeypatch)

    def missing(path, body, **kwargs):
        return {"orderStatus": {}}

    monkeypatch.setattr(broker2, "_post", missing)
    unknown = broker2.execute_bracket(_order(client_order_id="AFS-noid"))
    assert unknown.result == "UNKNOWN"
    assert OrderSubmissionStore().get("AFS-noid")["state"] == RECONCILE_REQUIRED
    TradovateBroker._reset_client_order_registry()
    calls = {"n": 0}

    def counting(path, body, **kwargs):
        calls["n"] += 1
        return {"orderId": 9}

    broker3 = _broker(monkeypatch)
    monkeypatch.setattr(broker3, "_post", counting)
    retry = broker3.execute_bracket(_order(client_order_id="AFS-noid"))
    assert retry.result == "UNKNOWN"
    assert calls["n"] == 0


def test_write_failure_before_send_sends_nothing(monkeypatch, tmp_path):
    monkeypatch.setenv("AFS_ORDER_SUBMISSION_DIR", str(tmp_path / "submissions"))
    broker = _broker(monkeypatch)
    calls = {"n": 0}

    def place(path, body, **kwargs):
        calls["n"] += 1
        return {"orderId": 1}

    monkeypatch.setattr(broker, "_post", place)

    def broken(spec):
        raise __import__(
            "execution.order_submission_store", fromlist=["SubmissionDurabilityError"]
        ).SubmissionDurabilityError("disk full")

    monkeypatch.setattr(broker._submission_store, "reserve_initial_send", broken)
    fill = broker.execute_bracket(_order(client_order_id="AFS-disk"))
    assert fill.exit_reason == "SUBMISSION_NOT_DURABLE"
    assert calls["n"] == 0


def test_corrupt_store_blocks_every_entry(monkeypatch, tmp_path):
    monkeypatch.setenv("AFS_ORDER_SUBMISSION_DIR", str(tmp_path / "submissions"))
    store = OrderSubmissionStore()
    store.records.mkdir(parents=True)
    (store.records / "bad.json").write_text("{", encoding="utf-8")
    broker = _broker(monkeypatch)
    calls = {"n": 0}
    monkeypatch.setattr(broker, "_post", lambda *a, **k: calls.__setitem__("n", calls["n"] + 1))
    fill = broker.execute_bracket(_order(client_order_id="AFS-new"))
    assert fill.result == "UNKNOWN"
    assert calls["n"] == 0
    assert (store.records / "bad.json").read_text(encoding="utf-8") == "{"


def test_unresolved_blocks_a_different_instrument(monkeypatch, tmp_path):
    monkeypatch.setenv("AFS_ORDER_SUBMISSION_DIR", str(tmp_path / "submissions"))
    store = OrderSubmissionStore()
    store.reserve_initial_send(_spec("AFS-a", instrument="MNQ"))
    store.mark_reconcile_required("AFS-a", "ack lost")
    broker = _broker(monkeypatch)
    calls = {"n": 0}
    monkeypatch.setattr(broker, "_post", lambda *a, **k: calls.__setitem__("n", calls["n"] + 1) or {"orderId": 1})
    other = BracketOrder(
        instrument="MES", direction="SHORT", entry=5000, stop=5010, target=4980,
        rr_ratio=2, strategy="other", client_order_id="AFS-b",
    )
    fill = broker.execute_bracket(other)
    assert fill.result == "UNKNOWN"
    assert calls["n"] == 0


def test_notifier_failure_keeps_the_block(monkeypatch, tmp_path):
    from webhook import runner

    monkeypatch.setenv("AFS_ORDER_SUBMISSION_DIR", str(tmp_path / "submissions"))
    store = OrderSubmissionStore()
    store.reserve_initial_send(_spec("AFS-alert"))
    store.mark_reconcile_required("AFS-alert", "lost")

    def boom(*args, **kwargs):
        raise RuntimeError("discord down")

    monkeypatch.setattr("notifications.discord_notifier.send_operational_alert", boom)
    runner._send_reconcile_required_alert(object(), store.entry_block())
    assert store.get("AFS-alert")["state"] == RECONCILE_REQUIRED
    assert store.entry_block()["entries_blocked"] is True


def test_partial_fill_then_cancel_adopts_the_filled_quantity(monkeypatch, tmp_path):
    """A canceled remainder with a live stop is not a zero-fill cancel."""
    monkeypatch.setenv("AFS_ORDER_SUBMISSION_DIR", str(tmp_path / "submissions"))
    monkeypatch.setenv("BROKER", "tradovate")
    store = OrderSubmissionStore()
    store.reserve_initial_send(_spec("AFS-part", quantity=2))
    broker = _bind(monkeypatch, _broker(monkeypatch), {
        "orders": [
            {
                "id": 7, "clOrdId": "AFS-part", "ordStatus": "Canceled",
                "filledQty": 1, "orderQty": 2, "orderType": "Market",
            },
            {"id": 8, "parentId": 7, "ordStatus": "Working", "orderType": "Stop"},
        ],
        "fills": [{"orderId": 7, "qty": 1, "price": 100}],
        "executions": [{"orderId": 7, "qty": 1}],
        "positions": [{"netPos": 1, "netPrice": 100, "contractId": 7}],
    })
    _recover(monkeypatch, tmp_path, broker)
    row = store.get("AFS-part")
    assert row["state"] == OPEN
    assert row["state"] not in {NO_FILL, CANCELLED}
    assert row["fill_evidence"]["filled_qty"] == 1


def test_unrelated_position_does_not_adopt(tmp_path):
    store = _store(tmp_path)
    store.reserve_initial_send(_spec("AFS-stranger"))
    row = store.reconcile_with_evidence("AFS-stranger", {
        "identity_linked": False,
        "status": "Filled",
        "filled_qty": 1,
        "position_linked": True,
    })
    assert row["state"] == RECONCILE_REQUIRED


def test_unrelated_root_does_not_adopt_or_clear_when_identity_is_missing(monkeypatch, tmp_path):
    """Same-root position without an order/fill/execution link stays unresolved."""
    monkeypatch.setenv("AFS_ORDER_SUBMISSION_DIR", str(tmp_path / "submissions"))
    monkeypatch.setenv("BROKER", "tradovate")
    store = OrderSubmissionStore()
    store.reserve_initial_send(_spec("AFS-stranger"))
    broker = _bind(monkeypatch, _broker(monkeypatch), {
        "orders": [],
        "fills": [],
        "executions": [],
        "positions": [{"netPos": 1, "netPrice": 100, "contractId": 7}],
    })
    _recover(monkeypatch, tmp_path, broker)
    assert store.get("AFS-stranger")["state"] == RECONCILE_REQUIRED


def test_anonymous_submit_is_refused_when_required(monkeypatch, tmp_path):
    monkeypatch.setenv("AFS_ORDER_SUBMISSION_DIR", str(tmp_path / "submissions"))
    monkeypatch.setenv("AFS_REQUIRE_CLIENT_ORDER_ID", "1")
    broker = _broker(monkeypatch)
    calls = {"n": 0}
    monkeypatch.setattr(broker, "_post", lambda *a, **k: calls.__setitem__("n", calls["n"] + 1))
    fill = broker.execute_bracket(_order())
    assert fill.exit_reason == "CLIENT_ORDER_ID_REQUIRED"
    assert calls["n"] == 0


def _working_book(monkeypatch, broker):
    _bind(monkeypatch, broker, {
        "orders": [],
        "fills": [],
        "executions": [],
        "positions": [],
    })

    def _get(path, **kwargs):
        if path.startswith("/order/item"):
            return {"id": path, "ordStatus": "Working", "orderType": "Stop"}
        if path.startswith("/order/list"):
            return []
        if path.startswith("/position/list") or path.startswith("/fill/list") or path.startswith("/execution/list"):
            return []
        if path.startswith("/contract/item"):
            return {"name": "MESH6"}
        raise TimeoutError(path)

    monkeypatch.setattr(broker, "_get", _get)


def _accepted(path, body, **kwargs):
    return {"orderId": 11, "oso1Id": 12, "oso2Id": 13}


def test_m1_volatile_registry_sends_twice(monkeypatch, tmp_path):
    """M1: a memory-only reserve resends after restart. posts == 1 fails on that mutant."""
    monkeypatch.setenv("AFS_ORDER_SUBMISSION_DIR", str(tmp_path / "submissions"))
    memory = {}
    real_reserve = OrderSubmissionStore.reserve_initial_send

    def volatile(self, spec):
        client_id = spec["client_order_id"]
        if client_id in memory:
            return {"may_send": False, "record": {"state": AMBIGUOUS}, "reason": AMBIGUOUS}
        memory[client_id] = True
        return {"may_send": True, "record": {"state": AMBIGUOUS}, "reason": AMBIGUOUS}

    posts = {"n": 0}

    def place(path, body, **kwargs):
        if str(path).startswith("/order/placeOSO"):
            posts["n"] += 1
        return _accepted(path, body)

    monkeypatch.setattr(OrderSubmissionStore, "reserve_initial_send", volatile)
    broker = _broker(monkeypatch)
    _working_book(monkeypatch, broker)
    monkeypatch.setattr(broker, "_post", place)
    broker.execute_bracket(_order(client_order_id="AFS-m1"))
    memory.clear()
    TradovateBroker._reset_client_order_registry()
    broker.execute_bracket(_order(client_order_id="AFS-m1"))
    with pytest.raises(AssertionError):
        assert posts["n"] == 1

    monkeypatch.setattr(OrderSubmissionStore, "reserve_initial_send", real_reserve)
    posts["n"] = 0
    real = _broker(monkeypatch)
    _working_book(monkeypatch, real)
    monkeypatch.setattr(real, "_post", place)
    TradovateBroker._reset_client_order_registry()
    real.execute_bracket(_order(client_order_id="AFS-m1-real"))
    TradovateBroker._reset_client_order_registry()
    real.execute_bracket(_order(client_order_id="AFS-m1-real"))
    assert posts["n"] == 1


def test_m2_skipping_the_pre_send_write_sends_without_a_record(monkeypatch, tmp_path):
    """M2: a no-op pre-send write lets placeOSO happen with nothing on disk."""
    monkeypatch.setenv("AFS_ORDER_SUBMISSION_DIR", str(tmp_path / "submissions"))
    real_write = OrderSubmissionStore._write_record
    seen = {}

    def skip_write(self, record):
        return None

    def place(path, body, **kwargs):
        if str(path).startswith("/order/placeOSO"):
            seen["durable"] = OrderSubmissionStore().get(body.get("clOrdId")) is not None
        return _accepted(path, body)

    monkeypatch.setattr(OrderSubmissionStore, "_write_record", skip_write)
    broker = _broker(monkeypatch)
    _working_book(monkeypatch, broker)
    monkeypatch.setattr(broker, "_post", place)
    broker.execute_bracket(_order(client_order_id="AFS-m2"))
    with pytest.raises(AssertionError):
        assert seen["durable"] is True

    monkeypatch.setattr(OrderSubmissionStore, "_write_record", real_write)
    seen.clear()
    real = _broker(monkeypatch)
    _working_book(monkeypatch, real)
    monkeypatch.setattr(real, "_post", place)
    real.execute_bracket(_order(client_order_id="AFS-m2-real"))
    assert seen["durable"] is True


def test_m3_unknown_mapped_to_cancelled_clears_ids(monkeypatch, tmp_path):
    """M3: mapping an unreadable entry to CANCELLED and dropping ids fails the preserve check."""
    monkeypatch.setenv("AFS_ORDER_SUBMISSION_DIR", str(tmp_path / "submissions"))
    monkeypatch.setenv("ENTRY_SLIPPAGE_TOLERANCE_TICKS", "2")
    real_unknown = TradovateBroker._unknown_fill

    def cancelled(self, order, reason, order_type=None, order_ids=None):
        self._last_order_ids = None
        self._last_position = None
        return self._cancelled_fill(order, "ENTRY_UNCONFIRMED")

    def drive(broker, client_id):
        monkeypatch.setattr(broker, "_post", _accepted)
        monkeypatch.setattr(broker, "_entry_status", lambda *args, **kwargs: "unknown")
        monkeypatch.setattr(broker, "_confirm_unknown_entry", lambda *args, **kwargs: "unknown")
        return broker.execute_bracket(_order(client_order_id=client_id))

    monkeypatch.setattr(TradovateBroker, "_unknown_fill", cancelled)
    broker = _broker(monkeypatch)
    fill = drive(broker, "AFS-m3")
    with pytest.raises(AssertionError):
        assert fill.result == "UNKNOWN" and broker._last_order_ids

    monkeypatch.setattr(TradovateBroker, "_unknown_fill", real_unknown)
    TradovateBroker._reset_client_order_registry()
    monkeypatch.setenv("AFS_ORDER_SUBMISSION_DIR", str(tmp_path / "m3-real"))
    real = _broker(monkeypatch)
    kept = drive(real, "AFS-m3-real")
    assert kept.result == "UNKNOWN"
    assert real._last_order_ids["entry"] == 11


def test_m4_lookup_failure_must_not_become_no_fill(monkeypatch, tmp_path):
    """M4: classifying an unreadable broker book as absence fails the reconcile-required check."""
    monkeypatch.setenv("AFS_ORDER_SUBMISSION_DIR", str(tmp_path / "submissions"))
    store = OrderSubmissionStore()
    store.reserve_initial_send(_spec("AFS-m4"))
    real_evidence = TradovateBroker.submission_evidence

    def pretend_absent(self, record):
        return {
            "identity_linked": True,
            "absence_proven": True,
            "order_identity_checked": True,
            "order_found": False,
            "no_order": True,
            "filled_qty": 0,
            "position_linked": False,
            "position_checked": True,
            "fills_checked": True,
            "executions_checked": True,
            "children_resolved": True,
            "status": "expired",
        }

    monkeypatch.setattr(TradovateBroker, "submission_evidence", pretend_absent)
    broker = _broker(monkeypatch)
    monkeypatch.setattr(broker, "_get", lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError("down")))
    cleared = store.reconcile_with_evidence(
        "AFS-m4", broker.submission_evidence(store.get("AFS-m4")),
    )
    with pytest.raises(AssertionError):
        assert cleared["state"] == RECONCILE_REQUIRED

    monkeypatch.setattr(TradovateBroker, "submission_evidence", real_evidence)
    store.reserve_initial_send(_spec("AFS-m4-real"))
    real = store.reconcile_with_evidence(
        "AFS-m4-real", broker.submission_evidence(store.get("AFS-m4-real")),
    )
    assert real["state"] == RECONCILE_REQUIRED
    assert store.reserve_initial_send(_spec("AFS-m4-real"))["may_send"] is False


def test_m5_partial_must_not_become_cancelled(monkeypatch, tmp_path):
    """M5: classifying a nonzero partial as CANCELLED fails. Production evidence does not."""
    monkeypatch.setenv("AFS_ORDER_SUBMISSION_DIR", str(tmp_path / "submissions"))
    monkeypatch.setenv("BROKER", "tradovate")
    real_reconcile = OrderSubmissionStore.reconcile_with_evidence

    def as_cancel(self, client_id, evidence):
        if (evidence or {}).get("filled_qty") not in (None, 0):
            if self.get(client_id)["state"] == AMBIGUOUS:
                self.transition(client_id, SUBMITTED, evidence="mutant")
            return self.transition(client_id, CANCELLED, evidence="mutant_partial")
        return real_reconcile(self, client_id, evidence)

    def books():
        return {
            "orders": [
                {
                    "id": 7, "clOrdId": "AFS-m5", "ordStatus": "Canceled",
                    "filledQty": 1, "orderType": "Market",
                },
                {"id": 8, "parentId": 7, "ordStatus": "Working", "orderType": "Stop"},
            ],
            "fills": [{"orderId": 7, "qty": 1}],
            "executions": [{"orderId": 7, "qty": 1}],
            "positions": [{"netPos": 1, "netPrice": 100, "contractId": 7}],
        }

    monkeypatch.setattr(OrderSubmissionStore, "reconcile_with_evidence", as_cancel)
    store = OrderSubmissionStore()
    store.reserve_initial_send(_spec("AFS-m5", quantity=2))
    broker = _bind(monkeypatch, _broker(monkeypatch), books())
    _recover(monkeypatch, tmp_path, broker)
    with pytest.raises(AssertionError):
        assert store.get("AFS-m5")["state"] != CANCELLED

    monkeypatch.setattr(OrderSubmissionStore, "reconcile_with_evidence", real_reconcile)
    fresh = OrderSubmissionStore()
    fresh.reserve_initial_send(_spec("AFS-m5-real", quantity=2))
    real_broker = _bind(monkeypatch, _broker(monkeypatch), {
        "orders": [
            {
                "id": 17, "clOrdId": "AFS-m5-real", "ordStatus": "Canceled",
                "filledQty": 1, "orderType": "Market",
            },
            {"id": 18, "parentId": 17, "ordStatus": "Working", "orderType": "Stop"},
        ],
        "fills": [{"orderId": 17, "qty": 1}],
        "executions": [{"orderId": 17, "qty": 1}],
        "positions": [{"netPos": 1, "netPrice": 100, "contractId": 7}],
    })
    _recover(monkeypatch, tmp_path, real_broker)
    assert fresh.get("AFS-m5-real")["state"] not in {CANCELLED, NO_FILL}


def test_m6_global_gate_bypass_reaches_the_broker(monkeypatch, tmp_path):
    """M6: skipping the unresolved-submission guard lets a different instrument send."""
    monkeypatch.setenv("AFS_ORDER_SUBMISSION_DIR", str(tmp_path / "submissions"))
    OrderSubmissionStore().reserve_initial_send(_spec("AFS-gate", instrument="MNQ"))
    OrderSubmissionStore().mark_reconcile_required("AFS-gate", "unknown")
    real_guard = TradovateBroker._guard_submission
    posts = {"n": 0}

    def open_gate(self, order, client_id, contract_symbol):
        return None

    def place(path, body, **kwargs):
        if str(path).startswith("/order/placeOSO"):
            posts["n"] += 1
        return _accepted(path, body)

    other = BracketOrder(
        instrument="MES", direction="LONG", entry=5000, stop=4990, target=5020,
        rr_ratio=2, strategy="other", client_order_id="AFS-other",
    )
    monkeypatch.setattr(TradovateBroker, "_guard_submission", open_gate)
    broker = _broker(monkeypatch)
    _working_book(monkeypatch, broker)
    monkeypatch.setattr(broker, "_post", place)
    broker.execute_bracket(other)
    with pytest.raises(AssertionError):
        assert posts["n"] == 0

    monkeypatch.setattr(TradovateBroker, "_guard_submission", real_guard)
    posts["n"] = 0
    blocked = _broker(monkeypatch)
    _working_book(monkeypatch, blocked)
    monkeypatch.setattr(blocked, "_post", place)
    fill = blocked.execute_bracket(other)
    assert fill.result == "UNKNOWN"
    assert posts["n"] == 0


def test_m7_corrupt_reset_allows_entry(monkeypatch, tmp_path):
    """M7: treating a corrupt store as an empty healthy registry lets placeOSO run."""
    monkeypatch.setenv("AFS_ORDER_SUBMISSION_DIR", str(tmp_path / "submissions"))
    store = OrderSubmissionStore()
    store.records.mkdir(parents=True)
    (store.records / "bad.json").write_text("not-json", encoding="utf-8")
    real_block = OrderSubmissionStore.entry_block
    posts = {"n": 0}

    def ignore_corrupt(self):
        self._load_all(strict=False)
        return None

    def place(path, body, **kwargs):
        if str(path).startswith("/order/placeOSO"):
            posts["n"] += 1
        return _accepted(path, body)

    monkeypatch.setattr(OrderSubmissionStore, "entry_block", ignore_corrupt)
    broker = _broker(monkeypatch)
    _working_book(monkeypatch, broker)
    monkeypatch.setattr(broker, "_post", place)
    broker.execute_bracket(_order(client_order_id="AFS-m7"))
    with pytest.raises(AssertionError):
        assert posts["n"] == 0
    assert (store.records / "bad.json").exists()

    monkeypatch.setattr(OrderSubmissionStore, "entry_block", real_block)
    posts["n"] = 0
    blocked = _broker(monkeypatch)
    _working_book(monkeypatch, blocked)
    monkeypatch.setattr(blocked, "_post", place)
    assert blocked.execute_bracket(_order(client_order_id="AFS-m7-real")).result == "UNKNOWN"
    assert posts["n"] == 0


def test_m8_duplicate_reconcile_appends_a_second_close(monkeypatch, tmp_path):
    """M8: a recovery mutant that writes CLOSED twice fails the single-outcome check."""
    monkeypatch.setenv("AFS_ORDER_SUBMISSION_DIR", str(tmp_path / "submissions"))
    monkeypatch.setenv("BROKER", "tradovate")
    real_reconcile = OrderSubmissionStore.reconcile_with_evidence

    def duplicate(self, client_id, evidence):
        row = real_reconcile(self, client_id, evidence)
        if row.get("state") == "CLOSED":
            current = self.get(client_id)
            current["history"].append(
                {"ts": current["updated_at"], "from": "CLOSED", "to": "CLOSED", "evidence": "duplicate"}
            )
            self._write_record(current)
            return self.get(client_id)
        return row

    books = {
        "orders": [
            {"id": 9, "clOrdId": "AFS-m8", "ordStatus": "Filled", "filledQty": 1},
            {"id": 10, "parentId": 9, "ordStatus": "Filled", "orderType": "Stop", "filledQty": 1},
        ],
        "fills": [{"orderId": 9, "qty": 1}, {"orderId": 10, "qty": 1}],
        "executions": [{"orderId": 9, "qty": 1}, {"orderId": 10, "qty": 1}],
        "positions": [],
    }
    monkeypatch.setattr(OrderSubmissionStore, "reconcile_with_evidence", duplicate)
    store = OrderSubmissionStore()
    store.reserve_initial_send(_spec("AFS-m8"))
    _recover(monkeypatch, tmp_path, _bind(monkeypatch, _broker(monkeypatch), books))
    with pytest.raises(AssertionError):
        assert len([row for row in store.get("AFS-m8")["history"] if row["to"] == "CLOSED"]) == 1

    monkeypatch.setattr(OrderSubmissionStore, "reconcile_with_evidence", real_reconcile)
    fresh = OrderSubmissionStore()
    fresh.reserve_initial_send(_spec("AFS-m8-real"))
    real_books = {
        "orders": [
            {"id": 19, "clOrdId": "AFS-m8-real", "ordStatus": "Filled", "filledQty": 1},
            {"id": 20, "parentId": 19, "ordStatus": "Filled", "orderType": "Stop", "filledQty": 1},
        ],
        "fills": [{"orderId": 19, "qty": 1}, {"orderId": 20, "qty": 1}],
        "executions": [{"orderId": 19, "qty": 1}, {"orderId": 20, "qty": 1}],
        "positions": [],
    }
    broker = _bind(monkeypatch, _broker(monkeypatch), real_books)
    _recover(monkeypatch, tmp_path, broker)
    _recover(monkeypatch, tmp_path, broker)
    assert len([row for row in fresh.get("AFS-m8-real")["history"] if row["to"] == "CLOSED"]) == 1


def test_fingerprint_is_stable():
    assert request_fingerprint(_spec()) == request_fingerprint(_spec())


def test_public_status_has_no_raw_payload(tmp_path):
    store = _store(tmp_path)
    store.reserve_initial_send(_spec("AFS-view"))
    view = store.public_status()
    blob = json.dumps(view)
    assert "password" not in blob
    assert view["entries_blocked"] is True
    assert view["unresolved"][0]["client_order_id"] == "AFS-view"
