"""FI-1 — ambiguous entry submit (#950 audit critical gap 1).

The fake broker APPLIES the OSO (the entry fills, a position exists), then the
response is lost or unusable. Safe requirement: ambiguous broker state is never
booked as definitively flat, and new entries stay blocked until reconciled.
"""
from __future__ import annotations

import pytest

from tests.fault_injection._harness import (
    ENTRY_ID, STOP_ID, TARGET_ID, FakeBook, FaultRecord, journal_open, journal_rows, make_broker,
    mes_payload, outcomes, patch_broker_class, real_broker_cfg, run_alert,
)

SECOND_BAR = "2026-05-23T14:45:00+00:00"

# id -> (injected failure description, how to inject it, needs limit entry)
CASES = {
    "timeout": ("placeOSO raises TimeoutError after the book filled the entry", "post_raise", False),
    "no_order_id": ("placeOSO returns a dict with no orderId", "post_no_id", False),
    "non_dict": ("placeOSO returns a non-dict body", "post_garbage", False),
    "unconfirmed": (
        "limit entry fills, but /order/item and /position/list are unreadable "
        "(ENTRY_UNCONFIRMED path)", "unreadable", True,
    ),
}


def _inject(book: FakeBook, kind: str) -> None:
    if kind == "post_raise":
        book.post_faults["/order/placeOSO"] = TimeoutError("read timed out")
    elif kind == "post_no_id":
        book.post_faults["/order/placeOSO"] = {"orderStatus": {}}
    elif kind == "post_garbage":
        book.post_faults["/order/placeOSO"] = "<html>502</html>"
    elif kind == "unreadable":
        # The entry fills, THEN reads go dark: arm the read faults only once
        # placeOSO has been applied, and return the normal OSO response.
        def _go_dark(_path):
            book.get_faults["/order/item"] = ConnectionError("order read failed")
            book.get_faults["/position/list"] = ConnectionError("position read failed")
            return {"orderId": ENTRY_ID, "oso1Id": TARGET_ID, "oso2Id": STOP_ID}
        book.post_faults["/order/placeOSO"] = _go_dark


def _submit(monkeypatch, config, tmp_path, case: str, *, children: bool):
    desc, kind, limit_entry = CASES[case]
    if limit_entry:
        monkeypatch.setenv("ENTRY_SLIPPAGE_TOLERANCE_TICKS", "2")
    book = FakeBook(place_mode="fill", children=children)
    _inject(book, kind)
    broker = make_broker(monkeypatch, book)
    cfg = real_broker_cfg(config, working_order_recheck=True)
    log_dir = tmp_path / "logs"
    first = run_alert(monkeypatch, broker, cfg, log_dir, mes_payload())
    book.require(book.place_calls() == 1, "exactly one placeOSO reached the book")
    book.require(book.net_pos == 1, "the fake broker holds the filled entry")
    rec = FaultRecord(
        case=f"FI-1 {case} (children={'working' if children else 'absent'})",
        initial_journal="flat, no open position",
        initial_broker="flat, no orders",
        injected_failure=desc,
        expected_safe_state="outcome UNKNOWN/ambiguous; instrument blocked until reconciled",
    )
    return book, broker, cfg, log_dir, first, rec


@pytest.mark.parametrize("case", list(CASES))
def test_fi1_ambiguous_submit_is_not_booked_flat(config, tmp_path, monkeypatch, case):
    book, _, _, log_dir, first, rec = _submit(monkeypatch, config, tmp_path, case, children=True)
    booked = [(o.get("result"), o.get("exit_reason")) for o in outcomes(log_dir)]
    rec.actual_state = (
        f"decision={first['decision']} journal_open={journal_open(log_dir)} "
        f"outcomes={booked} broker {book.describe()}"
    )
    rec.journal_broker_diverge = str(not journal_open(log_dir) and book.net_pos != 0)
    booked_flat = any(r == "CANCELLED" for r, _ in booked) and not journal_open(log_dir)
    assert not booked_flat, str(rec)


@pytest.mark.parametrize("case", list(CASES))
def test_fi1_next_entry_blocked_without_working_children(config, tmp_path, monkeypatch, case):
    book, broker, cfg, log_dir, _, rec = _submit(monkeypatch, config, tmp_path, case, children=False)
    rec.journal_broker_diverge = str(not journal_open(log_dir) and book.net_pos != 0)
    book.post_faults.clear()
    book.get_faults.clear()
    book.children = True  # the second order itself is healthy; only the first is unrecorded
    second = run_alert(monkeypatch, broker, cfg, log_dir, mes_payload(SECOND_BAR))
    rec.actual_state = (
        f"second decision={second['decision']} gate={second.get('gate_reason')} "
        f"broker {book.describe()}"
    )
    rec.new_entry_blocked = str(book.place_calls() == 1)
    assert book.place_calls() == 1, str(rec)


def test_fi1_next_entry_blocked_by_open_journal_after_ambiguous_submit(config, tmp_path, monkeypatch):
    """After the FI-1 fix the ambiguous submit is journaled OPEN, so the
    journal's own open-position gate stops the next entry (production path:
    BROKER=tradovate, so the open position is resolved against the broker,
    never simulated against bar OHLC)."""
    _quiet_alerts(monkeypatch)
    book, broker, cfg, log_dir, first, rec = _submit(monkeypatch, config, tmp_path, "timeout", children=True)
    book.post_faults.clear()
    monkeypatch.setenv("BROKER", "tradovate")
    patch_broker_class(monkeypatch, book)
    second = run_alert(monkeypatch, broker, cfg, log_dir, mes_payload(SECOND_BAR))
    rec.actual_state = (
        f"first={first['decision']} second={second['decision']} "
        f"journal_open={journal_open(log_dir)} broker {book.describe()}"
    )
    rec.new_entry_blocked = str(book.place_calls() == 1)
    # A lost ack is UNKNOWN. The durable submission blocks the next entry.
    # The journal is not marked flat and is not treated as a confirmed open fill.
    assert first["decision"] == "RECONCILE_REQUIRED", str(rec)
    assert second["decision"] == "BLOCKED_RECONCILE_REQUIRED", str(rec)
    assert outcomes(log_dir) == [], str(rec)
    assert book.place_calls() == 1, str(rec)


# ── FI-1 fix: how an ambiguous submit is settled ──────────────────────────────
def _quiet_alerts(monkeypatch) -> list[str]:
    sent: list[str] = []
    monkeypatch.setattr(
        "notifications.discord_notifier.send_operational_alert",
        lambda cfg, msg, *a, **k: sent.append(msg),
    )
    monkeypatch.setattr(
        "notifications.system_notifier.notify_system",
        lambda msg, *a, **k: sent.append(msg) or type("R", (), {"sent": False, "reason": "test"})(),
    )
    return sent


def _trade_rows(log_dir):
    return [r for r in journal_rows(log_dir) if r.get("decision") == "TRADE"]


def test_fi1_proven_flat_after_ambiguous_submit_books_cancelled(config, tmp_path, monkeypatch):
    """A timeout is not flat proof. Nothing is booked CANCELLED, and the
    durable submission keeps new entries blocked."""
    _quiet_alerts(monkeypatch)
    book = FakeBook(place_mode="fill", children=True)
    book.post_rejects["/order/placeOSO"] = TimeoutError("read timed out")
    broker = make_broker(monkeypatch, book)
    log_dir = tmp_path / "logs"
    result = run_alert(monkeypatch, broker, real_broker_cfg(config, working_order_recheck=True),
                       log_dir, mes_payload())
    book.require(book.net_pos == 0 and not book.orders, "fake broker applied nothing")
    assert result["decision"] == "RECONCILE_REQUIRED"
    assert result["fill"]["status"] == "UNKNOWN"
    assert outcomes(log_dir) == []
    assert _trade_rows(log_dir) == []
    assert not journal_open(log_dir)


def test_fi1_ambiguous_open_with_real_position_resolves_real_stop(config, tmp_path, monkeypatch):
    """The entry filled but the reply was lost. That is UNKNOWN, not a cancel,
    and the next bar must not invent a flat outcome or send another entry."""
    alerts = _quiet_alerts(monkeypatch)
    book = FakeBook(place_mode="fill", children=True)
    book.post_faults["/order/placeOSO"] = TimeoutError("read timed out")
    broker = make_broker(monkeypatch, book)
    monkeypatch.setenv("BROKER", "tradovate")  # after make_broker, which clears it
    patch_broker_class(monkeypatch, book)
    cfg = real_broker_cfg(config, working_order_recheck=True)
    log_dir = tmp_path / "logs"
    first = run_alert(monkeypatch, broker, cfg, log_dir, mes_payload())
    book.require(book.net_pos == 1, "fake broker holds the filled entry")
    assert first["decision"] == "RECONCILE_REQUIRED"
    assert first["fill"]["execution_state"] == "RECONCILE_REQUIRED"
    assert outcomes(log_dir) == []
    assert any("Order status unknown" in m for m in alerts)

    book.post_faults.clear()
    second = run_alert(monkeypatch, broker, cfg, log_dir, mes_payload(SECOND_BAR))
    assert second["decision"] == "BLOCKED_RECONCILE_REQUIRED"
    assert outcomes(log_dir) == []
    assert book.place_calls() == 1


def test_fi1_ambiguous_open_on_flat_broker_is_cleared_by_reconciler(config, tmp_path, monkeypatch):
    """Nothing reached the broker, but the immediate reads failed: journal OPEN
    (AMBIGUOUS_SUBMIT); once stale, the reconciler sees a definitive flat broker
    and clears it as CANCELLED."""
    from datetime import datetime, timedelta, timezone

    from webhook.reconciler import reconcile_open_position

    _quiet_alerts(monkeypatch)
    book = FakeBook(place_mode="fill", children=True)
    book.post_rejects["/order/placeOSO"] = TimeoutError("read timed out")
    book.get_faults["/position/list"] = ConnectionError("position read failed")
    broker = make_broker(monkeypatch, book)
    log_dir = tmp_path / "logs"
    first = run_alert(monkeypatch, broker, real_broker_cfg(config, working_order_recheck=False),
                      log_dir, mes_payload())
    book.require(book.net_pos == 0 and not book.orders, "fake broker applied nothing")
    assert first["decision"] == "RECONCILE_REQUIRED"
    assert outcomes(log_dir) == []
    assert not journal_open(log_dir)

    book.get_faults.clear()
    monkeypatch.setenv("BROKER", "tradovate")
    sweep = reconcile_open_position(
        config, log_dir=str(log_dir), now=datetime.now(timezone.utc) + timedelta(minutes=25), broker=broker,
    )
    assert sweep["action"] == "none"
    assert outcomes(log_dir) == []
