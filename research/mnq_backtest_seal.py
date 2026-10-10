"""Mandatory boundary checks for *new* MNQ historical research.

This is a research input-validation helper, not a broker/runtime change.
It does not authorize reading protected forward data or retroactively
rehabilitate an exploratory screen that already saw those data.
"""
from __future__ import annotations

from datetime import date, datetime
import re
from typing import Any, Iterable, Mapping

LAST_UNSEALED_MNQ_SESSION = date(2026, 6, 26)
FIRST_PROTECTED_MNQ_SESSION = date(2026, 6, 29)


class MNQSealViolation(ValueError):
    """An attempted historical MNQ request or data row crosses the seal."""


def _date(value: Any, label: str) -> date:
    if isinstance(value, datetime):
        raise MNQSealViolation(
            f"{label}: provide CME session-end DATE, not timestamp"
        )
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise MNQSealViolation(f"{label}: invalid YYYY-MM-DD date")
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise MNQSealViolation(f"{label}: invalid YYYY-MM-DD date") from exc
    raise MNQSealViolation(f"{label}: CME session-end date is required")


def _must_be_mnq(ticker: Any) -> None:
    if not isinstance(ticker, str):
        raise MNQSealViolation("MNQ ticker/source must be explicitly identified")
    # Supports MNQ, MNQM6, MNQM2026, MNQ1!, CME_MINI:MNQU2026,
    # and common broker prefix-qualified variants; never infers it from data.
    code = ticker.strip().upper().split(":")[-1]
    if not code.startswith("MNQ"):
        raise MNQSealViolation("MNQ source must be explicitly identified")


def validate_mnq_backtest_window(
    *, ticker: str, start_session: date | str, end_session: date | str,
) -> None:
    """Call BEFORE vendor fetch or file-open, not only after backtesting.

    End is the last CME session_END date, not the local timestamp of its
    preceding evening open. Deliberately rejects Jun 27–28 ambiguities.
    """
    _must_be_mnq(ticker)
    lo = _date(start_session, "start_session")
    hi = _date(end_session, "end_session")
    if lo > hi:
        raise MNQSealViolation("inverted or empty MNQ historical session window")
    if hi > LAST_UNSEALED_MNQ_SESSION:
        raise MNQSealViolation(
            "MNQ historical data are sealed after CME session 2026-06-26; "
            f"requested end session {hi}. Do not fetch/use Jun 29 or later "
            "in research; use registered forward-only study rules separately."
        )


def validate_mnq_backtest_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    ticker_field: str = "ticker",
    session_field: str = "session_end_date",
) -> int:
    """Fail the entire candidate dataset if any dated MNQ row is protected.

    Validate BEFORE features, scoring, parameter selection or aggregation.
    No silent trim, no data peeking, no reconstruction by local clock.
    This helper rejects absent/ambiguous CME session labels.
    """
    count = 0
    for count, row in enumerate(rows, start=1):
        if not isinstance(row, Mapping):
            raise MNQSealViolation("MNQ source row is not a mapping")
        _must_be_mnq(row.get(ticker_field))
        session = _date(row.get(session_field), session_field)
        if session > LAST_UNSEALED_MNQ_SESSION:
            raise MNQSealViolation(
                f"CONTAMINATED_POST_SEAL: MNQ row {count} has CME "
                f"session_end_date={session}; reject entire exploratory dataset"
            )
    if not count:
        raise MNQSealViolation("MNQ historical dataset empty; fail closed")
    return count
