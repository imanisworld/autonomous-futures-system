"""
execution/contract_identity.py

Contract-identity comparison for the #960 safety requirement — OBSERVE ONLY.

The dated contract that receives a broker order must be the dated contract
the alert's prices came from. The alert asserts its contract in the optional
``contract_hint`` field; the broker resolves its own dated symbol. This module
normalizes both names and compares them. It never predicts roll dates, never
converts prices, and never synthesizes a hint from the root, the ticker or
``_ROLL_DAYS``.

Design: docs/contract-identity-guard-design-2026-09-24.md (#966). This module
only reports a verdict; nothing here blocks an order. Enforcement is a later,
separately reviewed change.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Only the roots the guard is designed for (#966 §2).
SUPPORTED_ROOTS = ("MNQ", "MES")
QUARTERLY_CODES = "HMUZ"

MATCH = "MATCH"
UNKNOWN = "CONTRACT_IDENTITY_UNKNOWN"
UNNORMALIZABLE = "CONTRACT_IDENTITY_UNNORMALIZABLE"
MISMATCH = "CONTRACT_IDENTITY_MISMATCH"

OBSERVE_FILENAME = "contract_identity_observe.jsonl"

_SYMBOL = re.compile(r"^(?P<root>[A-Z0-9]+?)(?P<month>[FGHJKMNQUVXZ])(?P<year>\d{4}|\d)$")


def normalize(symbol: Optional[str], *, context_date: Optional[date] = None) -> Optional[str]:
    """Canonical ``<ROOT><MONTH><YYYY>`` or None when it cannot be proven.

    Accepts ``CME_MINI:MNQZ2026``, ``MNQZ2026`` and (with ``context_date``)
    ``MNQZ6``. A one-digit year is expanded only to the single year ending in
    that digit within [context year − 1, context year + 8]; without a context
    date it is not expanded. Unsupported roots and non-quarterly months fail.
    """
    if not isinstance(symbol, str):
        return None
    text = symbol.strip().upper()
    if ":" in text:
        text = text.rsplit(":", 1)[1]
    m = _SYMBOL.match(text)
    if not m:
        return None
    root, month, year = m.group("root"), m.group("month"), m.group("year")
    if root not in SUPPORTED_ROOTS or month not in QUARTERLY_CODES:
        return None
    if len(year) == 1:
        if context_date is None:
            return None
        years = [y for y in range(context_date.year - 1, context_date.year + 9) if y % 10 == int(year)]
        if len(years) != 1:
            return None
        year = str(years[0])
    return f"{root}{month}{year}"


@dataclass(frozen=True)
class Verdict:
    status: str                 # MATCH | CONTRACT_IDENTITY_*
    hint: Optional[str]         # raw alert value
    routed: Optional[str]       # raw routed/predicted symbol
    hint_normalized: Optional[str]
    routed_normalized: Optional[str]


def compare(hint: Optional[str], routed: Optional[str], *, context_date: date) -> Verdict:
    """Compare the alert's asserted contract with the routed one.

    Missing hint → UNKNOWN; either side unparseable → UNNORMALIZABLE;
    different contracts → MISMATCH. Never guesses.
    """
    hint_text = hint.strip() if isinstance(hint, str) else None
    h = normalize(hint_text, context_date=context_date) if hint_text else None
    r = normalize(routed, context_date=context_date)
    if not hint_text:
        status = UNKNOWN
    elif h is None or r is None:
        status = UNNORMALIZABLE
    elif h != r:
        status = MISMATCH
    else:
        status = MATCH
    return Verdict(status, hint_text or None, routed, h, r)


def record_observation(log_dir: str, row: dict) -> None:
    """Append one observe-only row. Fail-soft: never raises into the caller."""
    try:
        path = Path(log_dir) / OBSERVE_FILENAME
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"observed_at": datetime.now(timezone.utc).isoformat(), "enforced": False, **row}
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, default=str) + "\n")
    except Exception:  # noqa: BLE001 - evidence logging must never affect the alert path
        logger.warning("contract identity observation not recorded", exc_info=True)


def verdict_row(verdict: Verdict, **extra) -> dict:
    return {**asdict(verdict), **extra}
