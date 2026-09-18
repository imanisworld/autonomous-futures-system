"""Replay the frozen production V1 option selector from retained evidence.

This is advisory/evidence-only. It calls the same pure expiration and contract
selection functions used by the production scanner. It performs no provider I/O,
no storage write, no broker call, and no order construction.

The purpose is narrow: prove that retained decision-time evidence reproduces the
actual OPTIONS_PAPER_V1 contract decision. The newer canonical selector remains
useful as a reference/research implementation, but it is not production
authority unless a separately approved policy/cohort change makes it so.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
import hashlib
from pathlib import Path
from typing import Any, Mapping

from alert_ranker.market_data import OptionContractQuote
from alert_ranker.paper_v1 import choose_contract, choose_expiration

ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_SELECTOR_PATH = ROOT / "alert_ranker" / "paper_v1.py"


def production_selector_code_sha256(
    path: Path = PRODUCTION_SELECTOR_PATH,
) -> str:
    """Hash the exact production selector source used for evidence provenance."""

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _decision_time(value: object) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("decision_ts missing")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("decision_ts must be timezone-aware")
    return parsed


def _quote_from_supplement(row: Mapping[str, Any]) -> OptionContractQuote:
    return OptionContractQuote(
        symbol=str(row.get("contract_id") or ""),
        option_type=str(row.get("right") or ""),
        strike=row.get("strike"),
        bid=row.get("bid"),
        ask=row.get("ask"),
        mid=row.get("mid"),
        last=row.get("last"),
        volume=row.get("volume"),
        open_interest=row.get("open_interest"),
        delta=row.get("delta"),
        implied_volatility=row.get("implied_volatility"),
        quote_timestamp=row.get("quote_timestamp"),
        bid_timestamp=row.get("bid_timestamp"),
        ask_timestamp=row.get("ask_timestamp"),
        source=row.get("source"),
    )


def replay_production_selector(
    evidence: Mapping[str, Any],
) -> dict[str, Any]:
    """Replay OPTIONS_PAPER_V1 expiration + contract selection from evidence."""

    direction = str(evidence.get("production_direction") or "")
    if direction not in {"LONG", "SHORT"}:
        return {
            "status": "DATA_BLOCKED",
            "reason": "production_direction_invalid",
            "expiry": None,
            "contract": None,
        }

    try:
        decision_ts = _decision_time(evidence.get("decision_ts"))
    except ValueError:
        return {
            "status": "DATA_BLOCKED",
            "reason": "decision_timestamp_invalid",
            "expiry": None,
            "contract": None,
        }

    expirations = evidence.get("expiration_candidates")
    if not isinstance(expirations, list) or not expirations:
        return {
            "status": "DATA_BLOCKED",
            "reason": "expiration_candidates_missing",
            "expiry": None,
            "contract": None,
        }

    expiry_decision = choose_expiration(expirations, decision_ts)
    if not expiry_decision.valid or expiry_decision.expiry is None:
        return {
            "status": expiry_decision.status,
            "reason": expiry_decision.reason,
            "expiry": None,
            "contract": None,
        }

    captured_expiry = str(evidence.get("chosen_expiration") or "")
    if expiry_decision.expiry.expiration != captured_expiry:
        return {
            "status": "DATA_BLOCKED",
            "reason": "expiration_replay_mismatch",
            "expiry": asdict(expiry_decision.expiry),
            "contract": None,
        }

    supplement = evidence.get("chain_supplement")
    if not isinstance(supplement, list) or not supplement:
        return {
            "status": "DATA_BLOCKED",
            "reason": "chain_supplement_missing",
            "expiry": asdict(expiry_decision.expiry),
            "contract": None,
        }

    side = "CALL" if direction == "LONG" else "PUT"
    rows = [
        row
        for row in supplement
        if isinstance(row, Mapping)
        and str(row.get("expiration") or "") == captured_expiry
        and str(row.get("right") or "").upper() == side
    ]
    if not rows:
        return {
            "status": "DATA_BLOCKED",
            "reason": "production_side_chain_missing",
            "expiry": asdict(expiry_decision.expiry),
            "contract": None,
        }

    underlying = evidence.get("underlying")
    if not isinstance(underlying, Mapping):
        return {
            "status": "DATA_BLOCKED",
            "reason": "underlying_provenance_missing",
            "expiry": asdict(expiry_decision.expiry),
            "contract": None,
        }
    underlying_price = underlying.get("price")

    contracts = tuple(_quote_from_supplement(row) for row in rows)
    contract_decision = choose_contract(
        contracts,
        option_type=side,
        underlying_price=underlying_price,
    )
    return {
        "status": contract_decision.status,
        "reason": contract_decision.reason,
        "expiry": asdict(expiry_decision.expiry),
        "contract": (
            asdict(contract_decision.contract)
            if contract_decision.contract is not None
            else None
        ),
    }


def production_selection_matches_replay(
    production_selection: Mapping[str, Any],
    replay_result: Mapping[str, Any],
) -> bool:
    """Exact normalized parity for the production contract decision."""

    return (
        str(production_selection.get("status") or "")
        == str(replay_result.get("status") or "")
        and str(production_selection.get("reason") or "")
        == str(replay_result.get("reason") or "")
        and production_selection.get("contract") == replay_result.get("contract")
    )
