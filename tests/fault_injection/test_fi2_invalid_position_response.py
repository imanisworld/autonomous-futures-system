"""FI-2 — invalid position response in resolve_position (#950 audit critical gap 2).

The fake broker still HOLDS the position; only the /position/list response is
malformed. Safe requirement: unknown broker position state stays UNKNOWN (the
position stays open); it is never converted to flat, and never booked as a
FORCE_CLOSE_UNMATCHED outcome, solely because the response was malformed.
"""
from __future__ import annotations

import pytest

from execution.broker_interface import Position
from tests.fault_injection._harness import (
    ENTRY_ID, STOP_ID, TARGET_ID, FakeBook, FaultRecord, journal_open, make_broker,
    mes_payload, outcomes, patch_broker_class, real_broker_cfg, run_alert,
)

MALFORMED = {
    "dict": {"netPos": 1},
    "none": None,
    "string": "<html>503</html>",
    "error_body": {"errorText": "Access is denied", "errorCode": "UnknownError"},
}


def _open_broker(monkeypatch, book: FakeBook):
    book.seed_position(1, 5898.5)
    book.fills.append({"orderId": ENTRY_ID, "contractId": 4242, "price": 5898.5, "qty": 1})
    b = make_broker(monkeypatch, book)
    b._last_position = Position(
        instrument="MES", direction="LONG", entry_price=5898.5,
        stop=5896.0, target=5904.0, quantity=1, open=True,
    )
    b._last_order_ids = {"instrument": "MES", "entry": ENTRY_ID, "target": TARGET_ID, "stop": STOP_ID}
    return b


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="KNOWN DEFECT FI-2: malformed /position/list read as flat -> FORCE_CLOSE_UNMATCHED")
@pytest.mark.parametrize("shape", list(MALFORMED))
def test_fi2_malformed_position_list_never_books_a_close(monkeypatch, shape):
    book = FakeBook()
    b = _open_broker(monkeypatch, book)
    book.get_faults["/position/list"] = MALFORMED[shape]
    fills = [b.resolve_position() for _ in range(3)]
    book.require(book.net_pos == 1, "fake broker still holds the position")
    booked = [(f.result, f.exit_reason, f.pnl_dollars) for f in fills if f is not None]
    rec = FaultRecord(
        case=f"FI-2 {shape} (broker level, same instance x3)",
        initial_journal="n/a (broker level); broker._last_position = open MES LONG",
        initial_broker="MES netPos=1, entry fill only, no exit fill",
        injected_failure=f"/position/list returns {MALFORMED[shape]!r} on 3 consecutive resolves",
        expected_safe_state="all resolves return None; position stays open/unknown",
        actual_state=f"booked={booked} last_position_open={bool(b._last_position)}",
        journal_broker_diverge=str(bool(booked)),
    )
    assert booked == [], str(rec)


def test_fi2_control_position_list_exception_stays_open(monkeypatch):
    """Control (passes today): a RAISED read is handled as uncertainty."""
    book = FakeBook()
    b = _open_broker(monkeypatch, book)
    book.get_faults["/position/list"] = ConnectionError("reset by peer")
    fills = [b.resolve_position() for _ in range(3)]
    assert fills == [None, None, None]
    assert b._last_position is not None and b._last_position.open


def test_fi2_runner_rebuilds_resolver_each_bar_so_malformed_list_books_nothing(
    config, tmp_path, monkeypatch,
):
    """Runner level (passes today — mitigation, not a fix): the runner builds a
    fresh TradovateBroker per bar (runner.py ~1370), so the resolver's fail
    counter never reaches 3 and no FORCE_CLOSE_UNMATCHED is journaled even
    after several bars of malformed position data."""
    book = FakeBook(place_mode="fill", children=True)
    broker = make_broker(monkeypatch, book)
    cfg = real_broker_cfg(config, working_order_recheck=False)
    log_dir = tmp_path / "logs"
    opened = run_alert(monkeypatch, broker, cfg, log_dir, mes_payload())
    book.require(opened["decision"] == "TRADE" and journal_open(log_dir), "journal holds the open trade")
    book.require(book.net_pos == 1, "fake broker holds the position")

    monkeypatch.setenv("BROKER", "tradovate")
    patch_broker_class(monkeypatch, book)
    book.get_faults["/position/list"] = MALFORMED["dict"]
    reads_before = book.gets.count("/position/list")
    decisions = []
    for ts in ("2026-05-23T14:45:00+00:00", "2026-05-23T15:00:00+00:00",
               "2026-05-23T15:15:00+00:00", "2026-05-23T15:30:00+00:00"):
        decisions.append(run_alert(monkeypatch, broker, cfg, log_dir, mes_payload(ts))["decision"])
    malformed_reads = book.gets.count("/position/list") - reads_before
    book.require(malformed_reads >= 4, f"resolver read the malformed list each bar (got {malformed_reads})")
    rec = FaultRecord(
        case="FI-2 runner level (4 bars)",
        initial_journal="open MES LONG with order ids",
        initial_broker="MES netPos=1, children working",
        injected_failure="/position/list returns a dict on every bar",
        expected_safe_state="journal stays open; no outcome booked",
        actual_state=f"decisions={decisions} malformed_reads={malformed_reads} outcomes={outcomes(log_dir)} journal_open={journal_open(log_dir)}",
    )
    assert outcomes(log_dir) == [], str(rec)
    assert journal_open(log_dir), str(rec)
