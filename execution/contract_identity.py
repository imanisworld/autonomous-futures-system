"""Contract-identity normalization and fail-closed guard decisions.

This module is intentionally pure: it does not read broker state and it does
not submit/cancel orders. The external broker adapter owns the final placement
of this check after it resolves the Tradovate dated contract and before it
constructs any order body.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Mapping, Optional

CONTRACT_IDENTITY_UNKNOWN = "CONTRACT_IDENTITY_UNKNOWN"
CONTRACT_IDENTITY_MISMATCH = "CONTRACT_IDENTITY_MISMATCH"
CONTRACT_IDENTITY_UNNORMALIZABLE = "CONTRACT_IDENTITY_UNNORMALIZABLE"

_TRUE_VALUES = {"1", "true", "yes", "on", "enforce", "enabled", "fail_closed"}
_QUARTERLY_MONTHS = {"H", "M", "U", "Z"}
_SUPPORTED_ROOTS = {"MNQ", "MES"}
_SYMBOL_RE = re.compile(r"^(MNQ|MES)([HMUZ])(\d|\d{4})$")


@dataclass(frozen=True)
class ContractIdentityObservation:
    """Normalized contract-identity comparison evidence for logging/tests."""

    hint_raw: Optional[str]
    routed_raw: Optional[str]
    hint_normalized: Optional[str]
    routed_normalized: Optional[str]
    block_reason: Optional[str]
    enforcement_enabled: bool


def contract_identity_enforced(env: Optional[Mapping[str, str]] = None) -> bool:
    """Return whether contract-identity mismatches must block order submission.

    Default is observe/log-only. This preserves the design PR's pin-style
    rollout: operators must explicitly enable the guard before it fails closed.
    """

    source = os.environ if env is None else env
    raw = str(source.get("CONTRACT_IDENTITY_GUARD", "") or "").strip().lower()
    return raw in _TRUE_VALUES


def normalize_contract_symbol(value: object) -> Optional[str]:
    """Normalize a supported MNQ/MES dated contract symbol to Tradovate style.

    Accepted examples:
      - ``MNQZ6``
      - ``MNQZ2026``
      - ``CME_MINI:MNQZ2026``

    Continuous contracts (``MNQ1!``), bare roots (``MNQ``), unsupported roots,
    malformed strings, JSON-ish TradingView ticker descriptors, and non-strings
    return ``None`` so the caller can fail closed when enforcement is enabled.
    """

    if not isinstance(value, str):
        return None
    raw = value.strip().upper()
    if not raw:
        return None
    if raw.startswith("{") or raw.endswith("}"):
        return None
    if ":" in raw:
        raw = raw.rsplit(":", 1)[-1].strip()
    raw = raw.replace(" ", "")
    if raw.endswith("1!") or raw in _SUPPORTED_ROOTS:
        return None
    match = _SYMBOL_RE.fullmatch(raw)
    if not match:
        return None
    root, month_code, year = match.groups()
    if month_code not in _QUARTERLY_MONTHS:
        return None
    return f"{root}{month_code}{year[-1]}"


def contract_identity_observation(
    contract_hint: object,
    routed_symbol: object,
    *,
    enforce: bool,
) -> ContractIdentityObservation:
    """Return a deterministic comparison observation and optional block reason."""

    hint_normalized = normalize_contract_symbol(contract_hint)
    routed_normalized = normalize_contract_symbol(routed_symbol)
    block_reason: Optional[str] = None

    if enforce:
        if contract_hint is None or (isinstance(contract_hint, str) and not contract_hint.strip()):
            block_reason = CONTRACT_IDENTITY_UNKNOWN
        elif hint_normalized is None or routed_normalized is None:
            block_reason = CONTRACT_IDENTITY_UNNORMALIZABLE
        elif hint_normalized != routed_normalized:
            block_reason = CONTRACT_IDENTITY_MISMATCH

    return ContractIdentityObservation(
        hint_raw=contract_hint if isinstance(contract_hint, str) else None,
        routed_raw=routed_symbol if isinstance(routed_symbol, str) else None,
        hint_normalized=hint_normalized,
        routed_normalized=routed_normalized,
        block_reason=block_reason,
        enforcement_enabled=bool(enforce),
    )


def contract_identity_block_reason(
    contract_hint: object,
    routed_symbol: object,
    *,
    enforce: bool,
) -> Optional[str]:
    """Return the fail-closed block reason, or ``None`` if placement may continue."""

    return contract_identity_observation(
        contract_hint,
        routed_symbol,
        enforce=enforce,
    ).block_reason
