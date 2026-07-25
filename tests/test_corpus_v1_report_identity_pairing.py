"""Regression for the bug where scripts/corpus_v1_report.py::_load_trades
paired approved TRADE decisions with standalone OUTCOME rows via a
positional per-instrument FIFO queue -- reintroducing the exact defect
#327 fixed in adaptive/journal_reader.py (see
tests/test_journal_reader_identity_pairing.py). _load_trades now delegates
to JournalReader._trades_for_day (exact paper_order_id join, no FIFO
fallback). These tests prove the delegation is wired correctly end-to-end
through this script's own _stats()/_load_trades(), not just that
JournalReader itself is correct in isolation.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Optional

from scripts.corpus_v1_report import _load_trades, _stats

TODAY = date(2026, 7, 21)


def _write_journal(log_dir: Path, day: date, entries: list[dict]) -> None:
    path = log_dir / f"journal_{day.isoformat()}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


def _trade_entry(
    *,
    instrument: str = "MES",
    ts: str = "2026-07-21T14:30:00+00:00",
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
    ts: str = "2026-07-21T15:00:00+00:00",
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
    _write_journal(tmp_path, TODAY, [
        _trade_entry(paper_order_id="PAPER-a"),
        _outcome_entry(paper_order_id="PAPER-a", result="WIN", pnl_dollars=150.0),
    ])
    trades = _load_trades(tmp_path)
    assert len(trades) == 1
    assert trades[0]["result"] == "WIN"
    assert trades[0]["pnl"] == 150.0
    assert trades[0]["unjoinable_legacy"] is False
    s = _stats(trades)
    assert s == {
        "attempts": 1, "wins": 1, "losses": 0, "resolved": 1,
        "open_with_identity": 0, "unjoinable_legacy": 0,
        "win_rate": 1.0, "gross_win": 150.0, "gross_loss": 0,
        "profit_factor": float("inf"), "net_pnl": 150.0, "expectancy": 150.0,
    }


def test_out_of_order_outcomes_do_not_cross_pair_across_instruments(tmp_path):
    """The core #327 regression, re-proven through this script: two
    instruments resolve out of journal order. Positional FIFO would hand
    MES's WIN to the MNQ trade (and vice versa); identity-based pairing must
    not, regardless of which script is doing the reading."""
    _write_journal(tmp_path, TODAY, [
        _trade_entry(instrument="MNQ", ts="2026-07-21T14:30:00+00:00", paper_order_id="PAPER-mnq"),
        _trade_entry(instrument="MES", ts="2026-07-21T14:31:00+00:00", paper_order_id="PAPER-mes"),
        # MES resolves FIRST even though journaled second.
        _outcome_entry(instrument="MES", ts="2026-07-21T14:45:00+00:00",
                        paper_order_id="PAPER-mes", result="WIN", pnl_dollars=200.0),
        _outcome_entry(instrument="MNQ", ts="2026-07-21T15:00:00+00:00",
                        paper_order_id="PAPER-mnq", result="LOSS", pnl_dollars=-50.0),
    ])
    trades = {t["instrument"]: t for t in _load_trades(tmp_path)}
    assert trades["MNQ"]["result"] == "LOSS"
    assert trades["MNQ"]["pnl"] == -50.0
    assert trades["MES"]["result"] == "WIN"
    assert trades["MES"]["pnl"] == 200.0


def test_orphan_cancelled_outcome_cannot_shift_pairing(tmp_path):
    """An identity-less orphan OUTCOME (e.g. order-confirmation-missing,
    which never mints a paper_order_id) sits between the TRADE row and its
    real OUTCOME. FIFO would consume the orphan first and misbook the real
    trade as CANCELLED; identity-based matching must skip past it."""
    _write_journal(tmp_path, TODAY, [
        _trade_entry(paper_order_id="PAPER-a"),
        {
            "ts": "2026-07-21T14:40:00+00:00",
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
            },
        },
        _outcome_entry(paper_order_id="PAPER-a", result="WIN", pnl_dollars=150.0),
    ])
    trades = _load_trades(tmp_path)
    assert len(trades) == 1
    assert trades[0]["result"] == "WIN"
    assert trades[0]["pnl"] == 150.0


def test_unresolved_trade_with_real_identity_cannot_steal_a_later_outcome(tmp_path):
    """A TRADE with a real paper_order_id but no matching OUTCOME yet is
    genuinely still open -- it must stay unresolved, not opportunistically
    grab a later, unrelated OUTCOME row."""
    _write_journal(tmp_path, TODAY, [
        _trade_entry(paper_order_id="PAPER-open"),
        _outcome_entry(paper_order_id="PAPER-someone-else", result="WIN", pnl_dollars=999.0),
    ])
    trades = _load_trades(tmp_path)
    assert len(trades) == 1
    assert trades[0]["result"] is None
    assert trades[0]["pnl"] is None
    assert trades[0]["unjoinable_legacy"] is False
    s = _stats(trades)
    assert s["open_with_identity"] == 1
    assert s["unjoinable_legacy"] == 0
    assert s["resolved"] == 0


def test_missing_paper_order_id_fails_closed_as_unjoinable(tmp_path):
    """A TRADE row with no paper_order_id at all -- exactly what
    replay/replay_engine.py currently produces for every trade -- must be
    marked unjoinable_legacy and never FIFO-guessed onto an unrelated
    OUTCOME row sitting in the same file."""
    _write_journal(tmp_path, TODAY, [
        _trade_entry(paper_order_id=None),
        _outcome_entry(paper_order_id="PAPER-someone-else", result="WIN", pnl_dollars=999.0),
    ])
    trades = _load_trades(tmp_path)
    assert len(trades) == 1
    assert trades[0]["result"] is None
    assert trades[0]["pnl"] is None
    assert trades[0]["unjoinable_legacy"] is True
    s = _stats(trades)
    assert s["unjoinable_legacy"] == 1
    assert s["resolved"] == 0
    assert s["net_pnl"] == 0.0
