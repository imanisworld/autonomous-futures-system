"""Candidate formation and paper-outcome resolution rules for the shadow journal.

A scan may open a paper candidate only when it carries the full decision
context: underlying, direction, an exact contract, a source timestamp, an
entry quote, a bid/ask snapshot, liquidity data, a stop/invalidation, at
least one target, and a risk cap. Ordinary scans and provider failures never
become OPEN rows.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

RESOLVED_STATUSES = ("WIN", "LOSS", "BREAKEVEN", "CANCELLED", "EXPIRED", "REJECTED")

_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "direction": ("direction",),
    "entry_quote": ("option_mark", "mark", "premium", "entry_mark"),
    "bid": ("option_bid", "bid"),
    "ask": ("option_ask", "ask"),
    "liquidity": ("open_interest", "option_open_interest", "option_volume", "oi"),
    "stop": ("stop", "stop_level", "invalidation"),
    "target": ("target", "target_1", "target1"),
    "risk_cap": ("risk_cap", "max_loss", "risk_dollars"),
}


@dataclass(frozen=True)
class CandidateClassification:
    is_open_eligible: bool
    missing: tuple[str, ...]
    contract_key: str
    reason: str


def _first_present(data: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return value
    return None


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def contract_key_from(data: dict[str, Any]) -> str:
    """Stable identity for an exact contract, or "" when underspecified."""
    contract = data.get("contract")
    if contract not in (None, ""):
        return str(contract).upper().replace(" ", "")
    option_type = _first_present(data, ("option_type", "right"))
    strike = _first_present(data, ("strike",))
    expiry = _first_present(data, ("expiry", "expiration"))
    if option_type in (None, "") or strike in (None, "") or expiry in (None, ""):
        return ""
    return f"{str(data.get('ticker') or '').upper()}:{expiry}:{str(option_type).upper()}:{strike}"


def classify_candidate(raw: dict[str, Any]) -> CandidateClassification:
    missing: list[str] = []
    if not raw.get("ticker"):
        missing.append("underlying")
    if str(raw.get("direction") or "").upper() not in {"LONG", "SHORT"}:
        missing.append("direction")
    if raw.get("market_data_error") and _first_present(raw, ("option_bid",)) is None:
        # A provider failure can never open a candidate unless the alert source
        # itself supplied the full quote snapshot.
        return CandidateClassification(
            False,
            ("provider_data",),
            contract_key_from(raw),
            f"provider_error:{raw.get('market_data_error')}",
        )
    contract_key = contract_key_from(raw)
    if not contract_key:
        missing.append("contract")
    for name, aliases in _FIELD_ALIASES.items():
        if name == "direction":
            continue
        if _first_present(raw, aliases) is None:
            missing.append(name)
    if missing:
        return CandidateClassification(False, tuple(missing), contract_key, "not_a_candidate")
    return CandidateClassification(True, (), contract_key, "")


def open_candidate_fields(raw: dict[str, Any], contract_key: str) -> dict[str, Any]:
    """Snapshot persisted on the OPEN row so resolution never needs the scan raw."""
    return {
        "contract_key": contract_key,
        "entry_quote": _as_float(_first_present(raw, _FIELD_ALIASES["entry_quote"])),
        "entry_bid": _as_float(_first_present(raw, _FIELD_ALIASES["bid"])),
        "entry_ask": _as_float(_first_present(raw, _FIELD_ALIASES["ask"])),
        "liquidity": _as_float(_first_present(raw, _FIELD_ALIASES["liquidity"])),
        "stop": _as_float(_first_present(raw, _FIELD_ALIASES["stop"])),
        "target": _as_float(_first_present(raw, _FIELD_ALIASES["target"])),
        "risk_cap": _as_float(_first_present(raw, _FIELD_ALIASES["risk_cap"])),
        "expiry": _expiry_from(raw),
        "source_timestamp": raw.get("source_timestamp") or raw.get("timestamp"),
    }


def _expiry_from(raw: dict[str, Any]) -> str | None:
    expiry = _first_present(raw, ("expiry", "expiration"))
    if expiry is not None:
        try:
            return date.fromisoformat(str(expiry)).isoformat()
        except ValueError:
            return None
    dte = _as_float(raw.get("dte") or raw.get("days_to_expiration"))
    stamp = raw.get("source_timestamp") or raw.get("timestamp")
    if dte is not None and stamp:
        try:
            opened = datetime.fromisoformat(str(stamp))
        except ValueError:
            return None
        return (opened.date() + timedelta(days=int(dte))).isoformat()
    return None


def resolve_open_setup(
    *,
    direction: str,
    contract: dict[str, Any],
    underlying_price: float | None,
    now: datetime,
) -> tuple[str, dict[str, Any]] | None:
    """Deterministic paper resolution against stop/target levels on the underlying.

    Returns (status, outcome) or None when the setup stays OPEN. Expiry wins
    over price checks only when the price is unavailable; with a live price the
    stop/target check runs first so an in-the-money close on expiry day still
    resolves on levels.
    """
    stop = _as_float(contract.get("stop"))
    target = _as_float(contract.get("target"))
    expiry_raw = contract.get("expiry") or contract.get("expiration")
    expired = False
    if expiry_raw:
        try:
            expired = now.date() > date.fromisoformat(str(expiry_raw))
        except ValueError:
            expired = False
    if underlying_price is not None and stop is not None and target is not None:
        side = str(direction or "").upper()
        if side == "LONG":
            if underlying_price <= stop:
                return "LOSS", _resolution("stop_hit", underlying_price, now)
            if underlying_price >= target:
                return "WIN", _resolution("target_hit", underlying_price, now)
        elif side == "SHORT":
            if underlying_price >= stop:
                return "LOSS", _resolution("stop_hit", underlying_price, now)
            if underlying_price <= target:
                return "WIN", _resolution("target_hit", underlying_price, now)
    if expired:
        return "EXPIRED", _resolution("expired", underlying_price, now)
    return None


def _resolution(reason: str, underlying_price: float | None, now: datetime) -> dict[str, Any]:
    outcome: dict[str, Any] = {"closed_reason": reason, "resolved_at": now.isoformat()}
    if underlying_price is not None:
        outcome["underlying_price_at_resolution"] = underlying_price
    return outcome
