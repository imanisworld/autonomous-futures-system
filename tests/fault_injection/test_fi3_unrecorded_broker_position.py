"""FI-3 — unrecorded broker position before entry (#950 audit critical gap 3).

The fake broker holds a position the journal knows nothing about, with NO
working orders (children dead or never placed). Safe requirement: any broker
position not reconciled to the system's state blocks new submissions.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from tests.fault_injection._harness import (
    FakeBook, FaultRecord, journal_open, make_broker, mes_payload, real_broker_cfg, run_alert,
)


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="KNOWN DEFECT FI-3: runner never reads broker positions before entry")
def test_fi3_unrecorded_broker_position_blocks_new_entry(config, tmp_path, monkeypatch):
    book = FakeBook(place_mode="fill", children=True)
    book.seed_position(1, 5890.0)
    broker = make_broker(monkeypatch, book)
    log_dir = tmp_path / "logs"
    book.require(book.working_orders() == [], "no working orders on the account")
    book.require(not journal_open(log_dir), "journal flat")

    # The reconciler is the only other broker-truth sweep: with a flat journal
    # it returns before reading the broker at all.
    from webhook.reconciler import reconcile_open_position
    sweep = reconcile_open_position(
        config, log_dir=str(log_dir), now=datetime.now(timezone.utc), broker=broker,
    )

    cfg = real_broker_cfg(config, working_order_recheck=True)
    result = run_alert(monkeypatch, broker, cfg, log_dir, mes_payload())
    rec = FaultRecord(
        case="FI-3 unrecorded MES position, no working orders",
        initial_journal="flat",
        initial_broker="MES netPos=1 unknown to the journal; 0 working orders",
        injected_failure="none — a valid entry alert arrives",
        expected_safe_state="submission blocked; reason names the unreconciled broker position",
        actual_state=(
            f"reconciler={sweep} decision={result['decision']} "
            f"gate={result.get('gate_reason')} broker {book.describe()}"
        ),
        new_entry_blocked=str(book.place_calls() == 0),
        journal_broker_diverge="True",
    )
    assert book.place_calls() == 0, str(rec)
