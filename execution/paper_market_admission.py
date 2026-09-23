"""Which markets the paper broker may place orders for (prereg 2026-09-23 §7).

MNQ and MES are the existing paper markets. Any other market needs a row in
``config/paper_market_admission.json``, which is empty until a (market, setup)
pair is CONFIRMED under docs/prereg-cross-market-paper-admission-2026-09-23.md
and the operator gives an explicit GO. A missing, unreadable or malformed file
means no extra markets (fail closed). This module only answers the question;
it never places, routes or changes anything.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from config.futures_contracts import SUPPORTED_ROOTS, contract_root

logger = logging.getLogger(__name__)

BASE_PAPER_ROOTS: tuple[str, ...] = ("MNQ", "MES")
ADMISSION_FILE = Path(__file__).resolve().parents[1] / "config" / "paper_market_admission.json"
_REQUIRED_ROW_KEYS = ("market", "setup", "prereg_status", "operator_go")


def admitted_rows(path: Optional[Path] = None) -> list[dict]:
    """Validated admission rows; any problem with the file returns []."""
    p = Path(path) if path is not None else ADMISSION_FILE
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, ValueError):
        logger.error("paper market admission file unreadable: %s — no extra markets", p)
        return []
    rows = data.get("admitted") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        logger.error("paper market admission file malformed: %s — no extra markets", p)
        return []
    good = []
    for row in rows:
        if not isinstance(row, dict) or any(not str(row.get(k) or "").strip() for k in _REQUIRED_ROW_KEYS):
            logger.error("paper market admission row incomplete, ignored: %r", row)
            continue
        market = str(row["market"]).strip().upper()
        if market in BASE_PAPER_ROOTS or market not in SUPPORTED_ROOTS:
            logger.error("paper market admission row for %r ignored (base or unknown market)", market)
            continue
        if str(row["prereg_status"]).strip().upper() != "CONFIRMED":
            logger.error("paper market admission row for %s ignored: prereg_status is not CONFIRMED", market)
            continue
        good.append({**row, "market": market})
    return good


def admitted_markets(path: Optional[Path] = None) -> frozenset[str]:
    return frozenset(r["market"] for r in admitted_rows(path))


def paper_order_allowed(instrument: object, path: Optional[Path] = None) -> tuple[bool, Optional[str]]:
    """(allowed, root). MNQ/MES always; others only when admitted."""
    root = contract_root(instrument)
    if root is None:
        return False, None
    if root in BASE_PAPER_ROOTS:
        return True, root
    return root in admitted_markets(path), root
