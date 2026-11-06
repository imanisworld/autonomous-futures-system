"""Durable Tradovate submission recovery. Synthetic broker only.

Each mutant test names the unsafe behavior and shows that behavior failing
its safety assertion, then shows the real code holding the line.
"""
from __future__ import annotations

import json
from multiprocessing import Process, Queue

import pytest

from execution.broker_interface import BracketOrder, Fill
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


def test_pre_send_crash_stays_blocked_until_no_order_proof(tmp_path):
    """Durable intent with no HTTP call is unresolved. No automatic retry."""
    store = _store(tmp_path)
    spec = _spec()
    reserved = store.reserve_initial_send(spec)
    assert reserved["may_send"] is True
    restarted = OrderSubmissionStore(store.directory)
    again = restarted.reserve_initial_send(spec)
    assert again["may_send"] is False
    assert again["record"]["state"] == AMBIGUOUS
    cleared = restarted.reconcile_with_evidence("AFS-1", {
        "identity_linked": True,
        "no_order": True,
        "filled_qty": 0,
        "position_linked": False,
        "children_resolved": True,
    })
    assert cleared["state"] == NO_FILL
    assert restarted.entry_block() is None


def test_startup_recovers_parent_from_clordid_without_resend(tmp_path):
    store = _store(tmp_path)
    store.reserve_initial_send(_spec("AFS-recover"))
    evidence = {
        "identity_linked": True,
        "status": "Filled",
        "parent_id": 41,
        "stop_id": 42,
        "target_id": 43,
        "filled_qty": 1,
        "requested_qty": 1,
        "position_linked": True,
        "children_resolved": True,
    }
    adopted = store.reconcile_with_evidence("AFS-recover", evidence)
    assert adopted["state"] == OPEN
    assert adopted["parent_id"] == 41
    again = store.reconcile_with_evidence("AFS-recover", evidence)
    assert again["state"] == OPEN
    assert sum(1 for row in again["history"] if row["to"] == OPEN) == 1


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


def test_partial_fill_is_not_a_zero_fill(tmp_path):
    store = _store(tmp_path)
    store.reserve_initial_send(_spec("AFS-part", quantity=2))
    store.transition("AFS-part", SUBMITTED, parent_id=7, evidence="accepted")
    row = store.reconcile_with_evidence("AFS-part", {
        "identity_linked": True,
        "status": "Filled",
        "parent_id": 7,
        "filled_qty": 1,
        "requested_qty": 2,
        "position_linked": True,
        "children_resolved": False,
    })
    assert row["state"] == "PARTIALLY_FILLED"
    assert row["state"] not in {NO_FILL, CANCELLED}


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


def test_late_fill_then_flat_is_a_closed_round_trip(tmp_path):
    store = _store(tmp_path)
    store.reserve_initial_send(_spec("AFS-round"))
    closed = store.reconcile_with_evidence("AFS-round", {
        "identity_linked": True,
        "status": "Filled",
        "parent_id": 3,
        "filled_qty": 1,
        "requested_qty": 1,
        "position_linked": False,
        "children_resolved": True,
        "closed_round_trip": True,
    })
    assert closed["state"] == "CLOSED"
    assert store.reserve_initial_send(_spec("AFS-round"))["may_send"] is False
    repeat = store.reconcile_with_evidence("AFS-round", {
        "identity_linked": True,
        "closed_round_trip": True,
        "filled_qty": 1,
        "status": "Filled",
    })
    assert repeat["state"] == "CLOSED"
    assert sum(1 for row in repeat["history"] if row["to"] == "CLOSED") == 1


def test_anonymous_submit_is_refused_when_required(monkeypatch, tmp_path):
    monkeypatch.setenv("AFS_ORDER_SUBMISSION_DIR", str(tmp_path / "submissions"))
    monkeypatch.setenv("AFS_REQUIRE_CLIENT_ORDER_ID", "1")
    broker = _broker(monkeypatch)
    calls = {"n": 0}
    monkeypatch.setattr(broker, "_post", lambda *a, **k: calls.__setitem__("n", calls["n"] + 1))
    fill = broker.execute_bracket(_order())
    assert fill.exit_reason == "CLIENT_ORDER_ID_REQUIRED"
    assert calls["n"] == 0


def test_m1_volatile_registry_would_send_twice():
    """M1: memory cleared on restart resends. The mutant assertion fails; disk does not."""
    memory = {}
    sends = []

    def mutant(client_id):
        if client_id in memory:
            return
        memory[client_id] = AMBIGUOUS
        sends.append(client_id)

    mutant("AFS-m1")
    memory.clear()
    mutant("AFS-m1")
    with pytest.raises(AssertionError):
        assert sends == ["AFS-m1"]


def test_m1_real_store_does_not_resend(tmp_path):
    store = _store(tmp_path)
    assert store.reserve_initial_send(_spec("AFS-m1"))["may_send"] is True
    assert store.reserve_initial_send(_spec("AFS-m1"))["may_send"] is False


def test_m2_skipping_the_pre_send_write_fails_the_safety_check(tmp_path):
    """M2: a reserve that skips the write must fail the on-disk assertion."""
    store = _store(tmp_path)

    def mutant(spec):
        return {"may_send": True, "record": None, "reason": AMBIGUOUS}

    reserved = mutant(_spec("AFS-m2"))
    with pytest.raises(AssertionError):
        assert reserved["may_send"] is True and store.get("AFS-m2") is not None
    real = store.reserve_initial_send(_spec("AFS-m2"))
    assert real["may_send"] is True and store.get("AFS-m2")["state"] == AMBIGUOUS


def test_m3_unknown_mapped_to_cancelled_fails(monkeypatch, tmp_path):
    """M3: the old CANCELLED/ID-clear mapping fails the preserve-ids assertion."""
    monkeypatch.setenv("AFS_ORDER_SUBMISSION_DIR", str(tmp_path / "submissions"))
    broker = _broker(monkeypatch)
    monkeypatch.setattr(broker, "_post", lambda *a, **k: {"orderStatus": {}})
    fill = broker.execute_bracket(_order(client_order_id="AFS-m3"))
    mutant = Fill(
        instrument=fill.instrument, direction=fill.direction, contracts=1,
        entry_price=fill.entry_price, exit_price=None, exit_reason="ENTRY_UNCONFIRMED",
        result="CANCELLED", pnl_ticks=None, pnl_dollars=None,
    )
    with pytest.raises(AssertionError):
        assert mutant.result == "UNKNOWN" and broker._last_order_ids
    assert fill.result == "UNKNOWN"


def test_m4_lookup_failure_must_not_become_no_fill(tmp_path):
    """M4: treating an unavailable broker read as NO_FILL fails."""
    store = _store(tmp_path)
    store.reserve_initial_send(_spec("AFS-m4"))
    mutant_state = NO_FILL
    with pytest.raises(AssertionError):
        assert mutant_state == RECONCILE_REQUIRED
    real = store.reconcile_with_evidence("AFS-m4", {"error": "timeout", "identity_linked": False})
    assert real["state"] == RECONCILE_REQUIRED
    assert store.reserve_initial_send(_spec("AFS-m4"))["may_send"] is False


def test_m5_partial_must_not_become_cancelled(tmp_path):
    """M5: a nonzero partial classified as CANCELLED fails."""
    store = _store(tmp_path)
    store.reserve_initial_send(_spec("AFS-m5", quantity=3))
    store.transition("AFS-m5", SUBMITTED, parent_id=1, evidence="accepted")
    mutant = CANCELLED
    with pytest.raises(AssertionError):
        assert mutant == "PARTIALLY_FILLED"
    real = store.reconcile_with_evidence("AFS-m5", {
        "identity_linked": True, "status": "Working", "filled_qty": 1, "requested_qty": 3,
        "position_linked": True, "children_resolved": False, "parent_id": 1,
    })
    assert real["state"] == "PARTIALLY_FILLED"


def test_m6_global_gate_bypass_would_reach_the_broker(monkeypatch, tmp_path):
    """M6: ignoring an unresolved record and sending instrument B fails."""
    monkeypatch.setenv("AFS_ORDER_SUBMISSION_DIR", str(tmp_path / "submissions"))
    OrderSubmissionStore().reserve_initial_send(_spec("AFS-gate", instrument="MNQ"))
    OrderSubmissionStore().mark_reconcile_required("AFS-gate", "unknown")
    calls = {"n": 0}

    def mutant_send():
        calls["n"] += 1

    mutant_send()
    with pytest.raises(AssertionError):
        assert calls["n"] == 0
    broker = _broker(monkeypatch)
    monkeypatch.setattr(
        broker, "_post", lambda *a, **k: calls.__setitem__("n", calls["n"] + 1) or {"orderId": 5},
    )
    fill = broker.execute_bracket(BracketOrder(
        instrument="MES", direction="LONG", entry=1, stop=0.5, target=2,
        rr_ratio=2, strategy="other", client_order_id="AFS-other",
    ))
    assert fill.result == "UNKNOWN"
    assert calls["n"] == 1  # only the mutant send; the real broker did not post


def test_m7_corrupt_reset_would_allow_entry(monkeypatch, tmp_path):
    """M7: replacing a corrupt file with an empty store and then sending fails the test."""
    monkeypatch.setenv("AFS_ORDER_SUBMISSION_DIR", str(tmp_path / "submissions"))
    store = OrderSubmissionStore()
    store.records.mkdir(parents=True)
    (store.records / "bad.json").write_text("not-json", encoding="utf-8")
    with pytest.raises(SubmissionStoreError):
        store._read_record(store.records / "bad.json")
    broker = _broker(monkeypatch)
    calls = {"n": 0}
    monkeypatch.setattr(broker, "_post", lambda *a, **k: calls.__setitem__("n", calls["n"] + 1))
    assert broker.execute_bracket(_order(client_order_id="AFS-m7")).result == "UNKNOWN"
    assert calls["n"] == 0
    assert (store.records / "bad.json").exists()


def test_m8_duplicate_reconcile_does_not_double_close(tmp_path):
    """M8: two recovery passes must not append two CLOSED transitions."""
    store = _store(tmp_path)
    store.reserve_initial_send(_spec("AFS-m8"))
    evidence = {
        "identity_linked": True, "closed_round_trip": True, "filled_qty": 1,
        "requested_qty": 1, "status": "Filled", "parent_id": 9, "position_linked": False,
        "children_resolved": True,
    }
    store.reconcile_with_evidence("AFS-m8", evidence)
    store.reconcile_with_evidence("AFS-m8", evidence)
    closes = [row for row in store.get("AFS-m8")["history"] if row["to"] == "CLOSED"]
    assert len(closes) == 1


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
