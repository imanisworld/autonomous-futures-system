"""Premarket location-context observation collector.

Operator-approved 2026-07-16, OBSERVATION ONLY: journals supply/demand and
key-level location evidence beside every candidate (normal, shadow, paper) so
the morning-loss location hypothesis can be tested on a real sample. Nothing
in this module gates, blocks, authorizes, routes, or sizes a trade, and the
runner wraps every call in fail-soft try/except — a failure here can never
affect ingestion or decisions.

Two layers:
  * build_location_context(...)      — bar-level, computed once per decision
    bar: previous trading day OHLC, overnight/premarket ranges, nearest fresh
    1H/4H supply & demand zones (aggregated from 15m bar history), price
    location classification, middle-of-range flag, cross-instrument regime,
    impulse-phase classification.
  * candidate_location(...)          — per candidate: direction-vs-zone
    alignment, distance to the opposing zone, target-blocked-by-zone flag.

Zone definition (v1, documented deliberately): a zone is the base bar
immediately preceding an impulse bar (|body| >= IMPULSE_BODY_X * median true
range on that timeframe). A strong move UP leaves a demand zone at its
origin; a strong move DOWN leaves a supply zone. A zone is broken once a
later close passes its far edge; tests count later bars overlapping the zone
(skipping the impulse bar itself); fresh means zero tests. These heuristics
are observational v1 — calibrate at review time, not by editing live gates.

Regime persistence at +15/+30/+60 minutes is intentionally NOT collected at
decision time (it cannot exist yet, and must never leak forward information).
The journal already records market_condition every 15 minutes, so the review
computes it offline via regime_persistence() below.

Trading-day convention: CME micro sessions roll at 18:00 ET, so bars are
bucketed into trading days by (ET time + 6h).date(). "Previous day" is the
last completed trading day; "overnight" runs from the current day's reopen to
the earlier of now / 09:30 ET; "premarket" is 04:00–09:30 ET when reached.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional
from zoneinfo import ZoneInfo

import json

_ET = ZoneInfo("America/New_York")

# v1 heuristics (observation-only; tune at review, never silently)
IMPULSE_BODY_X = 1.2          # impulse bar body >= this x median true range
APPROACH_MTR_X = 0.5          # "approaching" a zone = within this x MTR of edge
ZONE_LOOKBACK_BARS_1H = 120   # ~10 trading days of 1H bars
ZONE_LOOKBACK_BARS_4H = 60    # ~10 trading days of 4H bars
IMPULSE_WINDOW_BARS_15M = 8   # 2h of 15m bars for impulse-phase classification
OTHER_REGIME_MAX_AGE_S = 1800  # cross-instrument regime older than this = stale


def _parse_ts(value) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _clean_bars(bars15: List[dict]) -> List[dict]:
    out = []
    for b in bars15 or []:
        ts = _parse_ts(b.get("ts"))
        try:
            o, h, l, c = (float(b["open"]), float(b["high"]),
                          float(b["low"]), float(b["close"]))
        except (KeyError, TypeError, ValueError):
            continue
        if ts is None:
            continue
        out.append({"ts": ts, "open": o, "high": h, "low": l, "close": c})
    out.sort(key=lambda b: b["ts"])
    return out


def _trading_day(ts: datetime):
    """CME trading-day key: sessions roll at 18:00 ET."""
    return (ts.astimezone(_ET) + timedelta(hours=6)).date()


def aggregate(bars15: List[dict], minutes: int) -> List[dict]:
    """Aggregate clean 15m bars into fixed `minutes` buckets (epoch-aligned)."""
    buckets: dict = {}
    for b in bars15:
        key = int(b["ts"].timestamp()) // (minutes * 60)
        cur = buckets.get(key)
        if cur is None:
            buckets[key] = dict(b)
        else:
            cur["high"] = max(cur["high"], b["high"])
            cur["low"] = min(cur["low"], b["low"])
            cur["close"] = b["close"]
    return [buckets[k] for k in sorted(buckets)]


def _median_true_range(bars: List[dict]) -> Optional[float]:
    trs = []
    for prev, cur in zip(bars, bars[1:]):
        trs.append(max(cur["high"] - cur["low"],
                       abs(cur["high"] - prev["close"]),
                       abs(cur["low"] - prev["close"])))
    if not trs:
        return None
    trs.sort()
    mid = len(trs) // 2
    return trs[mid] if len(trs) % 2 else (trs[mid - 1] + trs[mid]) / 2


def detect_zones(bars: List[dict], timeframe_minutes: int) -> List[dict]:
    """v1 zone scan (see module docstring). Returns zones oldest→newest."""
    if len(bars) < 3:
        return []
    mtr = _median_true_range(bars)
    if not mtr:
        return []
    zones = []
    for i in range(1, len(bars)):
        imp = bars[i]
        body = imp["close"] - imp["open"]
        if abs(body) < IMPULSE_BODY_X * mtr:
            continue
        base = bars[i - 1]
        zones.append({
            "kind": "demand" if body > 0 else "supply",
            "top": base["high"],
            "bottom": base["low"],
            "formed_ts": base["ts"],
            "timeframe_minutes": timeframe_minutes,
            "_formed_idx": i - 1,
        })
    # post-formation life: broken / test count (skip the impulse bar itself)
    for z in zones:
        tests, broken = 0, False
        for b in bars[z["_formed_idx"] + 2:]:
            if broken:
                break
            if z["kind"] == "supply" and b["close"] > z["top"]:
                broken = True
                continue
            if z["kind"] == "demand" and b["close"] < z["bottom"]:
                broken = True
                continue
            if b["low"] <= z["top"] and b["high"] >= z["bottom"]:
                tests += 1
        z["tests"] = tests
        z["fresh"] = tests == 0
        z["broken"] = broken
        del z["_formed_idx"]
    return zones


def nearest_zones(zones: List[dict], price: float) -> dict:
    """Nearest unbroken supply at/above price and demand at/below price."""
    supply = demand = None
    for z in zones:
        if z["broken"]:
            continue
        if z["kind"] == "supply" and (z["top"] >= price):
            d = max(0.0, z["bottom"] - price)
            if supply is None or d < supply["_d"]:
                supply = {**z, "_d": d}
        elif z["kind"] == "demand" and (z["bottom"] <= price):
            d = max(0.0, price - z["top"])
            if demand is None or d < demand["_d"]:
                demand = {**z, "_d": d}
    for z in (supply, demand):
        if z is not None:
            z["distance_points"] = round(z.pop("_d"), 4)
            z["formed_ts"] = z["formed_ts"].isoformat()
    return {"supply": supply, "demand": demand}


def _relation(near: dict, price: float, mtr: Optional[float]) -> str:
    """inside_supply | inside_demand | approaching_supply | approaching_demand
    | middle — for one timeframe's nearest zones."""
    sup, dem = near.get("supply"), near.get("demand")
    if sup and sup["bottom"] <= price <= sup["top"]:
        return "inside_supply"
    if dem and dem["bottom"] <= price <= dem["top"]:
        return "inside_demand"
    if mtr:
        if sup and 0 < sup["bottom"] - price <= APPROACH_MTR_X * mtr:
            return "approaching_supply"
        if dem and 0 < price - dem["top"] <= APPROACH_MTR_X * mtr:
            return "approaching_demand"
    return "middle"


def _day_ranges(bars15: List[dict], now: datetime) -> dict:
    today_key = _trading_day(now)
    by_day: dict = {}
    for b in bars15:
        by_day.setdefault(_trading_day(b["ts"]), []).append(b)
    prev_keys = sorted(k for k in by_day if k < today_key)
    prev = by_day.get(prev_keys[-1]) if prev_keys else None
    prev_day = None
    if prev:
        prev_day = {
            "high": max(b["high"] for b in prev),
            "low": min(b["low"] for b in prev),
            "open": prev[0]["open"],
            "close": prev[-1]["close"],
        }

    today = by_day.get(today_key) or []
    now_et = now.astimezone(_ET)
    # overnight: reopen → min(now, 09:30 ET)
    rth_open_et = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
    on_bars = [b for b in today
               if b["ts"] <= now and b["ts"].astimezone(_ET) < rth_open_et]
    overnight = None
    if on_bars:
        overnight = {"high": max(b["high"] for b in on_bars),
                     "low": min(b["low"] for b in on_bars)}
    # premarket: 04:00–09:30 ET (only once reached)
    pm_start_et = now_et.replace(hour=4, minute=0, second=0, microsecond=0)
    premarket = None
    if now_et >= pm_start_et:
        pm_bars = [b for b in on_bars
                   if b["ts"].astimezone(_ET) >= pm_start_et]
        if pm_bars:
            premarket = {"high": max(b["high"] for b in pm_bars),
                         "low": min(b["low"] for b in pm_bars)}
    return {"prev_day": prev_day, "overnight": overnight,
            "premarket": premarket}


def _impulse_phase(bars15: List[dict], now: datetime,
                   mtr15: Optional[float]) -> dict:
    """pre_impulse | developing_impulse | late_entry (v1, documented)."""
    past = [b for b in bars15 if b["ts"] <= now]
    w = IMPULSE_WINDOW_BARS_15M
    if len(past) < 2 * w or not mtr15:
        return {"phase": None, "reason": "insufficient bars"}
    recent, prior = past[-w:], past[-2 * w:-w]
    move = recent[-1]["close"] - recent[0]["open"]
    prior_range = max(b["high"] for b in prior) - min(b["low"] for b in prior)
    if abs(move) > max(prior_range, 3 * mtr15):
        phase = "late_entry"          # the expansion already happened
    elif abs(move) >= 1.5 * mtr15:
        phase = "developing_impulse"  # expansion under way
    else:
        phase = "pre_impulse"         # no expansion behind this signal
    return {"phase": phase, "move_2h_points": round(move, 4),
            "prior_2h_range_points": round(prior_range, 4),
            "mtr_15m_points": round(mtr15, 4)}


def read_other_instrument_regime(
    log_dir, instrument: str, now: datetime, for_date=None,
    tail_bytes: int = 262144,
) -> Optional[dict]:
    """Most recent journaled market_condition for the OTHER instrument (causal:
    reads only rows already written). None when absent or stale (>30 min)."""
    other = "MES" if instrument == "MNQ" else "MNQ"
    d = for_date or now.date()
    path = Path(log_dir) / f"journal_{d.isoformat()}.jsonl"
    try:
        with path.open("rb") as fh:
            fh.seek(max(0, path.stat().st_size - tail_bytes))
            tail = fh.read().decode("utf-8", errors="replace")
    except OSError:
        return None
    best = None
    for line in tail.splitlines():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get("instrument") != other or not r.get("market_condition"):
            continue
        ts = _parse_ts(r.get("ts"))
        if ts is None or ts > now:
            continue
        if best is None or ts > best[0]:
            best = (ts, r.get("market_condition"))
    if best is None:
        return None
    age = (now - best[0]).total_seconds()
    if age > OTHER_REGIME_MAX_AGE_S:
        return None
    return {"instrument": other, "market_condition": best[1],
            "age_seconds": int(age)}


def build_location_context(
    *,
    bars15: List[dict],
    price: float,
    now: datetime,
    market_condition: Optional[str],
    other_regime: Optional[dict],
) -> Optional[dict]:
    """Bar-level location context. Returns None when there is no usable data."""
    bars = _clean_bars(bars15)
    if not bars:
        return None
    ranges = _day_ranges(bars, now)
    past = [b for b in bars if b["ts"] <= now]
    mtr15 = _median_true_range(past[-64:])

    zones = {}
    for label, minutes, lookback in (
            ("1h", 60, ZONE_LOOKBACK_BARS_1H),
            ("4h", 240, ZONE_LOOKBACK_BARS_4H)):
        agg = aggregate(past, minutes)[-lookback:]
        near = nearest_zones(detect_zones(agg, minutes), price)
        near["relation"] = _relation(near, price, _median_true_range(agg))
        zones[label] = near

    middle = all(zones[tf]["relation"] == "middle" for tf in ("1h", "4h"))

    levels = {}
    if ranges["prev_day"]:
        levels.update({"pdh": ranges["prev_day"]["high"],
                       "pdl": ranges["prev_day"]["low"],
                       "prev_open": ranges["prev_day"]["open"],
                       "prev_close": ranges["prev_day"]["close"]})
    if ranges["overnight"]:
        levels.update({"onh": ranges["overnight"]["high"],
                       "onl": ranges["overnight"]["low"]})
    if ranges["premarket"]:
        levels.update({"pmh": ranges["premarket"]["high"],
                       "pml": ranges["premarket"]["low"]})
    nearest_level = None
    if levels:
        name = min(levels, key=lambda k: abs(price - levels[k]))
        nearest_level = {"name": name, "level": levels[name],
                         "distance_points": round(abs(price - levels[name]), 4)}

    return {
        "observed_price": price,
        "levels": levels or None,
        "nearest_key_level": nearest_level,
        "zones": zones,
        "middle_of_range": middle,
        "regime_at_signal": market_condition,
        "other_instrument": other_regime,
        "regime_agreement": (
            None if not other_regime or not market_condition
            else other_regime["market_condition"] == market_condition),
        "impulse": _impulse_phase(bars, now, mtr15),
        "mtr_15m_points": round(mtr15, 4) if mtr15 else None,
    }


def candidate_location(loc: Optional[dict], *, direction: Optional[str],
                       entry, target) -> Optional[dict]:
    """Per-candidate location fields. Pure read of the bar-level context."""
    if not loc or str(direction or "").upper() not in ("LONG", "SHORT"):
        return None
    direction = str(direction).upper()
    try:
        entry = float(entry)
    except (TypeError, ValueError):
        return None
    try:
        target = float(target) if target is not None else None
    except (TypeError, ValueError):
        target = None

    # alignment per timeframe: with demand = aligned LONG; with supply = aligned SHORT
    per_tf, overall = {}, "neutral"
    for tf in ("1h", "4h"):
        rel = loc["zones"][tf]["relation"]
        if rel in ("inside_demand", "approaching_demand"):
            per_tf[tf] = "aligned" if direction == "LONG" else "against"
        elif rel in ("inside_supply", "approaching_supply"):
            per_tf[tf] = "against" if direction == "LONG" else "aligned"
        else:
            per_tf[tf] = "neutral"
    if "against" in per_tf.values():
        overall = "against"
    elif "aligned" in per_tf.values():
        overall = "aligned"

    # opposing zone: the nearest one blocking the candidate's path
    opposing, room, blocked = None, None, None
    kind = "supply" if direction == "LONG" else "demand"
    cands = [loc["zones"][tf].get(kind) for tf in ("1h", "4h")]
    cands = [z for z in cands if z]
    if direction == "LONG":
        cands = [z for z in cands if z["top"] >= entry]
        cands.sort(key=lambda z: z["bottom"])
    else:
        cands = [z for z in cands if z["bottom"] <= entry]
        cands.sort(key=lambda z: -z["top"])
    if cands:
        z = cands[0]
        near_edge = z["bottom"] if direction == "LONG" else z["top"]
        opposing = {"kind": z["kind"], "top": z["top"], "bottom": z["bottom"],
                    "timeframe_minutes": z["timeframe_minutes"],
                    "fresh": z["fresh"], "tests": z["tests"]}
        room = round((near_edge - entry) if direction == "LONG"
                     else (entry - near_edge), 4)
        if target is not None:
            blocked = (target >= near_edge if direction == "LONG"
                       else target <= near_edge)

    return {
        "direction_zone_alignment": overall,
        "alignment_by_timeframe": per_tf,
        "opposing_zone": opposing,
        "room_to_opposing_points": room,
        "target_blocked_by_opposing_zone": blocked,
        "middle_of_range": loc["middle_of_range"],
    }


def regime_persistence(journal_path, instrument: str, signal_ts: datetime,
                       offsets_min=(15, 30, 60), tol_min: int = 8) -> dict:
    """OFFLINE review helper: market_condition at signal_ts + offsets, from the
    already-journaled decision rows. Never called on the live decision path."""
    rows = []
    p = Path(journal_path)
    if p.exists():
        for line in p.read_text().splitlines():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("instrument") != instrument or not r.get("market_condition"):
                continue
            ts = _parse_ts(r.get("ts"))
            if ts is not None:
                rows.append((ts, r["market_condition"]))
    out = {}
    for off in offsets_min:
        when = signal_ts + timedelta(minutes=off)
        best = None
        for ts, cond in rows:
            d = abs((ts - when).total_seconds())
            if d <= tol_min * 60 and (best is None or d < best[0]):
                best = (d, cond)
        out[f"+{off}m"] = best[1] if best else None
    return out
