"""Canonical executable state machine for the 12HR Miyagi 1-3-1 reversal.

Rules: docs/strategy-rules/12HR_Miyagi_Rules.md
Evidence: docs/strategy-rules/12HR_MIYAGI_CANONICAL_EVIDENCE_2026-07-26.md
          (the CODED-DETECTOR canonical study, PR #343 — MNQ 15 candidates
          / 8 fills / 7W-1L / net $516.33 / PF 2.81; MES 19 candidates /
          10 fills / 8W-2L / net $198.85 / PF 1.98. NOT the rules doc's own
          §9 "VALIDATED PERFORMANCE" table (n=13/92.3%/+$102.35,
          n=20/75.0%/+$25.78) — that table is the OLDER, pre-coded-detector
          manual study, explicitly labeled "non-reproducible manual study"
          in the canonical evidence doc's own §0 provenance section. Same
          stale-legacy-figure trap as 60M 3-2-2's rules doc had — caught
          here by actually reproducing the pinned command before citing
          any number.)

The module is deliberately pure: callers provide already-arrived 5-minute
bars and the last persisted state, and receive the next state plus an
optional entry candidate — same contract as strategy/four_hr_retrigger.py
and strategy/strat_322_first_live.py, whose helpers this module reuses
directly (``_as_bar``, ``aggregate_et_bars``, ``_completed_one_hour_stop``).

HEADLINE FINDING (2026-07-27, demo-readiness build) — the evidence's own
stop-reference formula has a confirmed lookahead defect, NOT replicated
here:

``research/detector_12hr_miyagi.py``'s Step 7 computes the stop reference
by filtering 60-minute bars to ``ts < 9:30`` and taking the last one. A
60-minute bucket LABELED "09:00" covers [09:00, 10:00) ET — it is NOT
closed until 10:00, so filtering by "ts < 9:30" does not exclude it (09:00
< 09:30 is True). Verified empirically against real MNQ signals
(2024-08-22, 2024-08-23, 2024-09-18): every one resolved
``stop_reference_bar_ts`` to exactly ``09:00:00`` ET — a bar whose true
high/low, as read from an offline full-day resample, includes price action
from 09:30-10:00 that has not happened yet at the 9:30 decision point.
This directly contradicts ``research/bars_12hr_miyagi_loader.py``'s own
docstring claim ("The stop-reference bar (8-9 AM ET)"), which describes
the INTENDED behavior, not what the code actually does.

The rules doc's own text says the stop is set "AT THE MOMENT OF ENTRY...
the most recently COMPLETED 60-minute candle before your entry time" — not
"at 9:30 confirmation time". This module follows that literal text: the
stop is computed causally, fresh, at the moment the trigger actually
crosses (via ``_completed_one_hour_stop``, the exact helper
``strategy/four_hr_retrigger.py`` already uses for the same "last
completed hour before entry" pattern) — never a value that depends on a
bar which has not yet closed.

CONSEQUENCE: live stop values, and therefore R:R and every trade's dollar
outcome, will systematically DIFFER from the evidenced $102.35/$25.78
expectancy numbers, which were computed against a stop level the live
system cannot ever legally know at decision time. This is not an edge
case — it affects the stop of every single trade this strategy takes. The
evidenced performance numbers do not describe what this live build will
produce; they describe what the (lookahead-affected) offline detector
would have produced. Flagged prominently, not silently patched or hidden.

Entry model (unaffected, verified against the honest-fill replay that
produced the evidence): no IOC cap, no gap-open special case — Miyagi
fills at the exact trigger price whenever any 5-minute bar's high/low
crosses it, in the entry window [9:30, 16:00) ET. A gap-through-the-
trigger-at-9:30 is structurally impossible (the detector's own Step 6
already requires price to be strictly beyond Bar C's boundary at 9:30, on
the OPPOSITE side from the trigger), matching
``research/replay_12hr_miyagi_honest_fill.py``'s documented reasoning.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

from strategy.four_hr_retrigger import _as_bar, _completed_one_hour_stop, aggregate_et_bars

ET = ZoneInfo("America/New_York")
_SUPPORTED = {"MNQ", "MES"}  # rules doc §11: MNQ preferred, MES also validated


def _at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=ET)


def _exact(bars: list[dict], target: datetime) -> Optional[dict]:
    return next((bar for bar in bars if bar["ts"] == target), None)


def _terminal(day: date, reason: str, *, status: str = "INVALIDATED") -> dict:
    return {
        "trading_date": day.isoformat(),
        "status": status,
        "invalidation": reason,
    }


def _bucket_start_12h(local_dt: datetime) -> datetime:
    """4AM/4PM-ET-anchored 12-hour bucket start (ports
    research/bars_12hr_miyagi_loader.py::_bucket_start_12h verbatim, adapted
    for a live 5-minute-native stream rather than an offline 15-minute
    resample — the anchoring rule itself is data-source-independent)."""
    hour = local_dt.hour
    if 4 <= hour < 16:
        return local_dt.replace(hour=4, minute=0, second=0, microsecond=0)
    if hour >= 16:
        return local_dt.replace(hour=16, minute=0, second=0, microsecond=0)
    prior = local_dt - timedelta(days=1)
    return prior.replace(hour=16, minute=0, second=0, microsecond=0)


def _aggregate_12h(bars_5m: list[dict]) -> list[dict]:
    """Aggregate causal 5-minute bars into 4AM/4PM-ET-anchored 12h bars —
    the live-native analog of aggregate_et_bars(bars, 240), which only
    supports 60/240-minute modular buckets and cannot express 4AM/4PM
    anchoring (a plain ``minutes // 720`` bucket would anchor at midnight/
    noon, not 4AM/4PM)."""
    buckets: dict[datetime, dict] = {}
    for bar in bars_5m:
        start = _bucket_start_12h(bar["ts"])
        current = buckets.get(start)
        if current is None:
            buckets[start] = {
                "ts": start,
                "open": bar["open"],
                "high": bar["high"],
                "low": bar["low"],
                "close": bar["close"],
            }
            continue
        current["high"] = max(current["high"], bar["high"])
        current["low"] = min(current["low"], bar["low"])
        current["close"] = bar["close"]
    return [buckets[key] for key in sorted(buckets)]


def advance_strat_12hr_miyagi(
    *,
    bars_5m: Iterable[dict],
    current_bar_ts: datetime,
    instrument: str,
    persisted_state: Optional[dict] = None,
) -> tuple[dict, Optional[dict]]:
    """Advance one completed 5-minute evaluation.

    ``current_bar_ts`` is the current bar's open timestamp; entry time is
    its close timestamp (same convention as advance_4hr_retrigger /
    advance_strat_322_first_live).
    """
    root = str(instrument or "").upper().replace("1!", "").replace("2!", "")
    current_open = current_bar_ts.astimezone(ET)
    day = current_open.date()
    if root not in _SUPPORTED:
        return _terminal(day, "UNSUPPORTED_INSTRUMENT"), None

    previous = dict(persisted_state or {})
    if previous.get("trading_date") != day.isoformat():
        previous = {}

    available: list[dict] = []
    current_close = current_open + timedelta(minutes=5)
    for raw in bars_5m:
        bar = _as_bar(raw)
        if bar is not None and bar["ts"] + timedelta(minutes=5) <= current_close:
            available.append(bar)
    available.sort(key=lambda bar: bar["ts"])

    status = previous.get("status")
    if status in {"TRIGGERED", "INVALIDATED", "EXPIRED"}:
        return previous, None

    window_open = _at(day, 9, 30)
    day_close = _at(day, 16, 0)

    if status != "ARMED":
        # Setup detection (1-3-1 pattern + premarket integrity + 9:30
        # direction confirmation) evaluates EXACTLY ONCE, at exactly
        # current_open == window_open — the same one-shot exact-boundary
        # gate strat_322_first_live's own setup detection uses, for the
        # identical reason: evaluating incrementally on bars leading up to
        # 9:30 would read a PARTIAL premarket window and could latch a
        # spurious CANDLE3_BECAME_OUTSIDE_BAR before the window closes.
        if current_open < window_open:
            return {
                "trading_date": day.isoformat(),
                "status": "FORMING",
                "invalidation": None,
            }, None
        if current_open > window_open:
            return _terminal(day, "SETUP_NOT_ESTABLISHED_BY_0930"), None

        bars_12h = _aggregate_12h(available)
        bar_d = _exact(bars_12h, _at(day, 4, 0))
        bar_c = _exact(bars_12h, _at(day - timedelta(days=1), 16, 0))
        bar_b = _exact(bars_12h, _at(day - timedelta(days=1), 4, 0))
        bar_a = _exact(bars_12h, _at(day - timedelta(days=2), 16, 0))
        bar_z = _exact(bars_12h, _at(day - timedelta(days=2), 4, 0))
        if any(bar is None for bar in (bar_a, bar_b, bar_c, bar_d, bar_z)):
            return _terminal(day, "REFERENCE_DATA_MISSING"), None

        # Step 1: Bar C (inside) must sit inside Bar B (outside).
        if not (bar_c["high"] <= bar_b["high"] and bar_c["low"] >= bar_b["low"]):
            return _terminal(day, "BAR_C_NOT_INSIDE_BAR_B"), None
        # Step 2: Bar B must be an outside bar relative to Bar A.
        if not (bar_b["high"] > bar_a["high"] and bar_b["low"] < bar_a["low"]):
            return _terminal(day, "BAR_B_NOT_OUTSIDE_BAR_A"), None
        # Step 3: Bar A (inside) must sit inside Bar Z.
        if not (bar_a["high"] <= bar_z["high"] and bar_a["low"] >= bar_z["low"]):
            return _terminal(day, "BAR_A_NOT_INSIDE_BAR_Z"), None

        trigger = (bar_c["high"] + bar_c["low"]) / 2.0

        # Step 5: single-bar engulf test on the 4:00-9:30 ET premarket
        # window — a bar whose OWN high AND low both breach Bar C's range
        # voids the setup (not a window-aggregate breach).
        premarket = [
            bar for bar in available
            if _at(day, 4, 0) <= bar["ts"] < window_open
        ]
        for bar in premarket:
            if bar["high"] > bar_c["high"] and bar["low"] < bar_c["low"]:
                return _terminal(day, "CANDLE3_BECAME_OUTSIDE_BAR"), None

        # Step 6: direction confirmed ONLY from the exact 9:30 bar's OPEN —
        # never from intrabar premarket movement.
        nine_thirty = _exact(available, window_open)
        if nine_thirty is None:
            return _terminal(day, "NINE_THIRTY_BAR_MISSING"), None
        price_at_open = nine_thirty["open"]
        if price_at_open > bar_c["high"]:
            direction = "SHORT"
        elif price_at_open < bar_c["low"]:
            direction = "LONG"
        else:
            return _terminal(day, "PRICE_INSIDE_TRIGGER_RANGE_AT_0930"), None

        target = bar_c["low"] if direction == "SHORT" else bar_c["high"]
        target_2 = bar_b["low"] if direction == "SHORT" else bar_b["high"]

        previous = {
            "trading_date": day.isoformat(),
            "status": "ARMED",
            "direction": direction,
            "trigger": trigger,
            "target": target,
            "target_2": target_2,
            "bar_c_high": bar_c["high"],
            "bar_c_low": bar_c["low"],
            "bar_b_high": bar_b["high"],
            "bar_b_low": bar_b["low"],
            "setup_bar_ts": bar_d["ts"].isoformat(),
            "expires_at": day_close.isoformat(),
            "invalidation": None,
        }

    if current_open >= day_close:
        expired = dict(previous)
        expired.update(status="EXPIRED", invalidation="TRIGGER_NOT_HIT_BY_DAY_CLOSE")
        return expired, None

    current = _exact(available, current_open)
    if current is None:
        return previous, None
    direction = str(previous["direction"])
    trigger = float(previous["trigger"])
    triggered = (
        current["high"] >= trigger if direction == "LONG" else current["low"] <= trigger
    )
    if not triggered:
        return previous, None

    # Stop: causally correct, computed AT THE MOMENT OF THIS ENTRY (not a
    # value snapshotted at 9:30) — see the module docstring's headline
    # finding for why the evidence's own stop reference cannot be
    # replicated live.
    bars_60m = aggregate_et_bars(available, 60)
    stop, stop_bar_ts = _completed_one_hour_stop(bars_60m, current_close, direction)
    if stop is None or stop_bar_ts is None:
        invalid = dict(previous)
        invalid.update(status="INVALIDATED", invalidation="COMPLETED_1H_STOP_MISSING")
        return invalid, None

    target = float(previous["target"])
    bracket_valid = (
        stop < trigger < target if direction == "LONG" else target < trigger < stop
    )
    if not bracket_valid:
        invalid = dict(previous)
        invalid.update(status="INVALIDATED", invalidation="INVALID_ENTRY_BRACKET")
        return invalid, None

    triggered_state = dict(previous)
    triggered_state.update(
        status="TRIGGERED",
        entry_time=current_close.isoformat(),
        stop=stop,
        stop_bar_ts=stop_bar_ts.isoformat(),
    )
    candidate = {
        "direction": direction,
        "entry": trigger,
        "stop": stop,
        "target": target,
        "entry_time": current_close,
        "stop_bar_ts": stop_bar_ts,
        "state": triggered_state,
    }
    return triggered_state, candidate
