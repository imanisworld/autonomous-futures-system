"""Pure gamma-exposure (GEX) math — no network, no config, no I/O.

Computes dealer gamma-exposure structure from an option chain snapshot:
net GEX, the per-strike profile, the gamma-flip (zero-gamma) level, and the
dominant call/put gamma walls. This is the testable core; fetching the chain and
wiring it into the journal live in ``sources.gex_observer``.

Dealer-sign convention (the standard SqueezeMetrics heuristic): dealers are long
calls / short puts, so call gamma contributes POSITIVE exposure and put gamma
NEGATIVE. Net GEX > 0 ⇒ dealers buy dips / sell rips (vol-suppressive, mean-
reverting). Net GEX < 0 ⇒ dealers chase (vol-expansive, trend-prone).

Dollar-gamma per contract (the "$ of dealer delta to hedge per 1% move"):
    sign · gamma · open_interest · 100 · spot² · 0.01
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

CONTRACT_MULTIPLIER = 100  # shares per option contract
# Drop legs whose implied vol exceeds this (fraction) from the BS zero-gamma solve.
# Public reports sane IV as a decimal (~0.18 at 6 DTE); expiry-day/after-hours 0DTE
# contracts blow up to 5+ (500%+) and would corrupt the flip.
_MAX_SANE_IV = 3.0


@dataclass(frozen=True)
class GexLeg:
    """One strike/side. ``iv`` (fraction) and ``tte_years`` are optional and only
    needed for the Black-Scholes zero-gamma flip; net GEX / walls ignore them."""

    strike: float
    is_call: bool
    gamma: float
    open_interest: float
    iv: Optional[float] = None
    tte_years: Optional[float] = None


@dataclass(frozen=True)
class GexProfile:
    """Computed gamma-exposure structure. ``ok=False`` ⇒ inputs were unusable."""

    ok: bool
    spot: Optional[float] = None
    net_gex: Optional[float] = None
    flip_point: Optional[float] = None
    call_wall: Optional[float] = None
    put_wall: Optional[float] = None
    regime: Optional[str] = None  # "positive" | "negative"
    per_strike: dict = field(default_factory=dict)  # strike -> net $-gamma
    n_legs: int = 0
    error: Optional[str] = None

    def to_dict(self) -> dict:
        """Compact, journal-friendly record (rounded; per_strike dropped)."""
        return {
            "ok": self.ok,
            "spot": _round(self.spot, 2),
            "net_gex": _round(self.net_gex, 0),
            "flip_point": _round(self.flip_point, 2),
            "call_wall": _round(self.call_wall, 2),
            "put_wall": _round(self.put_wall, 2),
            "regime": self.regime,
            "n_legs": self.n_legs,
            "error": self.error,
        }


def dollar_gamma(leg: GexLeg, spot: float) -> float:
    """Signed dollar-gamma for one leg (calls +, puts −)."""
    sign = 1.0 if leg.is_call else -1.0
    return sign * leg.gamma * leg.open_interest * CONTRACT_MULTIPLIER * spot * spot * 0.01


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def bs_gamma(spot: float, strike: float, tte_years: float, iv: float) -> float:
    """Black-Scholes gamma at a hypothetical spot (rates/divs ≈ 0 for short-dated)."""
    if spot <= 0 or strike <= 0 or tte_years <= 0 or iv <= 0:
        return 0.0
    srt = iv * math.sqrt(tte_years)
    d1 = (math.log(spot / strike) + 0.5 * iv * iv * tte_years) / srt
    return _norm_pdf(d1) / (spot * srt)


def zero_gamma_level(
    legs: list[GexLeg], spot: Optional[float], *, band: float = 0.20, steps: int = 200
) -> Optional[float]:
    """Precise gamma-flip: the spot S where total dealer gamma exposure = 0.

    Recomputes each leg's gamma via Black-Scholes across a ±``band`` grid around
    spot (using the per-leg IV) and finds the zero-crossing nearest spot. Unlike the
    cumulative-strike heuristic, this models how gamma shifts with price. Returns
    None when fewer than 4 legs carry a sane IV + positive time-to-expiry.
    """
    if spot is None or spot <= 0:
        return None
    usable = [
        (leg, 1.0 if leg.is_call else -1.0)
        for leg in legs
        if leg.iv and 0 < leg.iv < _MAX_SANE_IV
        and leg.tte_years and leg.tte_years > 0
        and leg.open_interest
    ]
    if len(usable) < 4:
        return None

    def total(s: float) -> float:
        return sum(
            sign * bs_gamma(s, leg.strike, leg.tte_years, leg.iv) * leg.open_interest
            for leg, sign in usable
        )

    lo, hi = spot * (1.0 - band), spot * (1.0 + band)
    prev_s, prev_v, best = lo, total(lo), None
    for i in range(1, steps + 1):
        s = lo + (hi - lo) * i / steps
        v = total(s)
        cand: Optional[float] = None
        if prev_v == 0:
            cand = prev_s
        elif (prev_v < 0 < v) or (prev_v > 0 > v):
            cand = prev_s + (0.0 - prev_v) / (v - prev_v) * (s - prev_s)
        if cand is not None and (best is None or abs(cand - spot) < abs(best - spot)):
            best = cand
        prev_s, prev_v = s, v
    return round(best, 2) if best is not None else None


def infer_spot_from_parity(
    pairs: list[tuple[float, float, float]],
) -> Optional[float]:
    """Estimate underlying spot from put-call parity at the near-ATM strike.

    ``pairs`` = ``[(strike, call_mid, put_mid), ...]``. For near-dated options
    (rates/divs ≈ 0): S ≈ K + C − P. The ATM strike (smallest |C − P|) gives the
    most reliable estimate; we average the two best to damp single-quote noise.
    Returns None when no strike has both a call and a put mid.
    """
    usable = [
        (k, c - p, abs(c - p))
        for (k, c, p) in pairs
        if c is not None and p is not None and c > 0 and p > 0
    ]
    if not usable:
        return None
    usable.sort(key=lambda t: t[2])  # closest to ATM first
    best = usable[:2]
    est = [k + diff for (k, diff, _) in best]
    return round(sum(est) / len(est), 4)


def compute_gex(legs: list[GexLeg], spot: Optional[float]) -> GexProfile:
    """Aggregate legs into a GexProfile. Fail-soft: bad inputs ⇒ ok=False."""
    if not legs:
        return GexProfile(ok=False, error="no_legs")
    if spot is None or spot <= 0:
        return GexProfile(ok=False, error="no_spot", n_legs=len(legs))

    per_strike: dict[float, float] = {}  # net (calls + puts), for the flip walk
    call_gex: dict[float, float] = {}     # call-only $-gamma per strike (≥0)
    put_gex: dict[float, float] = {}      # put-only $-gamma per strike (≤0)
    for leg in legs:
        if leg.gamma is None or leg.open_interest is None:
            continue
        dg = dollar_gamma(leg, spot)
        per_strike[leg.strike] = per_strike.get(leg.strike, 0.0) + dg
        side = call_gex if leg.is_call else put_gex
        side[leg.strike] = side.get(leg.strike, 0.0) + dg

    if not per_strike:
        return GexProfile(ok=False, error="no_usable_legs", n_legs=len(legs))

    net_gex = sum(per_strike.values())
    # Walls are computed PER SIDE (standard): the call wall is the strike with the
    # most call gamma (resistance), the put wall the strike with the most put
    # gamma (support). Netting sides would hide a wall behind heavier opposite OI.
    # Only strikes with NON-ZERO side gamma are candidates — otherwise a chain full
    # of zero-gamma legs (e.g. an expired/after-hours 0DTE snapshot) ties every
    # strike at $0 and max()/min() return an arbitrary deep-OTM strike.
    call_candidates = {k: v for k, v in call_gex.items() if v > 0}
    put_candidates = {k: v for k, v in put_gex.items() if v < 0}
    call_wall = max(call_candidates, key=call_candidates.__getitem__) if call_candidates else None
    put_wall = min(put_candidates, key=put_candidates.__getitem__) if put_candidates else None
    # Flip: prefer the precise Black-Scholes zero-gamma solve (needs per-leg IV +
    # time-to-expiry). Fall back to the cumulative zero-cross — banded to ±10% of
    # spot, since a crossing far out (deep-OTM OI noise) is not a real gamma flip —
    # when IV/TTE are absent.
    flip_point = zero_gamma_level(legs, spot)
    if flip_point is None:
        flip_raw = _flip_point(per_strike)
        flip_point = flip_raw if (flip_raw is not None and abs(flip_raw - spot) <= 0.10 * spot) else None
    regime = "positive" if net_gex >= 0 else "negative"

    return GexProfile(
        ok=True,
        spot=spot,
        net_gex=net_gex,
        flip_point=flip_point,
        call_wall=call_wall,
        put_wall=put_wall,
        regime=regime,
        per_strike=per_strike,
        n_legs=len(legs),
    )


def _flip_point(per_strike: dict[float, float]) -> Optional[float]:
    """Gamma-flip (zero-gamma) level: strike where cumulative GEX crosses zero.

    Walk strikes low→high accumulating net $-gamma; the flip is the price where
    the running total changes sign, linearly interpolated between the two
    bracketing strikes. None if the cumulative total never crosses zero.
    """
    strikes = sorted(per_strike)
    if len(strikes) < 2:
        return None
    cum = 0.0
    prev_k = strikes[0]
    prev_cum = per_strike[prev_k]
    cum = prev_cum
    for k in strikes[1:]:
        nxt_cum = cum + per_strike[k]
        if (prev_cum <= 0 <= nxt_cum) or (prev_cum >= 0 >= nxt_cum):
            if nxt_cum == prev_cum:
                return round((prev_k + k) / 2.0, 4)
            frac = (0.0 - prev_cum) / (nxt_cum - prev_cum)
            return round(prev_k + frac * (k - prev_k), 4)
        prev_k, prev_cum, cum = k, nxt_cum, nxt_cum
    return None


def _round(value: Optional[float], ndigits: int) -> Optional[float]:
    return None if value is None else round(value, ndigits)
