"""
tests/test_journal_reader_identity_pairing.py

Regression for the bug where adaptive/journal_reader.py::_trades_for_day
paired approved TRADE decisions with standalone OUTCOME rows via a
positional FIFO queue — no instrument or identity check. A trade could be
misbooked with the wrong instrument's outcome, an extra/CANCELLED row
could shift every later pairing, and there was no available signal that a
row had been guessed rather than genuinely matched.

_trades_for_day now joins by paper_order_id — the id PaperBroker mints once
per position (execution/paper_broker.py) and that webhook/runner.py writes
onto both the confirmed TRADE row and its eventual OUTCOME row. Rows without
that identity are never guessed onto a trade; a TRADE row that never carried
one is marked unjoinable_legacy instead of silently FIFO-paired.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Optional

from adaptive.journal_reader import JournalReader


def _write_journal(log_dir: Path, day: date, entries: list[dict]) -> None:
    path = log_dir / f"journal_{day.isoformat()}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


def _trade_entry(
    *,
    instrument: str = "MES",
    ts: str = "2026-05-23T14:30:00+00:00",
    strategy: str = "orb_breakout",
    paper_order_id: Optional[str] = "PAPER-a",
) -> dict:
    entry = {
        "ts": ts,
        "instrument": instrument,
        "session": "new_york",
        "decision": "TRADE",
        "reason": strategy,
        "market_condition": "TRENDING",
        "setup": {
            "strategy": strategy,
            "direction": "LONG",
            "entry": 5000.0,
            "stop": 4990.0,
            "target": 5020.0,
            "rr_ratio": 2.0,
            "contracts": 1,
            "notes": None,
        },
        "risk_check": {"result": "APPROVED", "failed_rule": None, "reason": None},
        "confluence": {"grade": "A", "score": 75, "factors": [], "penalties": []},
        "context": {
            "trend": {"direction": "UP", "strength": "STRONG"},
            "vwap": {"value": 5000.0, "price_vs_vwap": "above", "reclaimed": False, "holding": True},
            "volume": {"current_bar": 500, "avg_bar": 400, "relative": 1.25},
        },
        "outcome": None,
    }
    if paper_order_id is not None:
        entry["paper_order_id"] = paper_order_id
    return entry


def _outcome_entry(
    *,
    instrument: str = "MES",
    ts: str = "2026-05-23T15:00:00+00:00",
    result: str = "WIN",
    pnl_dollars: float = 100.0,
    paper_order_id: Optional[str] = "PAPER-a",
) -> dict:
    outcome = {
        "result": result,
        "entry_price": 5000.0,
        "exit_price": 5020.0 if result == "WIN" else 4990.0,
        "exit_reason": "TARGET_HIT" if result == "WIN" else "STOP_HIT",
        "pnl_ticks": 40 if result == "WIN" else -40,
        "pnl_dollars": pnl_dollars,
        "contracts": 1,
    }
    if paper_order_id is not None:
        outcome["paper_order_id"] = paper_order_id
    return {
        "ts": ts,
        "type": "OUTCOME",
        "instrument": instrument,
        "session": "new_york",
        "outcome": outcome,
    }


def test_identity_join_pairs_correctly_as_a_sanity_baseline(tmp_path):
    """Positive control: a single TRADE/OUTCOME sharing an id still pairs."""
    today = date.today()
    _write_journal(tmp_path, today, [
        _trade_entry(paper_order_id="PAPER-a"),
        _outcome_entry(paper_order_id="PAPER-a", result="WIN", pnl_dollars=150.0),
    ])
    trades = JournalReader(tmp_path).read_trades(days=1)
    assert len(trades) == 1
    assert trades[0].result == "WIN"
    assert trades[0].pnl_dollars == 150.0
    assert trades[0].unjoinable_legacy is False


def test_cross_instrument_trades_do_not_cross_pair(tmp_path):
    """The core regression: two instruments' trades/outcomes resolve out of
    FIFO order. Positional pairing would hand MNQ's LOSS to the MES trade
    (and vice versa); identity-based pairing must not."""
    today = date.today()
    _write_journal(tmp_path, today, [
        _trade_entry(instrument="MNQ", ts="2026-05-23T14:30:00+00:00", paper_order_id="PAPER-mnq"),
        _trade_entry(instrument="MES", ts="2026-05-23T14:31:00+00:00", paper_order_id="PAPER-mes"),
        # MES resolves FIRST even though it was journaled second — this is
        # exactly the ordering that breaks a positional FIFO queue.
        _outcome_entry(instrument="MES", ts="2026-05-23T14:45:00+00:00",
                        paper_order_id="PAPER-mes", result="WIN", pnl_dollars=200.0),
        _outcome_entry(instrument="MNQ", ts="2026-05-23T15:00:00+00:00",
                        paper_order_id="PAPER-mnq", result="LOSS", pnl_dollars=-50.0),
    ])
    trades = {t.instrument: t for t in JournalReader(tmp_path).read_trades(days=1)}
    assert len(trades) == 2
    assert trades["MNQ"].result == "LOSS"
    assert trades["MNQ"].pnl_dollars == -50.0
    assert trades["MES"].result == "WIN"
    assert trades["MES"].pnl_dollars == 200.0


def test_extra_identity_less_outcome_cannot_shift_pairing(tmp_path):
    """An orphaned CANCELLED outcome with no paper_order_id (e.g. the
    order-confirmation-missing path, which never mints one) sits between the
    TRADE row and its real OUTCOME. A positional FIFO queue would consume the
    orphan first and misbook the real trade as CANCELLED; identity-based
    matching must skip straight past it."""
    today = date.today()
    _write_journal(tmp_path, today, [
        _trade_entry(paper_order_id="PAPER-a"),
        {
            "ts": "2026-05-23T14:40:00+00:00",
            "type": "OUTCOME",
            "instrument": "MES",
            "session": "new_york",
            "outcome": {
                "result": "CANCELLED",
                "entry_price": 5000.0,
                "exit_price": None,
                "exit_reason": "order_confirmation_missing:OPEN_without_order_identity",
                "pnl_ticks": 0.0,
                "pnl_dollars": 0.0,
                "contracts": 1,
                # no paper_order_id — this OUTCOME carries no identity.
            },
        },
        _outcome_entry(paper_order_id="PAPER-a", result="WIN", pnl_dollars=150.0),
    ])
    trades = JournalReader(tmp_path).read_trades(days=1)
    assert len(trades) == 1
    assert trades[0].result == "WIN"
    assert trades[0].pnl_dollars == 150.0


def test_open_trade_with_identity_stays_unresolved(tmp_path):
    """A TRADE row that carries a real paper_order_id but has no matching
    OUTCOME yet is genuinely still open — result stays None, and it must not
    be flagged unjoinable_legacy (it has real identity, just no outcome)."""
    today = date.today()
    _write_journal(tmp_path, today, [_trade_entry(paper_order_id="PAPER-a")])
    trades = JournalReader(tmp_path).read_trades(days=1)
    assert len(trades) == 1
    assert trades[0].result is None
    assert trades[0].pnl_dollars is None
    assert trades[0].unjoinable_legacy is False


def test_trade_without_paper_order_id_marked_unjoinable_legacy(tmp_path):
    """A TRADE row with no paper_order_id at all (legacy row, or a
    non-PaperBroker execution path) must be marked explicitly unjoinable —
    never FIFO-guessed onto an unrelated OUTCOME row that happens to be
    sitting in the same file."""
    today = date.today()
    _write_journal(tmp_path, today, [
        _trade_entry(paper_order_id=None),
        # A real, identified OUTCOME for a *different* position. A FIFO
        # queue would hand this straight to the id-less TRADE above.
        _outcome_entry(paper_order_id="PAPER-someone-else", result="WIN", pnl_dollars=999.0),
    ])
    trades = JournalReader(tmp_path).read_trades(days=1)
    assert len(trades) == 1
    assert trades[0].result is None
    assert trades[0].pnl_dollars is None
    assert trades[0].unjoinable_legacy is True
