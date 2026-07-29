"""Observe-only MNQ canonical-strategy watchers (vwap_hold, vwap_rejection).

Purpose: keep collecting evidence on two unresolved VWAP strategies while
`orb_breakout` remains the ONLY executable concept (risk_rules 1.2.0, the
isolated MNQ inverse-ORB forward-paper lane).

**These candidates reuse the canonical executable setup builders** —
`DecisionEngine._try_vwap_hold()` and `_try_vwap_rejection()` — rather than
reimplementing them. That is the whole point: an observer built from a
hand-copied formula drifts from the executable one and its evidence stops
being comparable. Nothing here duplicates or alters entry/stop/target/trend/
Strat/VWAP/structure logic; it calls the canonical method and relabels the
result.

Isolation contract (every item enforced by tests in
`tests/test_canonical_vwap_observers.py`):
  - MNQ only.
  - Returns `ShadowSetupCandidate`s under DISTINCT `*_observed` strategy names,
    so they can never be confused with executable `vwap_hold`/`vwap_rejection`.
  - Never enters ranking or the strategy permission gate: these are produced
    OUTSIDE `DecisionEngine.evaluate()`, after the active decision is already
    final, and are attached to the journal only.
  - Never calls `RiskEngine.validate`, never instantiates or calls a broker,
    never touches trade counts, daily limits, positions, or account state.
  - Cannot suppress `orb_breakout` — it is not a ranking participant at all.
  - Fail-soft: any exception yields no candidates and changes nothing.
  - Resolution reuses the existing shadow resolver
    (`resolve_shadow_candidate`), which already models entry-fill realism and
    pessimistic same-bar stop/target handling.

The canonical builders are pure setup constructors — they read `MarketState`
and config tick/VWAP-distance settings only. They do NOT consult
`enabled_concepts`, so calling them here observes the real bracket a
re-enabled strategy would produce, without re-enabling anything.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:  # pragma: no cover - typing only
    from context.market_context import MarketState

# The observed strategy names. Deliberately distinct from the executable
# "vwap_hold"/"vwap_rejection" identities.
VWAP_HOLD_OBSERVED = "vwap_hold_observed"
VWAP_REJECTION_OBSERVED = "vwap_rejection_observed"

OBSERVED_STRATEGIES = (VWAP_HOLD_OBSERVED, VWAP_REJECTION_OBSERVED)

# Canonical builder method name -> observed strategy name.
_CANONICAL_BUILDERS = (
    ("_try_vwap_hold", VWAP_HOLD_OBSERVED),
    ("_try_vwap_rejection", VWAP_REJECTION_OBSERVED),
)

# This observer watches MNQ only (the instrument the active lane runs on).
_OBSERVED_INSTRUMENTS = ("MNQ",)

# Cached engines keyed by id() of the config object. Building a DecisionEngine
# calls load_config() when given none, which is wasteful per-bar; the engine is
# stateless with respect to these pure setup builders, so caching is safe.
_ENGINE_CACHE: dict[int, Any] = {}


def is_observed_instrument(instrument: Optional[str]) -> bool:
    root = (instrument or "").upper().replace("1!", "")
    return root in _OBSERVED_INSTRUMENTS


def _engine(config=None):
    """A DecisionEngine used ONLY as a holder for the canonical setup builders.

    `DecisionEngine.evaluate()` is never called from this module — only the
    individual `_try_vwap_*` setup constructors, which are pure functions of
    MarketState plus tick/VWAP-distance config.
    """
    key = id(config)
    engine = _ENGINE_CACHE.get(key)
    if engine is None:
        from strategy.signal_engine import DecisionEngine

        engine = DecisionEngine(config=config)
        _ENGINE_CACHE[key] = engine
    return engine


def reset_engine_cache() -> None:
    """Test helper — drop cached engines so a fresh config takes effect."""
    _ENGINE_CACHE.clear()


def evaluate_canonical_observers(state: "MarketState", config=None) -> list:
    """Observe-only canonical VWAP candidates for this bar.

    Returns `ShadowSetupCandidate`s (possibly empty). Fail-soft by contract:
    any exception returns an empty list rather than propagating, so an
    observer defect can never alter the active decision.
    """
    try:
        if not is_observed_instrument(getattr(state, "instrument", None)):
            return []

        from strategy.shadow_setups import ShadowSetupCandidate

        engine = _engine(config)
        candidates: list[ShadowSetupCandidate] = []

        for method_name, observed_name in _CANONICAL_BUILDERS:
            builder = getattr(engine, method_name, None)
            if builder is None:  # pragma: no cover - defensive
                continue
            try:
                setup = builder(state)
            except Exception:
                # One builder failing must not suppress the other.
                continue
            if setup is None:
                continue
            candidate = _to_candidate(setup, observed_name)
            if candidate is not None:
                candidates.append(candidate)

        return candidates
    except Exception:
        return []


def _to_candidate(setup, observed_name: str):
    """Relabel a canonical SetupDetail as an observe-only ShadowSetupCandidate.

    Bracket values are carried across UNCHANGED — no rounding, recomputation,
    or adjustment. Only the identity (strategy name) and the observe-only
    risk-tier metadata are added.
    """
    from strategy.shadow_setups import RISK_MATRIX, ShadowSetupCandidate

    risk_tier, size_multiplier = RISK_MATRIX.get(observed_name, ("C", 0.25))
    canonical_note = (setup.notes or "").strip()
    note = f"[observe-only, canonical {setup.strategy}] {canonical_note}".strip()
    return ShadowSetupCandidate(
        strategy=observed_name,
        direction=setup.direction,
        entry=setup.entry,
        stop=setup.stop,
        target=setup.target,
        rr_ratio=setup.rr_ratio,
        risk_tier=risk_tier,
        size_multiplier=size_multiplier,
        notes=note,
    )
