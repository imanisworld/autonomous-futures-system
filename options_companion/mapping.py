"""Map a futures instrument + direction to companion options candidates.

v1 mapping (SPX deferred to a follow-up):
    MNQ / NQ  LONG  -> (QQQ, CALL)   SHORT -> (QQQ, PUT)
    MES / ES  LONG  -> (SPY, CALL)   SHORT -> (SPY, PUT)

Returns a LIST of ``(underlying, contract_type)`` tuples so the SPX follow-up
(a second SPY/ES candidate -> SPX) is a non-breaking addition.
"""

from __future__ import annotations

# Futures root -> options underlying. Root is the symbol with any month / contract
# suffix stripped (e.g. MESU6 -> MES), mirroring webhook/runner.py's root-normalize.
_ROOT_TO_UNDERLYING: dict[str, str] = {
    "MNQ": "QQQ",
    "NQ": "QQQ",
    "MES": "SPY",
    "ES": "SPY",
}


def _root(instrument: str) -> str:
    """Strip the month/contract suffix from a futures symbol (MESU6 -> MES)."""
    return (instrument or "").upper().rstrip("!1234567890HMUZ")


def map_companion_candidates(
    futures_instrument: str,
    direction: str,
) -> list[tuple[str, str]]:
    """Return companion ``(underlying, contract_type)`` candidates.

    Empty list when the direction is not LONG/SHORT or the futures root has no
    mapped underlying (so no companion row is ever formed for it).
    """
    side = (direction or "").strip().upper()
    if side not in {"LONG", "SHORT"}:
        return []
    underlying = _ROOT_TO_UNDERLYING.get(_root(futures_instrument))
    if not underlying:
        return []
    contract_type = "CALL" if side == "LONG" else "PUT"
    return [(underlying, contract_type)]
