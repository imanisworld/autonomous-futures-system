"""Read-only causal forward outcomes for deduped 30m coverage episodes.

Answers, per episode and per entry reference, *what did the underlying do
before the close?*  Two views are always computed and never substituted for
each other:

* **mechanical** -- entry at the episode's mechanical trigger, path starting
  at the first regular-session 5m bar (inside the 30m breakout bar) whose
  extreme crosses the trigger;
* **first sight** -- entry at the price the coverage observer stored for the
  first scanner tick that could see the bar, path starting strictly after
  that tick.

Rules fixed by the operator (2026-09-16):

* horizon = the same regular session only; nothing carries overnight; an
  episode with neither target nor invalidation touched by the close is
  ``UNRESOLVED_AT_CLOSE``;
* 5m bars are used only to resolve the forward path after the 30m setup
  already exists -- they never create, re-family, re-trigger, re-target or
  re-align a setup;
* intrabar ordering is never invented: a 5m bar that touches both the
  applicable target and the invalidation is ``AMBIGUOUS`` and the bar is
  preserved; a threshold touched in the same bar as the invalidation is not
  counted as reached;
* R is normalised so favourable movement is positive for both directions;
  the R denominator is the episode's structural risk (|trigger − invalidation|)
  for both views so they are comparable;
* both stored target geometries (nearest level, >=1R floor) are evaluated
  where valid; no new targets are generated.

Identity: :data:`OUTCOME_ID` / :data:`OUTCOME_VERSION`.  Nothing here writes to
the observer or V1 databases.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any, Sequence

from .causal_bars import MINUTE_5, Bar
from .coverage_episodes import Episode

__all__ = [
    "OUTCOME_ID",
    "OUTCOME_VERSION",
    "GEOMETRIES",
    "THRESHOLDS_R",
    "PathView",
    "EpisodeOutcome",
    "measure_episode",
    "gate_bucket",
    "summarize_outcomes",
]

OUTCOME_ID = "OPTIONS_COVERAGE_OUTCOMES"
OUTCOME_VERSION = "out-v0.1"
GEOMETRIES = ("nearest", "floor")
THRESHOLDS_R = (0.5, 1.0, 1.5, 2.0)
FIVE = MINUTE_5.delta
BLIND_WINDOW_MATERIAL_R = 0.25

OUTCOME_TARGET_FIRST = "TARGET_FIRST"
OUTCOME_INVALIDATION_FIRST = "INVALIDATION_FIRST"
OUTCOME_UNRESOLVED = "UNRESOLVED_AT_CLOSE"
OUTCOME_AMBIGUOUS = "AMBIGUOUS"
OUTCOME_AFTER_CLOSE = "FIRST_SIGHT_AFTER_CLOSE"
OUTCOME_DATA_INVALID = "DATA_INVALID"

BUCKET_ORDER = (
    "UNSUPPORTED_FAMILY",
    "TARGET_GEOMETRY_REJECTED",
    "MARKET_ALIGNMENT_REJECTED",
    "LATE_AT_FIRST_SIGHT",
    "WOULD_OTHERWISE_QUALIFY",
)


@dataclass
class PathView:
    """One entry reference walked forward against one target geometry."""

    view: str  # mechanical | first_sight
    geometry: str  # nearest | floor
    entry_reference: float | None
    entry_at: str | None
    structural_risk: float
    target_1: float | None
    target_2: float | None
    target_1_r: float | None
    target_2_r: float | None
    mfe: float | None = None
    mfe_r: float | None = None
    mae: float | None = None
    mae_r: float | None = None
    close_r: float | None = None
    max_favorable_r_before_invalidation: float | None = None
    max_adverse_r_before_target: float | None = None
    hit_0_5r_at: str | None = None
    hit_1_0r_at: str | None = None
    hit_1_5r_at: str | None = None
    hit_2_0r_at: str | None = None
    invalidation_hit_at: str | None = None
    target_1_hit_at: str | None = None
    target_2_hit_at: str | None = None
    first_decisive_event: str | None = None
    minutes_to_1r: float | None = None
    minutes_to_target_1: float | None = None
    minutes_to_invalidation: float | None = None
    outcome: str = OUTCOME_DATA_INVALID
    ambiguous_bar: dict[str, Any] | None = None
    forward_bars: int = 0
    flags: list[str] = field(default_factory=list)


@dataclass
class EpisodeOutcome:
    outcome_id: str
    outcome_version: str
    reducer_version: str
    symbol: str
    session_date: str
    family: str
    direction: str
    first_bar_start: str
    first_bar_close: str
    n_events: int
    v1_supported: bool
    requested_family: bool
    entry_trigger: float
    invalidation: float
    structural_risk: float
    first_sight_at: str
    first_sight_after_close: bool
    first_sight_price: float | None
    blind_window_extension_r: float | None
    # orthogonal cohort flags
    flag_v1_supported: bool
    flag_unsupported_family: bool
    flag_target_geometry_rejected_nearest: bool
    flag_target_geometry_rejected_floor: bool
    flag_market_alignment_rejected: bool
    flag_late_at_first_sight_nearest: bool | None
    flag_late_at_first_sight_floor: bool | None
    alignment_ok: bool
    alignment_failures: str
    spy_aligned: bool
    qqq_aligned: bool
    hourly_aligned: bool
    daily_aligned: bool
    # mutually exclusive funnel buckets (one per geometry rule)
    gate_bucket_nearest: str
    gate_bucket_floor: str
    # quality
    rr_quality_flags: list[str]
    quality_flags: list[str]
    views: list[PathView]

    @property
    def clean(self) -> bool:
        return not self.rr_quality_flags and not self.quality_flags

    def view(self, view: str, geometry: str) -> PathView | None:
        for item in self.views:
            if item.view == view and item.geometry == geometry:
                return item
        return None

    def to_row(self) -> dict[str, Any]:
        row = asdict(self)
        row["clean"] = self.clean
        return row


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _sign(direction: str) -> int:
    return 1 if direction == "LONG" else -1


def _fav(bar: Bar, direction: str) -> float:
    return bar.high if direction == "LONG" else bar.low


def _adv(bar: Bar, direction: str) -> float:
    return bar.low if direction == "LONG" else bar.high


def _reached(direction: str, extreme: float, level: float) -> bool:
    """Favourable extreme reached a level lying in the favourable direction."""
    return extreme >= level if direction == "LONG" else extreme <= level


def _stopped(direction: str, extreme: float, level: float) -> bool:
    """Adverse extreme reached a level lying in the adverse direction."""
    return extreme <= level if direction == "LONG" else extreme >= level


def _r(direction: str, price: float, entry: float, risk: float) -> float:
    return _sign(direction) * (price - entry) / risk


def _bar_dict(bar: Bar, levels: dict[str, float]) -> dict[str, Any]:
    return {
        "bar_start": bar.start_utc.isoformat(),
        "open": bar.open,
        "high": bar.high,
        "low": bar.low,
        "close": bar.close,
        "levels_touched": levels,
    }


def _session_5m(bars: Sequence[Bar], session_open: datetime, session_close: datetime) -> list[Bar]:
    open_utc = session_open.astimezone(timezone.utc)
    close_utc = session_close.astimezone(timezone.utc)
    return sorted(
        (b for b in bars if b.start_utc >= open_utc and b.start_utc + FIVE <= close_utc),
        key=lambda b: b.start_utc,
    )


def _grid_gaps(bars: Sequence[Bar], start: datetime, end: datetime) -> int:
    expected = set()
    cursor = start
    while cursor + FIVE <= end:
        expected.add(cursor)
        cursor += FIVE
    have = {b.start_utc for b in bars}
    return len(expected - have)


# --------------------------------------------------------------------------- #
# the walk
# --------------------------------------------------------------------------- #


def _walk(
    view: PathView,
    direction: str,
    bars: Sequence[Bar],
    invalidation: float,
    *,
    first_bar_is_cross: bool,
) -> None:
    """Walk ``bars`` forward from the entry reference and fill ``view`` in place.

    ``first_bar_is_cross`` marks the mechanical view, whose first bar is the
    5m bar that crossed the trigger: the entry sits somewhere inside it, so
    an invalidation touch in that bar is unprovable ordering.

    Ordering inside one 5m bar is never assumed: MFE / thresholds count only
    bars strictly before the invalidation bar, MAE-before-target counts only
    bars strictly before the target bar, and a bar touching both target_1 and
    the invalidation ends the walk as AMBIGUOUS.
    """
    entry = view.entry_reference
    risk = view.structural_risk
    if entry is None or not bars or risk <= 0:
        view.outcome = OUTCOME_DATA_INVALID
        return
    view.forward_bars = len(bars)
    t1, t2 = view.target_1, view.target_2
    mfe = mae = 0.0
    adv_before_t1 = 0.0
    inv_hit = t1_hit = t2_hit = False
    thresholds: dict[float, str | None] = {t: None for t in THRESHOLDS_R}

    def minutes(bar: Bar) -> float | None:
        if view.entry_at is None:
            return None
        return (bar.start_utc + FIVE - _ts(view.entry_at)).total_seconds() / 60

    for index, bar in enumerate(bars):
        stamp = bar.start_utc.isoformat()
        fav_r = _r(direction, _fav(bar, direction), entry, risk)
        adv_r = _r(direction, _adv(bar, direction), entry, risk)
        touched_inv = (not inv_hit) and _stopped(direction, _adv(bar, direction), invalidation)
        touched_t1 = (not t1_hit) and t1 is not None and _reached(direction, _fav(bar, direction), t1)
        touched_t2 = (not t2_hit) and t2 is not None and _reached(direction, _fav(bar, direction), t2)

        if touched_inv and touched_t1:
            view.outcome = OUTCOME_AMBIGUOUS
            view.ambiguous_bar = _bar_dict(bar, {"target_1": t1, "invalidation": invalidation})
            view.flags.append("same_5m_bar_target_stop")
            view.first_decisive_event = "AMBIGUOUS"
            break
        if index == 0 and first_bar_is_cross and touched_inv:
            view.outcome = OUTCOME_AMBIGUOUS
            view.ambiguous_bar = _bar_dict(bar, {"trigger": entry, "invalidation": invalidation})
            view.flags.append("trigger_path_ambiguous")
            view.first_decisive_event = "AMBIGUOUS"
            break

        if inv_hit:
            # After the stop nothing else is scored; the loop only continues
            # so a later target touch is never mistaken for a win.
            continue

        if touched_inv:
            inv_hit = True
            mae = min(mae, adv_r)
            view.invalidation_hit_at = stamp
            view.minutes_to_invalidation = minutes(bar)
            if view.first_decisive_event is None:
                view.first_decisive_event = "invalidation"
                view.outcome = OUTCOME_INVALIDATION_FIRST
            continue

        # This bar did not touch the stop: its favourable extreme is provably
        # reached before any later invalidation.
        mfe = max(mfe, fav_r)
        mae = min(mae, adv_r)
        for t in THRESHOLDS_R:
            if thresholds[t] is None and fav_r >= t:
                thresholds[t] = stamp
        if not t1_hit and not touched_t1:
            adv_before_t1 = min(adv_before_t1, adv_r)
        if touched_t1:
            t1_hit = True
            view.target_1_hit_at = stamp
            view.minutes_to_target_1 = minutes(bar)
            if view.first_decisive_event is None:
                view.first_decisive_event = "target_1"
                view.outcome = OUTCOME_TARGET_FIRST
        if touched_t2:
            t2_hit = True
            view.target_2_hit_at = stamp

    last = bars[-1]
    view.mfe_r, view.mae_r = round(mfe, 4), round(mae, 4)
    view.mfe, view.mae = round(mfe * risk, 4), round(mae * risk, 4)
    view.close_r = round(_r(direction, last.close, entry, risk), 4)
    view.max_favorable_r_before_invalidation = round(mfe, 4)
    view.max_adverse_r_before_target = round(adv_before_t1, 4)
    view.hit_0_5r_at, view.hit_1_0r_at, view.hit_1_5r_at, view.hit_2_0r_at = (thresholds[t] for t in THRESHOLDS_R)
    if view.hit_1_0r_at and view.entry_at:
        view.minutes_to_1r = (_ts(view.hit_1_0r_at) + FIVE - _ts(view.entry_at)).total_seconds() / 60
    if view.outcome == OUTCOME_DATA_INVALID:
        view.outcome = OUTCOME_UNRESOLVED
        view.first_decisive_event = "close"


def _view(view: str, geometry: str, episode: Episode, entry: float | None, entry_at: str | None) -> PathView:
    t1 = episode.nearest_target_1 if geometry == "nearest" else episode.floor_target_1
    t2 = getattr(episode, f"{geometry}_target_2", None)
    risk = episode.risk
    direction = episode.direction
    return PathView(
        view=view,
        geometry=geometry,
        entry_reference=entry,
        entry_at=entry_at,
        structural_risk=risk,
        target_1=t1,
        target_2=t2,
        target_1_r=_r(direction, t1, entry, risk) if (t1 is not None and entry is not None and risk) else None,
        target_2_r=_r(direction, t2, entry, risk) if (t2 is not None and entry is not None and risk) else None,
    )


def gate_bucket(episode: Episode, geometry: str) -> str:
    if not episode.v1_supported:
        return "UNSUPPORTED_FAMILY"
    geometry_ok = episode.nearest_geometry_ok if geometry == "nearest" else episode.floor_geometry_ok
    if not geometry_ok:
        return "TARGET_GEOMETRY_REJECTED"
    if not episode.alignment_ok:
        return "MARKET_ALIGNMENT_REJECTED"
    late = episode.late_nearest if geometry == "nearest" else episode.late_floor
    if episode.first_sight_after_close or late is None or late:
        return "LATE_AT_FIRST_SIGHT"
    return "WOULD_OTHERWISE_QUALIFY"


def measure_episode(episode: Episode, session_open: datetime, session_close: datetime, five_minute_bars: Sequence[Bar]) -> EpisodeOutcome:
    """Compute both views × both geometries for one episode.  Pure."""
    direction = episode.direction
    bars = _session_5m(five_minute_bars, session_open, session_close)
    close_utc = session_close.astimezone(timezone.utc)
    quality: list[str] = []
    trigger = float(episode.entry_trigger)
    invalidation = float(episode.invalidation)
    risk = float(episode.risk)

    bar_start = _ts(episode.first_bar_start)
    bar_close = _ts(episode.first_bar_close)
    if not bars:
        quality.append("missing_forward_bars")
    else:
        if bars[-1].start_utc + FIVE < close_utc:
            quality.append("incomplete_forward_session")
        gaps = _grid_gaps(bars, bar_start, close_utc)
        if gaps:
            quality.append("missing_forward_bars")

    # mechanical view: the setup exists once the inside/previous bar closed;
    # the trigger cross happens inside the 30m breakout bar.
    inside = [b for b in bars if bar_start <= b.start_utc < bar_close]
    cross_index = next((i for i, b in enumerate(inside) if _reached(direction, _fav(b, direction), trigger)), None)
    mech_entry_at: str | None = None
    mech_bars: list[Bar] = []
    if cross_index is not None:
        cross_bar = inside[cross_index]
        mech_entry_at = cross_bar.start_utc.isoformat()
        mech_bars = [b for b in bars if b.start_utc >= cross_bar.start_utc]
        opened_through = (cross_bar.open > trigger) if direction == "LONG" else (cross_bar.open < trigger)
        if opened_through:
            quality.append("trigger_path_ambiguous")
    else:
        quality.append("trigger_path_ambiguous")

    # first-sight view: strictly after the scanner tick
    sight_at = _ts(episode.first_sight_at)
    sight_price = episode.first_sight_price
    if sight_price is None and not episode.first_sight_after_close:
        quality.append("first_sight_price_missing")
    sight_bars = [b for b in bars if b.start_utc >= sight_at]
    if episode.first_sight_after_close:
        quality.append("first_sight_after_close")

    views: list[PathView] = []
    for geometry in GEOMETRIES:
        geometry_ok = episode.nearest_geometry_ok if geometry == "nearest" else episode.floor_geometry_ok
        target_1 = episode.nearest_target_1 if geometry == "nearest" else episode.floor_target_1
        mech = _view("mechanical", geometry, episode, trigger, mech_entry_at)
        if cross_index is None or not mech_bars:
            mech.outcome = OUTCOME_DATA_INVALID
            mech.flags.append("trigger_path_ambiguous" if cross_index is None else "missing_forward_bars")
        else:
            _walk(mech, direction, mech_bars, invalidation, first_bar_is_cross=True)
        if not geometry_ok:
            mech.flags.append(f"geometry_invalid:{geometry}")
        views.append(mech)

        sight = _view("first_sight", geometry, episode, sight_price, episode.first_sight_at)
        if episode.first_sight_after_close:
            sight.outcome = OUTCOME_AFTER_CLOSE
        elif sight_price is None or not sight_bars:
            sight.outcome = OUTCOME_DATA_INVALID
            sight.flags.append("first_sight_price_missing" if sight_price is None else "missing_forward_bars")
        else:
            _walk(sight, direction, sight_bars, invalidation, first_bar_is_cross=False)
        if not geometry_ok:
            sight.flags.append(f"geometry_invalid:{geometry}")
        views.append(sight)

    blind = None
    if sight_price is not None and risk > 0 and not episode.first_sight_after_close:
        blind = round(_r(direction, float(sight_price), trigger, risk), 4)

    return EpisodeOutcome(
        outcome_id=OUTCOME_ID,
        outcome_version=OUTCOME_VERSION,
        reducer_version=episode.reducer_version,
        symbol=episode.symbol,
        session_date=episode.session_date,
        family=episode.family,
        direction=direction,
        first_bar_start=episode.first_bar_start,
        first_bar_close=episode.first_bar_close,
        n_events=episode.n_events,
        v1_supported=episode.v1_supported,
        requested_family=episode.requested_family,
        entry_trigger=trigger,
        invalidation=invalidation,
        structural_risk=risk,
        first_sight_at=episode.first_sight_at,
        first_sight_after_close=episode.first_sight_after_close,
        first_sight_price=sight_price,
        blind_window_extension_r=blind,
        flag_v1_supported=episode.v1_supported,
        flag_unsupported_family=not episode.v1_supported,
        flag_target_geometry_rejected_nearest=not episode.nearest_geometry_ok,
        flag_target_geometry_rejected_floor=not episode.floor_geometry_ok,
        flag_market_alignment_rejected=not episode.alignment_ok,
        flag_late_at_first_sight_nearest=episode.late_nearest,
        flag_late_at_first_sight_floor=episode.late_floor,
        alignment_ok=episode.alignment_ok,
        alignment_failures=episode.alignment_failures,
        spy_aligned=episode.spy_aligned,
        qqq_aligned=episode.qqq_aligned,
        hourly_aligned=episode.hourly_aligned,
        daily_aligned=episode.daily_aligned,
        gate_bucket_nearest=gate_bucket(episode, "nearest"),
        gate_bucket_floor=gate_bucket(episode, "floor"),
        rr_quality_flags=list(episode.rr_quality_flags),
        quality_flags=sorted(set(quality)),
        views=views,
    )


# --------------------------------------------------------------------------- #
# summaries
# --------------------------------------------------------------------------- #


def _pct(n: int, d: int) -> float | None:
    return round(100.0 * n / d, 1) if d else None


def _quantiles(values: Sequence[float]) -> dict[str, float | None]:
    vals = sorted(v for v in values if v is not None and math.isfinite(v))
    if not vals:
        return {"n": 0, "p25": None, "median": None, "p75": None}

    def q(p: float) -> float:
        k = (len(vals) - 1) * p
        lo, hi = math.floor(k), math.ceil(k)
        return vals[lo] + (vals[hi] - vals[lo]) * (k - lo)

    return {"n": len(vals), "p25": round(q(0.25), 3), "median": round(median(vals), 3), "p75": round(q(0.75), 3)}


def _view_stats(outcomes: Sequence[EpisodeOutcome], view: str, geometry: str) -> dict[str, Any]:
    views = [o.view(view, geometry) for o in outcomes]
    views = [v for v in views if v is not None]
    scored = [v for v in views if v.outcome in (OUTCOME_TARGET_FIRST, OUTCOME_INVALIDATION_FIRST, OUTCOME_UNRESOLVED, OUTCOME_AMBIGUOUS)]
    with_target = [v for v in scored if v.target_1 is not None]
    return {
        "episodes": len(views),
        "TARGET_FIRST": sum(1 for v in views if v.outcome == OUTCOME_TARGET_FIRST),
        "INVALIDATION_FIRST": sum(1 for v in views if v.outcome == OUTCOME_INVALIDATION_FIRST),
        "UNRESOLVED_AT_CLOSE": sum(1 for v in views if v.outcome == OUTCOME_UNRESOLVED),
        "AMBIGUOUS": sum(1 for v in views if v.outcome == OUTCOME_AMBIGUOUS),
        "FIRST_SIGHT_AFTER_CLOSE": sum(1 for v in views if v.outcome == OUTCOME_AFTER_CLOSE),
        "DATA_INVALID": sum(1 for v in views if v.outcome == OUTCOME_DATA_INVALID),
        "scored": len(scored),
        "with_valid_target": len(with_target),
        "ge_0_5r": sum(1 for v in scored if v.hit_0_5r_at),
        "ge_1r": sum(1 for v in scored if v.hit_1_0r_at),
        "ge_1_5r": sum(1 for v in scored if v.hit_1_5r_at),
        "ge_2r": sum(1 for v in scored if v.hit_2_0r_at),
        "ge_1r_rate_pct": _pct(sum(1 for v in scored if v.hit_1_0r_at), len(scored)),
        "ge_2r_rate_pct": _pct(sum(1 for v in scored if v.hit_2_0r_at), len(scored)),
        "target_first_rate_pct": _pct(sum(1 for v in with_target if v.outcome == OUTCOME_TARGET_FIRST), len(with_target)),
        "invalidation_first_rate_pct": _pct(sum(1 for v in with_target if v.outcome == OUTCOME_INVALIDATION_FIRST), len(with_target)),
        "mfe_r": _quantiles([v.mfe_r for v in scored]),
        "mae_r": _quantiles([v.mae_r for v in scored]),
        "close_r": _quantiles([v.close_r for v in scored]),
        "minutes_to_1r": _quantiles([v.minutes_to_1r for v in scored if v.minutes_to_1r is not None]),
    }


def _blind_stats(outcomes: Sequence[EpisodeOutcome]) -> dict[str, Any]:
    values = [o.blind_window_extension_r for o in outcomes if o.blind_window_extension_r is not None]
    mech_1r = [o for o in outcomes if (o.view("mechanical", "nearest") or PathView("", "", None, None, 0, None, None, None, None)).hit_1_0r_at]
    still = [o for o in mech_1r if (o.view("first_sight", "nearest") or PathView("", "", None, None, 0, None, None, None, None)).hit_1_0r_at]
    return {
        "blind_window_extension_r": _quantiles(values),
        "pct_losing_ge_0_25r_before_first_sight": _pct(sum(1 for v in values if v <= -BLIND_WINDOW_MATERIAL_R), len(values)),
        "pct_gaining_ge_0_25r_before_first_sight": _pct(sum(1 for v in values if v >= BLIND_WINDOW_MATERIAL_R), len(values)),
        "mechanical_ge1r_episodes": len(mech_1r),
        "pct_mechanical_ge1r_still_ge1r_at_first_sight": _pct(len(still), len(mech_1r)),
    }


def _block(outcomes: Sequence[EpisodeOutcome]) -> dict[str, Any]:
    clean = [o for o in outcomes if o.clean]
    out: dict[str, Any] = {
        "episodes": len(outcomes),
        "clean_episodes": len(clean),
        "quality_flagged_episodes": len(outcomes) - len(clean),
        "all": {},
        "clean": {},
    }
    for population, subset in (("all", outcomes), ("clean", clean)):
        block = {}
        for view in ("mechanical", "first_sight"):
            for geometry in GEOMETRIES:
                block[f"{view}:{geometry}"] = _view_stats(subset, view, geometry)
        block["timing"] = _blind_stats(subset)
        block["timing_loss"] = {
            geometry: {
                "mechanical_ge1r_rate_pct": block[f"mechanical:{geometry}"]["ge_1r_rate_pct"],
                "first_sight_ge1r_rate_pct": block[f"first_sight:{geometry}"]["ge_1r_rate_pct"],
                "mechanical_target_first_rate_pct": block[f"mechanical:{geometry}"]["target_first_rate_pct"],
                "first_sight_target_first_rate_pct": block[f"first_sight:{geometry}"]["target_first_rate_pct"],
            }
            for geometry in GEOMETRIES
        }
        out[population] = block
    return out


def summarize_outcomes(outcomes: Sequence[EpisodeOutcome]) -> dict[str, Any]:
    def group(key) -> dict[str, Any]:
        keys = sorted({key(o) for o in outcomes})
        return {k: _block([o for o in outcomes if key(o) == k]) for k in keys}

    flag_counts: dict[str, int] = {}
    for o in outcomes:
        for f in o.quality_flags + o.rr_quality_flags:
            flag_counts[f] = flag_counts.get(f, 0) + 1
    return {
        "outcome_id": OUTCOME_ID,
        "outcome_version": OUTCOME_VERSION,
        "total": _block(list(outcomes)),
        "by_family": group(lambda o: o.family),
        "by_session": group(lambda o: o.session_date),
        "by_symbol": group(lambda o: o.symbol),
        "by_direction": group(lambda o: o.direction),
        "by_alignment": group(lambda o: "aligned" if o.alignment_ok else "not_aligned"),
        "by_gate_bucket_nearest": group(lambda o: o.gate_bucket_nearest),
        "by_gate_bucket_floor": group(lambda o: o.gate_bucket_floor),
        "quality_flag_counts": flag_counts,
    }
