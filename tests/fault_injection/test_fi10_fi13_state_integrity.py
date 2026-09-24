"""FI-10 / FI-11 / FI-13 — corrupt journal lines and collector state
(#950 audit gaps 11-12, the parts not covered by the JW #1002 / LW #1006 fixes).

A partial write (crash, full disk) or on-disk corruption leaves one unreadable
line. Safe requirement: an unreadable record is never read as "nothing there"
— the reader fails closed (counts the position as open, refuses the claim, or
raises) instead of silently skipping it.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from tests.fault_injection._harness import (
    FakeBook, FaultRecord, FaultSetupError, journal_open, make_broker, mes_payload,
    real_broker_cfg, run_alert,
)
from tests.fault_injection._p2_harness import require


def _truncate_line(path: Path, predicate) -> str:
    """Cut the first line matching ``predicate`` in half (a torn write)."""
    import json

    lines = path.read_text().splitlines()
    for i, line in enumerate(lines):
        if line.strip() and predicate(json.loads(line)):
            lines[i] = line[: len(line) // 2]
            path.write_text("\n".join(lines) + "\n")
            return lines[i]
    raise FaultSetupError("no line matched the truncation target")


def _read_or_fail_closed(fn):
    """Return fn(), or the marker 'raised' when the reader fails closed."""
    try:
        return fn()
    except Exception:  # noqa: BLE001 — raising IS the safe outcome here
        return "raised"


# ── FI-10: a torn TRADE row hides an open position ────────────────────────────
def test_fi10_torn_trade_row_is_not_read_as_flat(config, tmp_path, monkeypatch):
    book = FakeBook(place_mode="fill", children=True)
    broker = make_broker(monkeypatch, book)
    log_dir = tmp_path / "logs"
    first = run_alert(monkeypatch, broker, real_broker_cfg(config, working_order_recheck=True),
                      log_dir, mes_payload())
    require(first["decision"] == "TRADE" and journal_open(log_dir), "a real open TRADE is journaled")
    [journal] = sorted(log_dir.glob("journal_*.jsonl"))
    torn = _truncate_line(journal, lambda row: row.get("decision") == "TRADE")
    require(not any(r.get("decision") == "TRADE" for r in _rows_lenient(journal)),
            "the TRADE row is now unreadable")
    seen_open = _read_or_fail_closed(lambda: journal_open(log_dir))
    rec = FaultRecord(
        case="FI-10 torn TRADE row",
        initial_journal="one approved TRADE, position open",
        initial_broker=f"{book.describe()}",
        injected_failure=f"TRADE line truncated to {len(torn)} chars",
        expected_safe_state="position still counted open, or the reader raises",
        actual_state=f"journal_open={seen_open}",
        journal_broker_diverge=str(seen_open is False and book.net_pos != 0),
    )
    assert seen_open in (True, "raised"), str(rec)


def _rows_lenient(path: Path) -> list[dict]:
    import json

    rows = []
    for line in path.read_text().splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


# ── FI-11: a torn claim row lets the same bar be claimed twice ────────────────
def test_fi11_torn_bar_claim_is_not_claimed_again(tmp_path):
    from journal.journal_logger import JournalLogger

    journal = JournalLogger(log_dir=str(tmp_path))
    day = date(2026, 5, 23)
    claim = dict(instrument="MES", bar_ts="2026-05-23T14:30:00+00:00", for_date=day,
                 timeframe_minutes=15)
    require(journal.claim_bar(**claim) is True, "first claim succeeds")
    require(journal.claim_bar(**claim) is False, "control: an intact claim blocks a repeat")
    [path] = sorted(tmp_path.glob("journal_*.jsonl"))
    _truncate_line(path, lambda row: row.get("type") == "BAR_CLAIM")
    again = _read_or_fail_closed(lambda: journal.claim_bar(**claim))
    rec = FaultRecord(
        case="FI-11 torn BAR_CLAIM row",
        initial_journal="one BAR_CLAIM for MES 14:30 (15m)",
        initial_broker="n/a",
        injected_failure="the claim line is truncated",
        expected_safe_state="the repeat claim is refused, or the reader raises",
        actual_state=f"second claim_bar -> {again}",
    )
    assert again in (False, "raised"), str(rec)


# ── FI-13: corrupt collector state silently resets ────────────────────────────
def test_fi13_corrupt_collector_state_does_not_reset_silently(tmp_path):
    from context import wide_stop_forward_collector as collector
    from context import wide_stop_ledger_paper as contract

    ledger = contract.ledger_for("MNQ", collector.FOUR_HR)
    state = {
        "filled_date": "2026-09-08", "filled_count": 3,
        "position": {"candidate_key": "open-paper", "direction": "LONG", "entry": 20_000.0},
        "seen": ["a", "b", "c"],
    }
    collector._save_state(tmp_path, ledger, state)
    require(collector._load_state(tmp_path, ledger)["filled_count"] == 3, "state round-trips")
    collector._state_path(tmp_path, ledger).write_text("{bad")
    loaded = _read_or_fail_closed(lambda: collector._load_state(tmp_path, ledger))
    rec = FaultRecord(
        case="FI-13 corrupt wide-stop collector state",
        initial_journal="paper position open, 3 fills used, 3 seen keys",
        initial_broker="n/a (paper)",
        injected_failure="state file overwritten with invalid JSON",
        expected_safe_state="load raises (fail closed); never an empty state",
        actual_state=f"_load_state -> {loaded}",
    )
    assert loaded == "raised", str(rec)
