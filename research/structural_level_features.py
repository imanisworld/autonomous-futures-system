"""Offline structural-level feature builder — RESEARCH ONLY (P1 of the frozen prereg).

Implements, from a list of 15-minute OHLCV bars ending at the decision bar ``B0``, the
level definitions (§3), the ``LC_ZONE`` construct (§4.1), the event taxonomy (§5), the
candidate-level reduction rule (§5.1) and the per-hypothesis T / F / NOT_APPLICABLE /
NOT_AVAILABLE labels (§6) of
``docs/prereg-dynamic-structural-level-attribution-2026-09-16.md`` (v1.2, frozen at
fdeac72).

Contract (binding, per the prereg):
  * pure — no I/O, no journal, no broker/risk/runner/replay/webhook imports; the only repo
    imports are the pure level helpers in ``context.location_context`` (so ``LC_ZONE`` is
    computed by the very same code that journals it live) and the contract tick table;
  * causal — every feature uses bars with open <= ``B0.open`` (``B0`` is the closed decision
    bar) and nothing later;
  * fail-closed — a level whose defining bar is missing is ``NOT_AVAILABLE``; a candidate
    that fits neither T nor F is ``NOT_APPLICABLE``; nothing is substituted or reassigned;
  * frozen constants — ``TAU_MTR``, ``K_SWEEP_BARS``, ``R_RETEST_BARS``, ``N_ACCEPT``,
    ``D_MAX_MTR``, ``PROX_MTR`` are the prereg values and are not tunable here.

Nothing under ``webhook/``, ``strategy/``, ``execution/`` or ``context/`` imports this
module, and nothing should. It computes features only; it never reads or produces an
outcome.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

from config.futures_contracts import contract_economics
from context.location_context import (
    ZONE_LOOKBACK_BARS_1H,
    ZONE_LOOKBACK_BARS_4H,
    _clean_bars,
    _median_true_range,
    _trading_day,
    aggregate,
    detect_zones,
    nearest_zones,
)

PREREG_VERSION = "1.2"
PREREG_SHA = "fdeac72"

_ET = ZoneInfo("America/New_York")

# ── frozen constants (prereg §5) ──────────────────────────────────────────────
TAU_MTR = 0.25          # touch tolerance for retest / target_rel, × MTR15
PROX_MTR = 0.5          # proximity / cluster / relevance band, × MTR15
K_SWEEP_BARS = 4        # sweep lookback (1 h)
R_RETEST_BARS = 8       # retest lookback (2 h), inherited IMPULSE_WINDOW_BARS_15M
N_ACCEPT = 2            # consecutive closes for ACCEPT
D_MAX_MTR = 1.0         # sweep depth cap, × MTR15 (deeper = break)
MTR_WINDOW_BARS = 64    # inherited: location_context MTR15 window
LIVE_BAR_WINDOW = 960   # inherited: runner passes BarHistory.recent(..., 960)

# ── level names ───────────────────────────────────────────────────────────────
MAJOR = ("PWH", "PWL", "PDH", "PDL", "ONH", "ONL", "NY_ORB_H", "NY_ORB_L",
         "LDN_ORB_H", "LDN_ORB_L")
ZONE_NAMES = ("LC_ZONE_4H_SUPPLY", "LC_ZONE_4H_DEMAND", "LC_ZONE_1H_SUPPLY", "LC_ZONE_1H_DEMAND")
EXPLORATORY_LEVELS = ("PMH", "PML", "HOD", "LOD")

# §5.1 tie precedence (higher timeframe first)
TIE_PRECEDENCE = {
    "PWH": 0, "PWL": 0, "PDH": 1, "PDL": 1, "ONH": 2, "ONL": 2,
    "NY_ORB_H": 3, "NY_ORB_L": 3, "LDN_ORB_H": 4, "LDN_ORB_L": 4,
    "LC_ZONE_4H_SUPPLY": 5, "LC_ZONE_4H_DEMAND": 5,
    "LC_ZONE_1H_SUPPLY": 6, "LC_ZONE_1H_DEMAND": 6,
    "PDC": 7, "VWAP": 8,
    # exploratory levels never enter a confirmatory anchor; ranked last for completeness
    "PMH": 9, "PML": 9, "HOD": 10, "LOD": 10,
}

# §6 hypothesis level sets
H_LEVEL_SETS = {
    "H1": MAJOR,
    "H2": MAJOR,
    "H3": MAJOR + ZONE_NAMES,
    "H4": MAJOR + ZONE_NAMES,
    "H5": MAJOR + ZONE_NAMES + ("PDC", "VWAP"),
    "H6": MAJOR + ZONE_NAMES,
}

# §6 own-level exclusion by strategy family
OWN_LEVEL_EXCLUSION = {
    "orb": ("NY_ORB_H", "NY_ORB_L", "LDN_ORB_H", "LDN_ORB_L"),
    "vwap": ("VWAP",),
    "pdh_pdl": ("PDH", "PDL"),
    "range_signal": (),  # the range wall is not one of the admitted levels; nothing to strip
}


def family_of(strategy: Optional[str]) -> str:
    """Own-level family for a strategy id (mirrors the review tooling's grouping)."""
    s = (strategy or "").lower()
    if s.startswith("orb_"):
        return "orb"
    if s.startswith("vwap_"):
        return "vwap"
    if s in ("pdh_reclaim", "pdl_reclaim"):
        return "pdh_pdl"
    if s.startswith("range_"):
        return "range_signal"
    if s.startswith("strat") or "strat_" in s:
        return "strat"
    return "other"


# ── sessions / calendar (re-implemented; parity-tested against state_builder) ─
def detect_session(ts: datetime) -> str:
    """Same map as webhook.state_builder.detect_session (not imported: no webhook deps)."""
    t = ts.astimezone(_ET).time()
    if t >= time(18, 0) or t < time(3, 0):
        return "asian"
    if time(3, 0) <= t < time(9, 30):
        return "london"
    if time(9, 30) <= t < time(17, 0):
        return "new_york"
    return "off_hours"


def trading_week(ts: datetime) -> tuple[int, int]:
    """ISO (year, week) of the CME trading day; Sunday 18:00 ET reopen belongs to Monday."""
    d = _trading_day(ts)
    iso = d.isocalendar()
    return (iso[0], iso[1])


def cme_open_hours(t0: datetime, t1: datetime) -> float:
    """Hours the CME equity-index market is open between t0 and t1 (17:00–18:00 ET halt and
    Friday 17:00 → Sunday 18:00 ET excluded). Hour-granular, used only for AGE."""
    if t1 <= t0:
        return 0.0
    cur = t0.astimezone(_ET)
    end = t1.astimezone(_ET)
    hours = 0.0
    while cur < end:
        nxt = min(end, (cur + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0))
        wd, hr = cur.weekday(), cur.hour
        closed = hr == 17 or (wd == 5) or (wd == 4 and hr >= 17) or (wd == 6 and hr < 18)
        if not closed:
            hours += (nxt - cur).total_seconds() / 3600.0
        cur = nxt
    return round(hours, 2)


GAP_CONTAMINATION_MIN = 45   # prereg §9.4


def _slot_open(t: datetime) -> bool:
    e = t.astimezone(_ET)
    wd, hr = e.weekday(), e.hour
    return not (hr == 17 or wd == 5 or (wd == 4 and hr >= 17) or (wd == 6 and hr < 18))


def gap_minutes(bars: list[dict], t0: datetime, t1: datetime) -> int:
    """Missing 15m slots (CME open hours only) with open in [t0, t1) given the bars present."""
    present = {b["ts"] for b in bars if t0 <= b["ts"] < t1}
    missing = 0
    t = t0
    while t < t1:
        if _slot_open(t) and t not in present:
            missing += 1
        t += timedelta(minutes=15)
    return missing * 15


def _flag_gap(lv: Level, bars: list[dict], t0: Optional[datetime], t1: Optional[datetime]) -> None:
    if t0 is None or t1 is None or t1 <= t0:
        return
    g = gap_minutes(bars, t0, t1)
    lv.gap_minutes = g
    lv.gap_contaminated = g >= GAP_CONTAMINATION_MIN


# ── data classes ──────────────────────────────────────────────────────────────
@dataclass
class Level:
    name: str
    value: Optional[float]           # point level (zones: facing-edge is resolved per side)
    status: str                      # AVAILABLE | NOT_AVAILABLE | NOT_IN_WINDOW
    kind: str                        # point | zone
    top: Optional[float] = None
    bottom: Optional[float] = None
    formed_ts: Optional[datetime] = None
    valid_from_idx: Optional[int] = None   # first bar index (in the window) where the level is in the reference set
    tests: Optional[int] = None            # zones: inherited detect_zones tests
    broken: Optional[bool] = None
    exploratory: bool = False
    reason: Optional[str] = None
    gap_minutes: Optional[int] = None      # missing 15m slots (CME open) inside the formation window
    gap_contaminated: Optional[bool] = None  # prereg §9.4: a gap >= 45 min inside the window

    def edge_facing(self, price: float) -> Optional[float]:
        if self.kind != "zone":
            return self.value
        if self.top is None or self.bottom is None:
            return None
        if price > self.top:
            return self.top
        if price < self.bottom:
            return self.bottom
        return price  # inside the zone


@dataclass
class LevelSet:
    instrument: str
    b0_ts: datetime
    session: str
    close: float
    tick: float
    mtr15: Optional[float]
    levels: dict[str, Level]
    zones_raw: dict[str, Any]
    n_bars: int
    warnings: list[str] = field(default_factory=list)

    def available(self, name: str) -> bool:
        lv = self.levels.get(name)
        return lv is not None and lv.status == "AVAILABLE" and lv.value is not None

    def to_dict(self) -> dict:
        return {
            "prereg_version": PREREG_VERSION, "prereg_sha": PREREG_SHA,
            "instrument": self.instrument, "b0_ts": self.b0_ts.isoformat(),
            "session": self.session, "close": self.close, "mtr15": self.mtr15,
            "n_bars": self.n_bars,
            "levels": {k: {"value": v.value, "status": v.status, "top": v.top, "bottom": v.bottom,
                           "tests": v.tests, "broken": v.broken, "exploratory": v.exploratory,
                           "reason": v.reason, "gap_minutes": v.gap_minutes,
                           "gap_contaminated": v.gap_contaminated,
                           "formed_ts": v.formed_ts.isoformat() if v.formed_ts else None}
                       for k, v in self.levels.items()},
            "warnings": list(self.warnings),
        }


# ── level construction (§3, §4.1) ────────────────────────────────────────────
def _et(ts: datetime) -> datetime:
    return ts.astimezone(_ET)


def _bar_et_time(b: dict) -> time:
    return _et(b["ts"]).time()


def build_levels(bars15: list[dict], instrument: str, *, b0_ts: Optional[datetime] = None) -> LevelSet:
    """Levels at the close of ``B0`` (= the last bar with ts <= ``b0_ts``; default: last bar).

    ``bars15`` may contain bars after ``b0_ts``; they are discarded (causality). The window
    handed to the zone/MTR helpers is the last ``LIVE_BAR_WINDOW`` bars, matching the live
    runner call so P3 parity compares like with like.
    """
    tick, _ = contract_economics(instrument)
    clean = _clean_with_volume(bars15)
    if b0_ts is not None:
        clean = [b for b in clean if b["ts"] <= b0_ts]
    if not clean:
        raise ValueError("no bars at or before b0_ts")
    past = clean[-LIVE_BAR_WINDOW:]
    b0 = past[-1]
    now = b0["ts"]
    session = detect_session(now)
    close = b0["close"]
    mtr15 = _median_true_range(past[-MTR_WINDOW_BARS:])
    warnings: list[str] = []
    levels: dict[str, Level] = {}

    today = _trading_day(now)
    by_day: dict[date, list[dict]] = {}
    for b in past:
        by_day.setdefault(_trading_day(b["ts"]), []).append(b)
    day_keys = sorted(by_day)
    today_bars = by_day.get(today, [])
    first_today_idx = len(past) - len(today_bars)

    # 3.1 PDH / PDL / PDC — previous trading day present in the window
    prev_keys = [k for k in day_keys if k < today]
    if prev_keys:
        pk = prev_keys[-1]
        pb = by_day[pk]
        complete = (pk == today - timedelta(days=1)) or (today.weekday() == 0 and pk == today - timedelta(days=3))
        if not complete:
            warnings.append(f"previous trading day in window is {pk}, not the calendar-previous day")
        formed = pb[-1]["ts"] + timedelta(minutes=15)
        levels["PDH"] = Level("PDH", max(b["high"] for b in pb), "AVAILABLE", "point", formed_ts=formed, valid_from_idx=first_today_idx)
        levels["PDL"] = Level("PDL", min(b["low"] for b in pb), "AVAILABLE", "point", formed_ts=formed, valid_from_idx=first_today_idx)
        levels["PDC"] = Level("PDC", pb[-1]["close"], "AVAILABLE", "point", formed_ts=formed, valid_from_idx=first_today_idx)
        pd_start = _et(pb[0]["ts"]).replace(hour=18, minute=0, second=0, microsecond=0) - timedelta(days=1) \
            if _et(pb[0]["ts"]).hour < 18 else _et(pb[0]["ts"]).replace(hour=18, minute=0, second=0, microsecond=0)
        pd_end = pd_start + timedelta(hours=23)
        for n in ("PDH", "PDL", "PDC"):
            _flag_gap(levels[n], past, pd_start, pd_end)
    else:
        for n in ("PDH", "PDL", "PDC"):
            levels[n] = Level(n, None, "NOT_AVAILABLE", "point", reason="no previous trading day in window")

    # 3.2 PWH / PWL — previous Mon–Fri trading week fully inside the window
    this_week = trading_week(now)
    prev_week_bars = [b for b in clean if trading_week(b["ts"]) < this_week]
    if prev_week_bars:
        pw = trading_week(prev_week_bars[-1]["ts"])
        pwb = [b for b in prev_week_bars if trading_week(b["ts"]) == pw]
        pw_days = {_trading_day(b["ts"]) for b in pwb}
        monday = min(pw_days) - timedelta(days=min(pw_days).weekday())
        expected = {monday + timedelta(days=i) for i in range(5)}
        if not expected.issubset(pw_days):
            missing = sorted(expected - pw_days)
            levels["PWH"] = Level("PWH", None, "NOT_AVAILABLE", "point", reason=f"previous week incomplete in window: missing {missing}")
            levels["PWL"] = Level("PWL", None, "NOT_AVAILABLE", "point", reason=f"previous week incomplete in window: missing {missing}")
        else:
            formed = pwb[-1]["ts"] + timedelta(minutes=15)
            week_start_idx = next((i for i, b in enumerate(past) if trading_week(b["ts"]) == this_week), len(past) - 1)
            levels["PWH"] = Level("PWH", max(b["high"] for b in pwb), "AVAILABLE", "point", formed_ts=formed, valid_from_idx=week_start_idx)
            levels["PWL"] = Level("PWL", min(b["low"] for b in pwb), "AVAILABLE", "point", formed_ts=formed, valid_from_idx=week_start_idx)
            wk_start = datetime.combine(monday - timedelta(days=1), time(18, 0), tzinfo=_ET)
            wk_end = datetime.combine(monday + timedelta(days=4), time(17, 0), tzinfo=_ET)
            for n in ("PWH", "PWL"):
                _flag_gap(levels[n], clean, wk_start, wk_end)
    else:
        for n in ("PWH", "PWL"):
            levels[n] = Level(n, None, "NOT_AVAILABLE", "point", reason="no previous trading week in window")

    # 3.3 / 3.4 / 3.5 ONH/ONL (frozen 09:30, RTH-only events), PMH/PML, HOD/LOD (running)
    # datetime (not time-of-day) comparison: the overnight run starts at 18:00 ET the
    # previous calendar day — identical to location_context._day_ranges
    rth_open_dt = datetime.combine(today, time(9, 30), tzinfo=_ET)
    pm_start_dt = datetime.combine(today, time(4, 0), tzinfo=_ET)
    on_bars = [b for b in today_bars if _et(b["ts"]) < rth_open_dt]
    pm_bars = [b for b in on_bars if _et(b["ts"]) >= pm_start_dt]
    if on_bars:
        frozen = session == "new_york"
        rth_idx = first_today_idx + len(on_bars)
        levels["ONH"] = Level("ONH", max(b["high"] for b in on_bars), "AVAILABLE" if frozen else "NOT_IN_WINDOW", "point",
                              formed_ts=on_bars[-1]["ts"] + timedelta(minutes=15), valid_from_idx=rth_idx,
                              reason=None if frozen else "overnight range still forming; events are RTH-only")
        levels["ONL"] = Level("ONL", min(b["low"] for b in on_bars), levels["ONH"].status, "point",
                              formed_ts=levels["ONH"].formed_ts, valid_from_idx=rth_idx, reason=levels["ONH"].reason)
        on_start = datetime.combine(today - timedelta(days=1), time(18, 0), tzinfo=_ET)
        on_end = min(datetime.combine(today, time(9, 30), tzinfo=_ET), now + timedelta(minutes=15))
        for n in ("ONH", "ONL"):
            _flag_gap(levels[n], past, on_start, on_end)
    else:
        for n in ("ONH", "ONL"):
            levels[n] = Level(n, None, "NOT_AVAILABLE", "point", reason="no overnight bars for the current trading day in window")
    if pm_bars:
        st = "AVAILABLE" if session == "new_york" else "NOT_IN_WINDOW"
        levels["PMH"] = Level("PMH", max(b["high"] for b in pm_bars), st, "point", exploratory=True, valid_from_idx=first_today_idx + len(on_bars))
        levels["PML"] = Level("PML", min(b["low"] for b in pm_bars), st, "point", exploratory=True, valid_from_idx=first_today_idx + len(on_bars))
    else:
        for n in ("PMH", "PML"):
            levels[n] = Level(n, None, "NOT_AVAILABLE", "point", exploratory=True, reason="no premarket bars")
    if today_bars:
        levels["HOD"] = Level("HOD", max(b["high"] for b in today_bars), "AVAILABLE", "point", exploratory=True, valid_from_idx=first_today_idx)
        levels["LOD"] = Level("LOD", min(b["low"] for b in today_bars), "AVAILABLE", "point", exploratory=True, valid_from_idx=first_today_idx)
        for n in ("HOD", "LOD"):
            _flag_gap(levels[n], past, datetime.combine(today - timedelta(days=1), time(18, 0), tzinfo=_ET), now + timedelta(minutes=15))
    else:
        for n in ("HOD", "LOD"):
            levels[n] = Level(n, None, "NOT_AVAILABLE", "point", exploratory=True, reason="no bars for the current trading day")

    # 3.6 NY ORB / London ORB — the canonical opening bar must exist; no substitute
    def _orb(prefix: str, open_t: time, valid_from: time, valid_to: time, day_bars: list[dict]) -> None:
        bar = next((b for b in day_bars if _bar_et_time(b) == open_t), None)
        t0 = _et(now).time()
        in_window = valid_from <= t0 < valid_to
        if bar is None:
            for suffix in ("H", "L"):
                levels[f"{prefix}_{suffix}"] = Level(
                    f"{prefix}_{suffix}", None, "NOT_AVAILABLE", "point",
                    reason=f"canonical {open_t.strftime('%H:%M')} ET bar absent for this trading day" if in_window
                    else f"outside validity window and no {open_t.strftime('%H:%M')} bar")
            return
        st = "AVAILABLE" if in_window else "NOT_IN_WINDOW"
        idx = past.index(bar) + 1
        formed = bar["ts"] + timedelta(minutes=15)
        levels[f"{prefix}_H"] = Level(f"{prefix}_H", bar["high"], st, "point", formed_ts=formed, valid_from_idx=idx,
                                      reason=None if in_window else "outside validity window")
        levels[f"{prefix}_L"] = Level(f"{prefix}_L", bar["low"], st, "point", formed_ts=formed, valid_from_idx=idx,
                                      reason=None if in_window else "outside validity window")

    _orb("NY_ORB", time(9, 30), time(9, 45), time(17, 0), today_bars)
    _orb("LDN_ORB", time(3, 0), time(3, 15), time(9, 30), today_bars)

    # 3.7 VWAP — CME trading-day anchored, hlc3 × volume
    num = den = 0.0
    vwap_ok = True
    for b in today_bars:
        v = b.get("volume")
        try:
            v = float(v) if v is not None else None
        except (TypeError, ValueError):
            v = None
        if v is None:
            vwap_ok = False
            break
        num += (b["high"] + b["low"] + b["close"]) / 3.0 * v
        den += v
    if today_bars and vwap_ok and den > 0:
        levels["VWAP"] = Level("VWAP", num / den, "AVAILABLE", "point", valid_from_idx=first_today_idx)
        _flag_gap(levels["VWAP"], past, datetime.combine(today - timedelta(days=1), time(18, 0), tzinfo=_ET), now + timedelta(minutes=15))
    else:
        levels["VWAP"] = Level("VWAP", None, "NOT_AVAILABLE", "point",
                               reason="no volume on a current-day bar" if today_bars else "no bars for the current trading day")

    # 4.1 LC_ZONE — identical code path to the live collector
    zones_raw: dict[str, Any] = {}
    for label, minutes, lookback in (("1h", 60, ZONE_LOOKBACK_BARS_1H), ("4h", 240, ZONE_LOOKBACK_BARS_4H)):
        agg = aggregate(past, minutes)[-lookback:]
        near = nearest_zones(detect_zones(agg, minutes), close)
        zones_raw[label] = near
        for kind in ("supply", "demand"):
            z = near.get(kind)
            name = f"LC_ZONE_{label.upper()}_{kind.upper()}"
            if z:
                levels[name] = Level(name, None, "AVAILABLE", "zone", top=z["top"], bottom=z["bottom"],
                                     formed_ts=datetime.fromisoformat(z["formed_ts"]), tests=z["tests"], broken=z["broken"])
                if agg:
                    _flag_gap(levels[name], past, agg[0]["ts"], now + timedelta(minutes=15))
            else:
                levels[name] = Level(name, None, "NOT_AVAILABLE", "zone", reason=f"no unbroken {kind} zone on {label}")

    return LevelSet(instrument=instrument, b0_ts=now, session=session, close=close, tick=tick,
                    mtr15=mtr15, levels=levels, zones_raw=zones_raw, n_bars=len(past), warnings=warnings)


# ── clean bar window helper (shared by events) ───────────────────────────────
def _clean_with_volume(bars15: list[dict]) -> list[dict]:
    """location_context._clean_bars drops volume; re-attach it by timestamp for VWAP."""
    clean = _clean_bars(bars15)
    vol: dict[datetime, Any] = {}
    for b in bars15 or []:
        try:
            ts = b["ts"] if isinstance(b["ts"], datetime) else datetime.fromisoformat(str(b["ts"]))
        except (KeyError, TypeError, ValueError):
            continue
        vol[ts] = b.get("volume")
    for b in clean:
        b["volume"] = vol.get(b["ts"])
    return clean


def _window(bars15: list[dict], b0_ts: Optional[datetime]) -> list[dict]:
    clean = _clean_with_volume(bars15)
    if b0_ts is not None:
        clean = [b for b in clean if b["ts"] <= b0_ts]
    return clean[-LIVE_BAR_WINDOW:]


# ── events (§5) ───────────────────────────────────────────────────────────────
def _side_sign(direction: str) -> int:
    return 1 if direction.upper() == "LONG" else -1


def _beyond(price: float, lvl: float, sign: int, tick: float = 0.0) -> bool:
    """True when ``price`` is on the trade-direction side of ``lvl`` by >= tick.
    For a supportive level of a LONG, 'beyond' = above (sign +1)."""
    return (price - lvl) * sign >= tick if tick else (price - lvl) * sign > 0


def event_touch(b0: dict, lvl: Level) -> bool:
    if lvl.kind == "zone":
        return b0["low"] <= lvl.top and b0["high"] >= lvl.bottom
    return b0["low"] <= lvl.value <= b0["high"]


def event_test_count(past: list[dict], lvl: Level) -> Optional[int]:
    """Bars from validity start to B−1 overlapping the level (zones: inherited ``tests``)."""
    if lvl.kind == "zone":
        return lvl.tests
    if lvl.valid_from_idx is None or lvl.value is None:
        return None
    n = 0
    for b in past[lvl.valid_from_idx:-1]:
        if b["low"] <= lvl.value <= b["high"]:
            n += 1
    return n


def event_age_hours(b0: dict, lvl: Level) -> Optional[float]:
    if lvl.formed_ts is None:
        return None
    return cme_open_hours(lvl.formed_ts, b0["ts"] + timedelta(minutes=15))


def event_wick_reject(b0: dict, lvl: Level, sign: int, tick: float, mtr: float) -> bool:
    """E3a: wick through a supportive level, close back on the trade side, depth <= D_max."""
    l = lvl.edge_facing(b0["close"])
    if l is None or mtr is None:
        return False
    if sign > 0:
        return b0["low"] <= l - tick and b0["close"] > l and (l - b0["low"]) <= D_MAX_MTR * mtr
    return b0["high"] >= l + tick and b0["close"] < l and (b0["high"] - l) <= D_MAX_MTR * mtr


def event_sweep_reclaim(past: list[dict], lvl: Level, sign: int, mtr: float) -> bool:
    """E3b: a run (<= K bars, ending at B−1) of closes on the far side, entered from the near
    side, containing a strict far-side close, max excursion <= D_max, and B0 closes back."""
    b0 = past[-1]
    l = lvl.edge_facing(b0["close"])
    if l is None or mtr is None or len(past) < 3:
        return False
    if not (b0["close"] - l) * sign > 0:
        return False
    run = 0
    strict = False
    ext = 0.0
    i = len(past) - 2
    while i >= 0 and (past[i]["close"] - l) * sign <= 0:
        run += 1
        if (past[i]["close"] - l) * sign < 0:
            strict = True
        exc = (l - past[i]["low"]) if sign > 0 else (past[i]["high"] - l)
        ext = max(ext, exc)
        i -= 1
    if run == 0 or run > K_SWEEP_BARS or not strict or i < 0:
        return False
    if not (past[i]["close"] - l) * sign > 0:   # must have come from the near side
        return False
    ext = max(ext, (l - b0["low"]) if sign > 0 else (b0["high"] - l))
    return ext <= D_MAX_MTR * mtr


def find_break(past: list[dict], lvl: Level, sign: int, tick: float, max_age: int) -> Optional[int]:
    """E4: most recent BREAK bar index j (B−j) within max_age whose closes since stayed
    beyond the level in the trade direction. None if no such bar."""
    l = lvl.value if lvl.kind == "point" else None
    if l is None:
        return None
    n = len(past)
    start = lvl.valid_from_idx or 0
    for j in range(0, max_age + 1):
        idx = n - 1 - j
        if idx <= start or idx < 1:
            break
        bj, bprev = past[idx], past[idx - 1]
        if _beyond(bj["close"], l, sign, tick) and not _beyond(bprev["close"], l, sign, tick):
            # closes B−j+1..B−1 must have held; B0 itself is classified by event_retest_state
            if all(_beyond(b["close"], l, sign, 0.0) for b in past[idx + 1:-1]):
                return j
            return None  # a close before B0 came back through → not a held break
    return None


def event_accept(past: list[dict], lvl: Level, sign: int, tick: float) -> bool:
    l = lvl.value
    if l is None or len(past) < N_ACCEPT:
        return False
    return all(_beyond(b["close"], l, sign, tick if i == 0 else 0.0) for i, b in enumerate(past[-N_ACCEPT:]))


def event_retest_state(past: list[dict], lvl: Level, sign: int, tick: float, mtr: float) -> dict:
    """E5: classify the held break on ``lvl`` (if any) into
    BREAK_RETEST_HOLD | BREAK_RETEST_REJECT | ACCEPT_NO_RETEST | RETESTED_EARLIER | IMMEDIATE | none."""
    out = {"state": "none", "break_age": None}
    if mtr is None or lvl.value is None:
        return out
    j = find_break(past, lvl, sign, tick, R_RETEST_BARS)
    if j is None:
        return out
    out["break_age"] = j
    if j < 2:
        out["state"] = "IMMEDIATE"
        return out
    l, tau = lvl.value, TAU_MTR * mtr
    n = len(past)

    def within(b: dict) -> bool:
        return (b["low"] <= l + tau) if sign > 0 else (b["high"] >= l - tau)

    mid = past[n - j:n - 1]          # B−j+1 .. B−1
    if any(within(b) for b in mid):
        out["state"] = "RETESTED_EARLIER"
        return out
    b0 = past[-1]
    if within(b0):
        out["state"] = "BREAK_RETEST_HOLD" if _beyond(b0["close"], l, sign, 0.0) else "BREAK_RETEST_REJECT"
    elif _beyond(b0["close"], l, sign, 0.0):
        out["state"] = "ACCEPT_NO_RETEST"
    else:
        out["state"] = "BACK_THROUGH_NO_RETEST"   # gapped back through without touching τ; neither T nor F
    return out


def event_proximity_only(b0: dict, lvl: Level, sign: int, mtr: float) -> bool:
    """E6: supportive level within PROX band on the trade side, not touched."""
    l = lvl.edge_facing(b0["close"])
    if l is None or mtr is None or event_touch(b0, lvl):
        return False
    d = (b0["close"] - l) * sign
    return 0 < d <= PROX_MTR * mtr


def event_dist(b0: dict, lvl: Level, sign: int, mtr: float) -> Optional[float]:
    l = lvl.edge_facing(b0["close"])
    if l is None or not mtr:
        return None
    return round((b0["close"] - l) * sign / mtr, 4)


# ── §5.1 reduction: anchors ───────────────────────────────────────────────────
def _admitted(ls: LevelSet, names: tuple[str, ...], family: str) -> list[Level]:
    excl = set(OWN_LEVEL_EXCLUSION.get(family, ()))
    out = []
    for n in names:
        if n in excl:
            continue
        lv = ls.levels.get(n)
        if lv is None or lv.status != "AVAILABLE" or lv.exploratory:
            continue
        if lv.kind == "point" and lv.value is None:
            continue
        if lv.kind == "zone" and (lv.top is None or lv.bottom is None):
            continue
        out.append(lv)
    return out


def _supportive(lv: Level, entry: float, sign: int) -> bool:
    """Supportive = at/below entry for LONG (zones: entirely at/below), mirrored for SHORT."""
    if lv.kind == "zone":
        return (lv.top <= entry) if sign > 0 else (lv.bottom >= entry)
    return (lv.value <= entry) if sign > 0 else (lv.value >= entry)


def _opposing(lv: Level, entry: float, sign: int) -> bool:
    if lv.kind == "zone":
        return (lv.bottom >= entry) if sign > 0 else (lv.top <= entry)
    return (lv.value >= entry) if sign > 0 else (lv.value <= entry)


def _dist_to_entry(lv: Level, entry: float, tick: float) -> float:
    e = lv.edge_facing(entry)
    return round(abs(entry - e) / tick) * tick


def select_anchor(ls: LevelSet, names: tuple[str, ...], family: str, entry: float, sign: int,
                  side: str = "supportive") -> Optional[Level]:
    """A_H: nearest admitted level on the given side of entry; ties by TIE_PRECEDENCE, then
    lower price for LONG / higher for SHORT. Geometry only."""
    pred = _supportive if side == "supportive" else _opposing
    cands = [lv for lv in _admitted(ls, names, family) if pred(lv, entry, sign)]
    if not cands:
        return None

    def key(lv: Level):
        price = lv.edge_facing(entry)
        return (_dist_to_entry(lv, entry, ls.tick), TIE_PRECEDENCE.get(lv.name, 99), price * sign)

    return min(cands, key=key)


def select_broken_anchor(ls: LevelSet, past: list[dict], family: str, entry: float, sign: int) -> Optional[tuple[Level, dict]]:
    """A_2: nearest eligible broken MAJOR level (trade-direction break, age 2..R, closes held)."""
    best = None
    for lv in _admitted(ls, MAJOR, family):
        if not _supportive(lv, entry, sign):
            continue
        st = event_retest_state(past, lv, sign, ls.tick, ls.mtr15)
        if st["state"] in ("BREAK_RETEST_HOLD", "BREAK_RETEST_REJECT", "ACCEPT_NO_RETEST", "RETESTED_EARLIER", "BACK_THROUGH_NO_RETEST"):
            k = (_dist_to_entry(lv, entry, ls.tick), TIE_PRECEDENCE.get(lv.name, 99), lv.value * sign)
            if best is None or k < best[0]:
                best = (k, lv, st)
    return None if best is None else (best[1], best[2])


# ── cluster / room (E8, E9) ───────────────────────────────────────────────────
def cluster_count(ls: LevelSet, anchor: Level, family: str, sign: int) -> int:
    """Distinct admitted H5-set levels within ± PROX_MTR × MTR15 of the anchor's facing edge
    (anchor included). Tautology removal: HOD/LOD are not in the set; ONH/ONL only when
    frozen (RTH), so the pre-09:30 ONH≡HOD identity cannot double-count."""
    a = anchor.edge_facing(ls.close)
    band = PROX_MTR * ls.mtr15
    n = 0
    for lv in _admitted(ls, H_LEVEL_SETS["H5"], family):
        if lv.kind == "zone":
            if lv.bottom <= a + band and lv.top >= a - band:
                n += 1
        elif abs(lv.value - a) <= band:
            n += 1
    return n


def room_to_opposing(ls: LevelSet, family: str, entry: float, stop: float, target: Optional[float], sign: int) -> dict:
    opp = select_anchor(ls, H_LEVEL_SETS["H6"], family, entry, sign, side="opposing")
    if opp is None:
        return {"opposing": None, "room_points": None, "room_R": None, "target_rel": None}
    edge = opp.edge_facing(entry)
    room = (edge - entry) * sign
    risk = abs(entry - stop)
    rel = None
    if target is not None and ls.mtr15:
        tau = TAU_MTR * ls.mtr15
        tdist = (target - entry) * sign
        if tdist < room - tau:
            rel = "before"
        elif tdist > room + tau:
            rel = "beyond"
        else:
            rel = "inside"
    return {"opposing": opp.name, "room_points": round(room, 4),
            "room_R": round(room / risk, 4) if risk else None, "target_rel": rel}


# ── candidate labelling (§6) ─────────────────────────────────────────────────
def label_candidate(bars15: list[dict], instrument: str, *, direction: str, entry: float, stop: float,
                    target: Optional[float], strategy: Optional[str], b0_ts: Optional[datetime] = None,
                    level_set: Optional[LevelSet] = None) -> dict:
    """Per-hypothesis labels for one existing candidate. Never reads an outcome."""
    past = _window(bars15, b0_ts)
    ls = level_set or build_levels(bars15, instrument, b0_ts=b0_ts)
    b0 = past[-1]
    sign = _side_sign(direction)
    family = family_of(strategy)
    mtr, tick = ls.mtr15, ls.tick
    out: dict[str, Any] = {"family": family, "direction": direction.upper(), "session": ls.session,
                           "mtr15": mtr, "hypotheses": {}}
    if not mtr:
        for h in H_LEVEL_SETS:
            out["hypotheses"][h] = {"label": "NOT_AVAILABLE", "reason": "MTR15 unavailable"}
        return out

    def na(h: str, reason: str, **extra: Any) -> None:
        out["hypotheses"][h] = {"label": "NOT_APPLICABLE", "reason": reason, **extra}

    # H1 — sweep→reclaim vs plain touch on A_H (MAJOR)
    a = select_anchor(ls, H_LEVEL_SETS["H1"], family, entry, sign)
    if a is None:
        na("H1", "no admitted supportive MAJOR level")
    else:
        sweep = event_wick_reject(b0, a, sign, tick, mtr) or event_sweep_reclaim(past, a, sign, mtr)
        touch = event_touch(b0, a)
        if sweep:
            out["hypotheses"]["H1"] = {"label": "T", "anchor": a.name}
        elif touch:
            out["hypotheses"]["H1"] = {"label": "F", "anchor": a.name}
        else:
            na("H1", "anchor neither swept/reclaimed nor touched", anchor=a.name)

    # H2 — break→retest→hold vs age-matched accept-no-retest on A_2
    ba = select_broken_anchor(ls, past, family, entry, sign)
    if ba is None:
        na("H2", "no eligible broken MAJOR level (age 2..R, closes held)")
    else:
        lv, st = ba
        if st["state"] == "BREAK_RETEST_HOLD":
            out["hypotheses"]["H2"] = {"label": "T", "anchor": lv.name, "break_age": st["break_age"]}
        elif st["state"] == "ACCEPT_NO_RETEST":
            out["hypotheses"]["H2"] = {"label": "F", "anchor": lv.name, "break_age": st["break_age"]}
        else:
            na("H2", st["state"].lower(), anchor=lv.name, break_age=st["break_age"])

    # H3 — wick-reject vs proximity-only on A_H (MAJOR ∪ LC_ZONE)
    a = select_anchor(ls, H_LEVEL_SETS["H3"], family, entry, sign)
    if a is None:
        na("H3", "no admitted supportive level")
    elif event_wick_reject(b0, a, sign, tick, mtr):
        out["hypotheses"]["H3"] = {"label": "T", "anchor": a.name}
    elif event_proximity_only(b0, a, sign, mtr):
        out["hypotheses"]["H3"] = {"label": "F", "anchor": a.name}
    elif event_touch(b0, a):
        na("H3", "touch_and_close_through_or_plain_touch (descriptive third group)", anchor=a.name)
    else:
        na("H3", "anchor beyond proximity band", anchor=a.name)

    # H4 — test count / age on A_H (MAJOR ∪ LC_ZONE), among touch or proximity
    a = select_anchor(ls, H_LEVEL_SETS["H4"], family, entry, sign)
    if a is None:
        na("H4", "no admitted supportive level")
    elif event_touch(b0, a) or event_proximity_only(b0, a, sign, mtr):
        out["hypotheses"]["H4"] = {"label": "APPLICABLE", "anchor": a.name,
                                   "test_count": event_test_count(past, a), "age_hours": event_age_hours(b0, a)}
    else:
        na("H4", "anchor neither touched nor within proximity band", anchor=a.name)

    # H5 — cluster on A_H (H5 set) within relevance band
    a = select_anchor(ls, H_LEVEL_SETS["H5"], family, entry, sign)
    if a is None:
        na("H5", "no admitted supportive level")
    elif _dist_to_entry(a, entry, tick) > PROX_MTR * mtr:
        na("H5", "anchor beyond relevance band", anchor=a.name, dist_mtr=round(_dist_to_entry(a, entry, tick) / mtr, 4))
    else:
        c = cluster_count(ls, a, family, sign)
        out["hypotheses"]["H5"] = {"label": "T" if c >= 2 else "F", "anchor": a.name, "cluster": c}

    # H6 — room / target relation
    r = room_to_opposing(ls, family, entry, stop, target, sign)
    if r["opposing"] is None:
        na("H6", "no admitted opposing level")
    elif r["target_rel"] is None:
        na("H6", "no target on candidate")
    elif r["target_rel"] == "inside":
        na("H6", "target inside ± tau of the opposing level", **r)
    else:
        out["hypotheses"]["H6"] = {"label": "T" if r["target_rel"] == "before" else "F", **r}
    return out
