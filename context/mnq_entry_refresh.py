"""MNQ entry-refresh — Phase 1: moderate-detachment shadow lane.

Follows `docs/mnq-entry-refresh-study-2026-07-13.md` (PR #265): the study found
runner-exit-paired bracket TRANSLATION recovers positive, walk-forward-stable
expectancy for `orb_reclaim` when detachment is capped near 1R, but is not
even geometrically meaningful for the 35 live incidents observed (2.4R-45R —
`REJECTED_TARGET_PASSED` under a fixed-bracket check, `CAP_REJECTED` under a
translated one either way). This module implements ONLY the moderate class.

Scope, deliberately narrow (config-driven, not hardcoded, for forward
extensibility — but the DEFAULTS match exactly the approved Phase 1 scope):
  - instrument in ENTRY_REFRESH_INSTRUMENTS (default: MNQ only)
  - strategy in ENTRY_REFRESH_STRATEGIES (default: orb_reclaim only)
  - detachment <= ENTRY_REFRESH_MAX_DETACHMENT_R (default: 1.0)

Modes (config.entry_refresh_mode / ENTRY_REFRESH_MODE env, default "off"):
  - off:           this module does nothing; the caller must not invoke it.
  - observe_only:  compute the refresh decision and geometry, attach as a pure
                    audit dict. Never opens a shadow position, never resolves
                    an outcome, zero I/O beyond the caller's own journal write.
  - shadow:        everything observe_only does, PLUS: on a REFRESHED outcome,
                    the caller may open a persisted hypothetical position
                    (see execution/entry_refresh_shadow.py) that is resolved
                    against subsequent bars using the SAME runner-exit math as
                    real positions. No broker call, ever, at any mode.

"demo" and "live" are deliberately NOT valid values yet — Phase 1 is shadow
only (see docs/mnq-entry-refresh-study-2026-07-13.md "Phase 4"). Adding demo
execution is a separate, later, explicitly-approved build, not a config flip.

This module is pure decision logic — it does not journal, call risk, call a
broker, or open/close a shadow position itself. The caller (webhook/runner.py)
owns all of that.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

VALID_MODES = ("off", "observe_only", "shadow")
DEFAULT_MODE = "off"
DEFAULT_STRATEGIES = ("orb_reclaim",)
DEFAULT_INSTRUMENTS = ("MNQ",)
DEFAULT_MAX_DETACHMENT_R = 1.0
DEFAULT_MIN_RR = 1.0

_OUTCOMES = (
    "REFRESHED",
    "REJECTED_TOO_LATE",
    "REJECTED_TARGET_PASSED",
    "REJECTED_BAD_RR",
    "REJECTED_STOP_TOO_WIDE",
    "REJECTED_SETUP_INVALID",
    "REJECTED_NO_STRUCTURE",
)


def entry_refresh_mode(cfg=None) -> str:
    """Validated mode. Defense in depth: config/settings.py's _validate_config
    already fails the whole process closed at load time on an invalid value,
    but this helper never trusts that alone — anything outside VALID_MODES
    (including a stray "live"/"demo") falls back to "off", the safest state."""
    raw = getattr(cfg, "entry_refresh_mode", None)
    if raw is None:
        raw = os.getenv("ENTRY_REFRESH_MODE", DEFAULT_MODE)
    raw = str(raw or DEFAULT_MODE).strip().lower()
    return raw if raw in VALID_MODES else DEFAULT_MODE


def _str_tuple_setting(cfg, attr: str, env_name: str, default: tuple) -> frozenset:
    raw = getattr(cfg, attr, None)
    if raw is None:
        raw = os.getenv(env_name)
    if raw is None:
        return frozenset(default)
    if isinstance(raw, (list, tuple, set, frozenset)):
        items = raw
    else:
        items = str(raw).split(",")
    cleaned = frozenset(s.strip() for s in items if str(s).strip())
    return cleaned or frozenset(default)


def entry_refresh_strategies(cfg=None) -> frozenset:
    return _str_tuple_setting(
        cfg, "entry_refresh_strategies", "ENTRY_REFRESH_STRATEGIES", DEFAULT_STRATEGIES
    )


def entry_refresh_instruments(cfg=None) -> frozenset:
    return frozenset(
        s.upper() for s in _str_tuple_setting(
            cfg, "entry_refresh_instruments", "ENTRY_REFRESH_INSTRUMENTS", DEFAULT_INSTRUMENTS
        )
    )


def entry_refresh_max_detachment_r(cfg=None) -> float:
    raw = getattr(cfg, "entry_refresh_max_detachment_r", None)
    if raw is None:
        raw = os.getenv("ENTRY_REFRESH_MAX_DETACHMENT_R")
    try:
        value = float(raw) if raw is not None else DEFAULT_MAX_DETACHMENT_R
    except (TypeError, ValueError):
        return DEFAULT_MAX_DETACHMENT_R
    return value if value > 0 else DEFAULT_MAX_DETACHMENT_R


def is_entry_refresh_candidate(
    instrument: Optional[str], strategy: Optional[str], cfg=None
) -> bool:
    root = (instrument or "").upper().replace("1!", "")
    return (
        root in entry_refresh_instruments(cfg)
        and strategy in entry_refresh_strategies(cfg)
    )


@dataclass(frozen=True)
class RefreshDecision:
    outcome: str
    direction: str
    original_entry: float
    original_stop: float
    original_target: float
    live_price: float
    detachment_ticks: Optional[float]
    detachment_r: Optional[float]
    refreshed_entry: Optional[float]
    refreshed_stop: Optional[float]
    refreshed_target: Optional[float]
    refreshed_risk_ticks: Optional[float]
    refreshed_reward_ticks: Optional[float]
    refreshed_rr: Optional[float]
    max_detachment_r: float
    reason: str

    def to_audit_dict(self) -> dict:
        return {
            "outcome": self.outcome,
            "direction": self.direction,
            "original_entry": self.original_entry,
            "original_stop": self.original_stop,
            "original_target": self.original_target,
            "live_price": self.live_price,
            "detachment_ticks": self.detachment_ticks,
            "detachment_r": self.detachment_r,
            "refreshed_entry": self.refreshed_entry,
            "refreshed_stop": self.refreshed_stop,
            "refreshed_target": self.refreshed_target,
            "refreshed_risk_ticks": self.refreshed_risk_ticks,
            "refreshed_reward_ticks": self.refreshed_reward_ticks,
            "refreshed_rr": self.refreshed_rr,
            "max_detachment_r": self.max_detachment_r,
            "reason": self.reason,
        }


def _reject(
    outcome: str,
    reason: str,
    *,
    direction: str,
    entry: float,
    stop: float,
    target: float,
    live_price: float,
    max_detachment_r: float,
    detachment_ticks: Optional[float] = None,
    detachment_r: Optional[float] = None,
) -> RefreshDecision:
    assert outcome in _OUTCOMES
    return RefreshDecision(
        outcome=outcome,
        direction=direction,
        original_entry=entry,
        original_stop=stop,
        original_target=target,
        live_price=live_price,
        detachment_ticks=detachment_ticks,
        detachment_r=detachment_r,
        refreshed_entry=None,
        refreshed_stop=None,
        refreshed_target=None,
        refreshed_risk_ticks=None,
        refreshed_reward_ticks=None,
        refreshed_rr=None,
        max_detachment_r=max_detachment_r,
        reason=reason,
    )


def refresh_detached_entry(
    *,
    direction: str,
    entry: float,
    stop: float,
    target: float,
    live_price: float,
    tick: float,
    max_detachment_r: float = DEFAULT_MAX_DETACHMENT_R,
    min_rr: float = DEFAULT_MIN_RR,
    max_stop_ticks: Optional[float] = None,
) -> RefreshDecision:
    """Pure geometry decision for an `ENTRY_DETACHED_FROM_PRICE` candidate.

    Policy: full bracket TRANSLATION (the only policy the 2026-07-13 study
    validated for orb_reclaim) — stop and target both shift by the same offset
    as the entry, so R and R:R are preserved EXACTLY by construction. This
    means REJECTED_TARGET_PASSED / REJECTED_BAD_RR / REJECTED_STOP_TOO_WIDE
    can only fire on degenerate/adversarial inputs (defense-in-depth + kept
    for API parity with future non-translating policies) — under this policy
    the two REACHABLE rejections in normal operation are
    REJECTED_SETUP_INVALID (price already crossed the ORIGINAL stop — the
    thesis is broken, not just late) and REJECTED_TOO_LATE (detachment beyond
    the proven cap — this is what kills all 35 live incidents observed so
    far, every one of which was 2.4R+).

    REJECTED_NO_STRUCTURE is reserved for a caller-side re-validation (e.g.
    "is market_condition still TRENDING right now") that this pure function
    does not perform — the same-bar synchronous design means that check is
    already trivially true here (see webhook/runner.py wiring), so this
    outcome does not currently originate from this function.
    """
    kwargs = dict(
        direction=direction, entry=entry, stop=stop, target=target,
        live_price=live_price, max_detachment_r=max_detachment_r,
    )
    if direction not in ("LONG", "SHORT") or tick is None or tick <= 0:
        return _reject("REJECTED_SETUP_INVALID", "invalid direction or tick size", **kwargs)

    is_long = direction == "LONG"
    risk = abs(entry - stop)
    if risk <= 0:
        return _reject(
            "REJECTED_SETUP_INVALID", "degenerate original risk (entry == stop)", **kwargs
        )
    risk_ticks = risk / tick

    adverse = (live_price <= stop) if is_long else (live_price >= stop)
    if adverse:
        return _reject(
            "REJECTED_SETUP_INVALID",
            "live price has already crossed the ORIGINAL stop — thesis invalidated, not just late",
            **kwargs,
        )

    gap = (live_price - entry) if is_long else (entry - live_price)
    if gap <= 0:
        return _reject(
            "REJECTED_SETUP_INVALID",
            "live price has not moved past the original entry in the trade direction",
            **kwargs,
        )
    detachment_ticks = gap / tick
    detachment_r = detachment_ticks / risk_ticks

    if detachment_r > max_detachment_r:
        return _reject(
            "REJECTED_TOO_LATE",
            f"detachment {detachment_r:.2f}R exceeds the proven cap {max_detachment_r:.2f}R",
            detachment_ticks=detachment_ticks, detachment_r=detachment_r, **kwargs,
        )

    offset = live_price - entry
    new_entry = live_price
    new_stop = stop + offset
    new_target = target + offset
    new_risk = abs(new_entry - new_stop)
    new_reward = abs(new_target - new_entry)
    new_risk_ticks = new_risk / tick
    new_reward_ticks = new_reward / tick

    if new_reward <= 0:  # structurally unreachable under pure translation; defensive only
        return _reject(
            "REJECTED_TARGET_PASSED",
            "translated target no longer ahead of the new entry",
            detachment_ticks=detachment_ticks, detachment_r=detachment_r, **kwargs,
        )
    if max_stop_ticks is not None and new_risk_ticks > max_stop_ticks:
        return _reject(
            "REJECTED_STOP_TOO_WIDE",
            f"refreshed risk {new_risk_ticks:.1f} ticks exceeds max {max_stop_ticks:.1f}",
            detachment_ticks=detachment_ticks, detachment_r=detachment_r, **kwargs,
        )
    new_rr = new_reward / new_risk
    if new_rr < min_rr:  # structurally unreachable under pure translation; defensive only
        return _reject(
            "REJECTED_BAD_RR",
            f"refreshed R:R {new_rr:.2f} below minimum {min_rr:.2f}",
            detachment_ticks=detachment_ticks, detachment_r=detachment_r, **kwargs,
        )

    return RefreshDecision(
        outcome="REFRESHED",
        direction=direction,
        original_entry=entry,
        original_stop=stop,
        original_target=target,
        live_price=live_price,
        detachment_ticks=detachment_ticks,
        detachment_r=detachment_r,
        refreshed_entry=new_entry,
        refreshed_stop=new_stop,
        refreshed_target=new_target,
        refreshed_risk_ticks=new_risk_ticks,
        refreshed_reward_ticks=new_reward_ticks,
        refreshed_rr=new_rr,
        max_detachment_r=max_detachment_r,
        reason=(
            f"translated bracket {detachment_r:.2f}R within cap {max_detachment_r:.2f}R — "
            f"shadow only, no order sent"
        ),
    )
