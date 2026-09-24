"""FI-1 — ambiguous entry submit (#950 audit critical gap 1).

The fake broker APPLIES the OSO (the entry fills, a position exists), then the
response is lost or unusable. Safe requirement: ambiguous broker state is never
booked as definitively flat, and new entries stay blocked until reconciled.
"""
from __future__ import annotations

import pytest

from tests.fault_injection._harness import (
    ENTRY_ID, STOP_ID, TARGET_ID, FakeBook, FaultRecord, journal_open, make_broker, mes_payload, outcomes,
    real_broker_cfg, run_alert,
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


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="KNOWN DEFECT FI-1: ambiguous submit is booked CANCELLED / flat")
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


def test_fi1_working_order_recheck_blocks_next_entry_when_children_live(config, tmp_path, monkeypatch):
    """Partial safeguard, passes today: while the OSO children are still
    Working, the runner's working-order recheck (runner.py ~2900) suppresses
    the next entry even though the journal wrongly says flat."""
    book, broker, cfg, log_dir, _, rec = _submit(monkeypatch, config, tmp_path, "timeout", children=True)
    book.post_faults.clear()
    second = run_alert(monkeypatch, broker, cfg, log_dir, mes_payload(SECOND_BAR))
    rec.actual_state = f"second decision={second['decision']} gate={second.get('gate_reason')}"
    assert second["decision"] == "ORDER_SUPPRESSED", str(rec)
    assert "working_order_conflict" in (second.get("gate_reason") or ""), str(rec)
    assert book.place_calls() == 1, str(rec)
