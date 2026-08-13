"""Observe-only MNQ watchers backed by canonical VWAP setup builders.

These watchers are deliberately outside ``DecisionEngine.evaluate``: they do
not rank, request risk approval, mutate daily state, or reach a broker.  The
bracket is produced by the executable builder itself so replay/live formula
parity is structural rather than maintained by a copied formula.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:  # pragma: no cover
    from context.market_context import MarketState

VWAP_HOLD_OBSERVED = "vwap_hold_observed"
VWAP_REJECTION_OBSERVED = "vwap_rejection_observed"
OBSERVED_STRATEGIES = (VWAP_HOLD_OBSERVED, VWAP_REJECTION_OBSERVED)
_ENGINE_CACHE: dict[int, Any] = {}


def is_observed_instrument(instrument: Optional[str]) -> bool:
    return (instrument or "").upper().replace("1!", "") == "MNQ"


def _engine(config=None):
    key = id(config)
    engine = _ENGINE_CACHE.get(key)
    if engine is None:
        from strategy.signal_engine import DecisionEngine

        engine = DecisionEngine(config=config)
        _ENGINE_CACHE[key] = engine
    return engine


def reset_engine_cache() -> None:
    _ENGINE_CACHE.clear()


def evaluate_canonical_observers(state: "MarketState", config=None) -> list:
    """Return isolated canonical VWAP candidates; fail soft on all defects."""
    try:
        if not is_observed_instrument(getattr(state, "instrument", None)):
            return []
        engine = _engine(config)
        candidates = []
        # The validated hold population is New York only.  Rejection is an
        # unproven sample-collection observer and retains its natural sessions.
        builders = []
        if getattr(state, "session", None) == "new_york":
            builders.append(("_try_vwap_hold", VWAP_HOLD_OBSERVED))
        builders.append(("_try_vwap_rejection", VWAP_REJECTION_OBSERVED))
        for method_name, observed_name in builders:
            try:
                setup = getattr(engine, method_name)(state)
            except Exception:
                continue
            if setup is not None:
                candidates.append(_to_candidate(setup, observed_name))
        return candidates
    except Exception:
        return []


def _to_candidate(setup, observed_name: str):
    from strategy.shadow_setups import RISK_MATRIX, ShadowSetupCandidate

    risk_tier, size_multiplier = RISK_MATRIX.get(observed_name, ("C", 0.25))
    canonical_note = (setup.notes or "").strip()
    return ShadowSetupCandidate(
        strategy=observed_name,
        direction=setup.direction,
        entry=setup.entry,
        stop=setup.stop,
        target=setup.target,
        rr_ratio=setup.rr_ratio,
        risk_tier=risk_tier,
        size_multiplier=size_multiplier,
        notes=f"[observe-only, canonical {setup.strategy}] {canonical_note}".strip(),
    )
