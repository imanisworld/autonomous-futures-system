"""Observe-only GEX producer, fed by the Public.com option-chain feed.

Maps a futures instrument to its tracking ETF (MNQ/NQ→QQQ, MES/ES→SPY), pulls a
near-dated chain with gamma + open interest, and computes a compact gamma-exposure
record (``sources.gex_compute``) for the journal. It is OBSERVE-ONLY: it NEVER
mutates ``state.gex`` or the gex_gate, never blocks, never raises. The journaled
``gex_observed`` record is scored against resolved outcomes by the existing GEX
shadow analysis — earn the gate before trusting it.

No vendor data product is involved: the chain comes from Public.com (already used,
read-only, by the options companion lane) and the GEX math is computed in-house.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import date
from typing import Any, Optional

from sources.gex_compute import GexLeg, compute_gex, infer_spot_from_parity

logger = logging.getLogger(__name__)

# Futures root → tracking ETF for the chain. Public covers liquid ETF options.
DEFAULT_SYMBOL_MAP = {"MNQ": "QQQ", "NQ": "QQQ", "MES": "SPY", "ES": "SPY"}

_CACHE_TTL_SECONDS = 60.0
_cache: dict[str, tuple[float, dict]] = {}  # etf -> (expires_monotonic, record)


def _root(instrument: str) -> str:
    return (instrument or "").upper().rstrip("!1234567890HMUZ")


def map_underlying(instrument: str, cfg: Any) -> Optional[str]:
    """Return the tracking ETF for a futures instrument, or None if unmapped."""
    mapping = getattr(cfg, "gex_observe_symbol_map", None) or DEFAULT_SYMBOL_MAP
    return mapping.get(_root(instrument))


def _build_provider(cfg: Any):
    from options_companion.chain_provider import PublicChainProvider

    return PublicChainProvider(
        base_url=getattr(cfg, "public_base_url", "https://api.public.com"),
        api_key=os.getenv("PUBLIC_API_KEY", "").strip(),
        account_id=os.getenv("PUBLIC_ACCOUNT_ID", "").strip(),
    )


async def _profile_record(provider, etf: str, max_dte: int) -> dict:
    """Fetch a chain via the provider and compute a compact GEX record."""
    async with provider:
        snap = await provider.fetch_chain(etf, max_dte=max_dte)
    if getattr(snap, "error", None):
        return {"ok": False, "underlying": etf, "error": snap.error}

    legs: list[GexLeg] = []
    call_mid: dict[float, float] = {}
    put_mid: dict[float, float] = {}
    today = date.today()
    for c in snap.contracts:
        is_call = c.contract_type == "CALL"
        if c.gamma is not None and c.open_interest is not None:
            # Years to expiry for the BS flip; same-day (0DTE) floored at half a
            # session so it isn't dropped. Already-expired contracts get None.
            days = (c.expiry - today).days
            tte = (max(days, 0.5) / 365.0) if days >= 0 else None
            legs.append(
                GexLeg(
                    strike=c.strike,
                    is_call=is_call,
                    gamma=c.gamma,
                    open_interest=c.open_interest,
                    iv=c.iv,
                    tte_years=tte,
                )
            )
        if c.mid is not None:
            (call_mid if is_call else put_mid)[c.strike] = c.mid

    pairs = [(k, call_mid[k], put_mid[k]) for k in call_mid.keys() & put_mid.keys()]
    spot = getattr(snap, "underlying_price", None) or infer_spot_from_parity(pairs)

    record = compute_gex(legs, spot).to_dict()
    record["underlying"] = etf
    return record


def observe_gex(
    instrument: str,
    cfg: Any,
    *,
    provider: Any = None,
    use_cache: bool = True,
) -> Optional[dict]:
    """Compact GEX record for the instrument's ETF, or None. Sync, fail-soft.

    Gated on ``cfg.gex_observe_enabled`` (default off). Caches per-ETF for
    ``_CACHE_TTL_SECONDS`` so multiple bars in a minute don't refetch the chain.
    Never raises — any error returns None.
    """
    if not getattr(cfg, "gex_observe_enabled", False):
        return None
    try:
        etf = map_underlying(instrument, cfg)
        if not etf:
            return None
        now = time.monotonic()
        if use_cache:
            hit = _cache.get(etf)
            if hit is not None and hit[0] > now:
                return hit[1]
        max_dte = int(getattr(cfg, "gex_observe_max_dte", 7) or 7)
        prov = provider or _build_provider(cfg)
        record = asyncio.run(_profile_record(prov, etf, max_dte))
        if use_cache:
            _cache[etf] = (now + _CACHE_TTL_SECONDS, record)
        return record
    except Exception:  # noqa: BLE001 — observe must never affect the pipeline
        logger.warning("GEX observe failed", exc_info=True)
        return None
