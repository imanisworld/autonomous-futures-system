"""stocks_advisory/paper_journal.py

Append-only JSONL persistence for the TQQQ/SQQQ Paper Advisory Bot v1
forward paper-proof harness. One `PaperJournalRecord` is appended per
decision evaluation and per lifecycle update -- existing lines are
never rewritten, truncated, or deleted; "the latest state of a trade"
is always reconstructed by taking the most recent record for a given
(`trade_date`, `strategy_version`) key, never by mutating an earlier
line.

No broker, order, execution, futures, or options_manager import of any
kind. The only I/O this module performs is reading/appending the one
journal file path the caller supplies -- no network call, no other
file access, no system-clock read (every timestamp field is supplied
by the caller).
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Optional


class PaperJournalError(ValueError):
    """Raised when the journal file itself cannot be trusted -- a
    corrupt or malformed line. Never raised for a missing file (an
    absent journal is just an empty history, not a defect); never
    silently skips a bad line, since dedup and open-position tracking
    depend on the full history being intact."""


@dataclasses.dataclass(frozen=True, kw_only=True)
class PaperJournalRecord:
    """One journal entry -- either a fresh decision (`status` in
    {WATCHING, NO_TRADE, INVALID}) or a lifecycle update to a
    previously-journaled trade (`status` in {ACTIVE, EXITED,
    INVALIDATED, EXPIRED}). `trade_date` + `strategy_version` together
    are the trade's identity; there may be multiple records sharing
    that identity over time, each one superseding the last."""

    trade_date: str
    strategy_version: str
    recorded_at: str
    data_source: str

    signal_symbol: str
    qqq_price: float
    direction: str
    vehicle_symbol: str
    decision: str  # "TRADE" | "NO_TRADE" | "INVALID"
    reason: str

    entry_trigger: str = ""
    stop_price: Optional[float] = None
    target_1: Optional[float] = None
    target_2: Optional[float] = None

    status: str = ""  # PaperTradeStatus.value, or "invalid" for a rejected intake
    raw_entry_price: Optional[float] = None
    modeled_entry_price: Optional[float] = None
    entry_time: Optional[str] = None
    raw_exit_price: Optional[float] = None
    modeled_exit_price: Optional[float] = None
    exit_time: Optional[str] = None
    exit_reason: str = ""

    shares: Optional[float] = None
    entry_slippage_dollars: Optional[float] = None
    exit_slippage_dollars: Optional[float] = None
    regulatory_fees_dollars: Optional[float] = None
    total_friction_dollars: Optional[float] = None
    gross_pnl_dollars: Optional[float] = None
    net_pnl_dollars: Optional[float] = None

    notes: str = ""


_OPEN_STATUSES = ("watching", "active")


def append_record(journal_path: Path, record: PaperJournalRecord) -> None:
    """Appends one record as a single JSON line. Never opens the file
    in a truncating mode -- creates it if absent, otherwise appends."""
    line = json.dumps(dataclasses.asdict(record), sort_keys=True)
    with open(journal_path, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def read_all_records(journal_path: Path) -> list[PaperJournalRecord]:
    """Reads every record in file order. Returns an empty list if the
    file does not exist yet. Raises `PaperJournalError` (never returns
    a partial/best-effort result) if any line is not valid JSON or is
    missing a required field -- a corrupt journal is not silently
    trusted."""
    path = Path(journal_path)
    if not path.exists():
        return []

    records: list[PaperJournalRecord] = []
    known_fields = {f.name for f in dataclasses.fields(PaperJournalRecord)}
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise PaperJournalError(f"{journal_path}:{line_number}: not valid JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise PaperJournalError(f"{journal_path}:{line_number}: expected a JSON object")
        unexpected = set(payload) - known_fields
        if unexpected:
            raise PaperJournalError(f"{journal_path}:{line_number}: unexpected field(s) {sorted(unexpected)}")
        try:
            records.append(PaperJournalRecord(**payload))
        except TypeError as exc:
            raise PaperJournalError(f"{journal_path}:{line_number}: missing required field: {exc}") from exc
    return records


def has_decision_for(journal_path: Path, trade_date: str, strategy_version: str) -> bool:
    """True if a decision has already been journaled for this
    (trade_date, strategy_version) -- the very first record ever
    written for that key is always the decision record, so presence of
    any record for the key means the decision-evaluation gate must
    refuse to run again."""
    for record in read_all_records(journal_path):
        if record.trade_date == trade_date and record.strategy_version == strategy_version:
            return True
    return False


def latest_record_for(
    journal_path: Path, trade_date: str, strategy_version: str
) -> Optional[PaperJournalRecord]:
    """The most recent record for one (trade_date, strategy_version)
    key, or None if that trade has never been journaled."""
    latest: Optional[PaperJournalRecord] = None
    for record in read_all_records(journal_path):
        if record.trade_date == trade_date and record.strategy_version == strategy_version:
            latest = record
    return latest


def latest_open_positions(journal_path: Path, strategy_version: str) -> list[PaperJournalRecord]:
    """The latest record per trade_date (for this strategy_version)
    whose status is still WATCHING or ACTIVE -- the set of positions a
    lifecycle-update pass needs to advance. Order is by first
    appearance of each trade_date in the file."""
    latest_by_date: dict[str, PaperJournalRecord] = {}
    order: list[str] = []
    for record in read_all_records(journal_path):
        if record.strategy_version != strategy_version:
            continue
        if record.trade_date not in latest_by_date:
            order.append(record.trade_date)
        latest_by_date[record.trade_date] = record
    return [
        latest_by_date[date]
        for date in order
        if latest_by_date[date].status.lower() in _OPEN_STATUSES
    ]
