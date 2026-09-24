"""FI-4 — cancel failure on a resting entry (#950 audit critical gap 4).

A capped Limit entry rests as Working; the no-fill guard tries to cancel the
OSO and every /order/cancelorder call raises, so the order stays live at the
fake broker. Safe requirement: a failed or unconfirmed cancel leaves the state
unresolved and blocks new execution until broker truth is known.
"""
from __future__ import annotations

import pytest

from execution.broker_interface import BracketOrder
from tests.fault_injection._harness import (
    ENTRY_ID, FakeBook, FaultRecord, journal_open, make_broker, mes_payload, outcomes,
    real_broker_cfg, run_alert,
)

SECOND_BAR = "2026-05-23T14:45:00+00:00"


def _resting_book(monkeypatch) -> FakeBook:
    monkeypatch.setenv("ENTRY_SLIPPAGE_TOLERANCE_TICKS", "2")  # Limit entry -> no-fill guard
    book = FakeBook(place_mode="rest", children=True)
    book.post_rejects["/order/cancelorder"] = RuntimeError("cancel rejected: timeout")
    return book


def _entry_working(book: FakeBook) -> bool:
    return book.orders.get(ENTRY_ID, {}).get("ordStatus") == "Working"


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="KNOWN DEFECT FI-4: failed cancel is reported as ENTRY_NOT_FILLED")
def test_fi4_broker_does_not_report_no_fill_when_cancel_failed(monkeypatch):
    book = _resting_book(monkeypatch)
    broker = make_broker(monkeypatch, book)
    fill = broker.execute_bracket(BracketOrder(
        instrument="MES", direction="LONG", entry=5898.5, stop=5896.0,
        target=5904.0, rr_ratio=2.2, strategy="orb_breakout",
    ))
    cancels = sum(1 for p, _ in book.posts if p == "/order/cancelorder")
    book.require(cancels >= 1, "the no-fill guard attempted a cancel")
    book.require(_entry_working(book), "entry still Working at the fake broker")
    rec = FaultRecord(
        case="FI-4 broker level (execute_bracket)",
        initial_journal="n/a (broker level)",
        initial_broker="flat; Limit entry rests Working",
        injected_failure="every /order/cancelorder raises",
        expected_safe_state="result is unresolved, not a definitive no-fill",
        actual_state=(
            f"fill.result={fill.result} exit_reason={fill.exit_reason} "
            f"no_fill_reason={fill.no_fill_reason} cancel_attempts={cancels} "
            f"entry_still_working={_entry_working(book)}"
        ),
    )
    assert not (fill.result == "CANCELLED" and fill.exit_reason == "ENTRY_NOT_FILLED"), str(rec)


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="KNOWN DEFECT FI-4: runner books CANCELLED while the entry is still working")
def test_fi4_runner_does_not_book_flat_while_entry_still_working(config, tmp_path, monkeypatch):
    book = _resting_book(monkeypatch)
    broker = make_broker(monkeypatch, book)
    cfg = real_broker_cfg(config, working_order_recheck=True)
    log_dir = tmp_path / "logs"
    first = run_alert(monkeypatch, broker, cfg, log_dir, mes_payload())
    book.require(_entry_working(book), "entry still Working at the fake broker")
    booked = [(o.get("result"), o.get("exit_reason")) for o in outcomes(log_dir)]
    rec = FaultRecord(
        case="FI-4 runner level, entry still working",
        initial_journal="flat",
        initial_broker="flat; Limit entry rests Working",
        injected_failure="every /order/cancelorder raises",
        expected_safe_state="journal records an unresolved order, not a finished no-fill",
        actual_state=(
            f"decision={first['decision']} outcomes={booked} journal_open={journal_open(log_dir)} "
            f"broker {book.describe()} entry_working={_entry_working(book)}"
        ),
        journal_broker_diverge="True",
    )
    booked_flat = any(r == "CANCELLED" for r, _ in booked) and not journal_open(log_dir)
    assert not booked_flat, str(rec)


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="KNOWN DEFECT FI-4: the leftover entry fills later with no journal row")
def test_fi4_late_fill_of_uncancelled_entry_is_not_left_unrecorded(config, tmp_path, monkeypatch):
    book = _resting_book(monkeypatch)
    broker = make_broker(monkeypatch, book)
    cfg = real_broker_cfg(config, working_order_recheck=True)
    log_dir = tmp_path / "logs"
    run_alert(monkeypatch, broker, cfg, log_dir, mes_payload())
    book.require(_entry_working(book), "entry still Working at the fake broker")
    # The market comes back to the resting limit: it fills at the broker.
    book.orders[ENTRY_ID]["ordStatus"] = "Filled"
    book.seed_position(1, 5899.0)
    book.post_rejects.clear()
    second = run_alert(monkeypatch, broker, cfg, log_dir, mes_payload(SECOND_BAR))
    rec = FaultRecord(
        case="FI-4 late fill after failed cancel",
        initial_journal="flat (CANCELLED booked for the entry)",
        initial_broker="entry Working, then Filled -> MES netPos=1; children Working",
        injected_failure="cancelorder raised; the leftover entry fills afterwards",
        expected_safe_state="journal reflects the position (or UNKNOWN); entries blocked",
        actual_state=(
            f"journal_open={journal_open(log_dir)} second decision={second['decision']} "
            f"gate={second.get('gate_reason')} broker {book.describe()}"
        ),
        new_entry_blocked=str(book.place_calls() == 1),
        journal_broker_diverge=str(not journal_open(log_dir) and book.net_pos != 0),
    )
    assert journal_open(log_dir) or book.net_pos == 0, str(rec)
