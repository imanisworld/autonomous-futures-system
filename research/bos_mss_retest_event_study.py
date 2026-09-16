"""Causal BOS/MSS -> first-retest event study primitives.

Research-only. No strategy, risk, broker, webhook, env, or deployment wiring.

This module intentionally studies events, not trades:
- Recreate the repo's RiskSentinel swing-break concept causally from OHLC.
- BOS = break in the same direction as the previous confirmed break.
- MSS = break opposite the previous confirmed break.
- FIRST_BREAK establishes direction but is not scored as BOS/MSS.
- First retest = first subsequent 5m bar that touches the broken swing level.
  A close back on the breakout side is RETEST_HOLD; otherwise RETEST_FAIL.
- Forward measurements are descriptive excursions/returns only. No bracket,
  P&L, commission, or promotion claim is produced here.

CHoCH is deliberately NOT claimed. The repository has no canonical CHoCH
definition; MSS is the existing transparent structure-shift label.
"""
from __future__ import annotations

from bisect import bisect_left
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import statistics
from typing import Iterable, Optional

DEFAULT_SWING = 7
DEFAULT_HORIZONS_MINUTES = (15, 30, 60, 120)
RETEST_HOLD = "RETEST_HOLD"
RETEST_FAIL = "RETEST_FAIL"
NO_RETEST = "NO_RETEST"


def parse_ts(value: object) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise ValueError("timestamp is required")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid ISO timestamp: {text}") from exc
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a UTC offset")
    return parsed.astimezone(timezone.utc)


def row_ts(row: dict) -> datetime:
    return parse_ts(row.get("timestamp") or row.get("ts"))


def _price(row: dict, key: str) -> float:
    value = row.get(key)
    if isinstance(value, bool):
        raise ValueError(f"{key} must be numeric")
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} must be numeric") from exc


def _bucket_15m(ts: datetime) -> datetime:
    return ts.replace(minute=(ts.minute // 15) * 15, second=0, microsecond=0)


def _prepare_fine(rows: Iterable[dict]) -> tuple[list[dict], list[datetime]]:
    ordered = sorted((dict(row) for row in rows), key=row_ts)
    times = [row_ts(row) for row in ordered]
    return ordered, times


def _fine_window(
    ordered: list[dict],
    times: list[datetime],
    start: datetime,
    end: datetime,
) -> list[dict]:
    left = bisect_left(times, start)
    right = bisect_left(times, end)
    return ordered[left:right]


def aggregate_5m_to_15m(rows: Iterable[dict]) -> tuple[list[dict], dict]:
    ordered = sorted((dict(row) for row in rows), key=row_ts)
    seen: set[datetime] = set()
    buckets: dict[datetime, list[dict]] = defaultdict(list)
    for row in ordered:
        ts = row_ts(row)
        if ts in seen:
            raise ValueError(f"duplicate 5m timestamp: {ts.isoformat()}")
        seen.add(ts)
        buckets[_bucket_15m(ts)].append(row)

    complete: list[dict] = []
    skipped = 0
    expected_offsets = (0, 5, 10)
    for bucket_start in sorted(buckets):
        group = sorted(buckets[bucket_start], key=row_ts)
        offsets = tuple(int((row_ts(row) - bucket_start).total_seconds() // 60) for row in group)
        if len(group) != 3 or offsets != expected_offsets:
            skipped += 1
            continue
        bar = {
            "timestamp": bucket_start.isoformat(),
            "close_ts": (bucket_start + timedelta(minutes=15)).isoformat(),
            "open": _price(group[0], "open"),
            "high": max(_price(row, "high") for row in group),
            "low": min(_price(row, "low") for row in group),
            "close": _price(group[-1], "close"),
            "source_5m_count": 3,
        }
        volumes = [row.get("volume") for row in group]
        if all(v is not None and not isinstance(v, bool) for v in volumes):
            try:
                bar["volume"] = sum(float(v) for v in volumes)
            except (TypeError, ValueError):
                pass
        complete.append(bar)
    return complete, {
        "fine_rows": len(ordered),
        "complete_15m_bars": len(complete),
        "skipped_incomplete_15m_buckets": skipped,
    }


def _unique_pivot_high(window: list[dict], center: int) -> Optional[float]:
    highs = [_price(row, "high") for row in window]
    candidate = highs[center]
    if candidate == max(highs) and highs.count(candidate) == 1:
        return candidate
    return None


def _unique_pivot_low(window: list[dict], center: int) -> Optional[float]:
    lows = [_price(row, "low") for row in window]
    candidate = lows[center]
    if candidate == min(lows) and lows.count(candidate) == 1:
        return candidate
    return None


def detect_structure_events(
    bars_15m: Iterable[dict],
    *,
    swing: int = DEFAULT_SWING,
) -> list[dict]:
    if swing < 1:
        raise ValueError("swing must be >= 1")
    bars = sorted((dict(row) for row in bars_15m), key=lambda row: parse_ts(row["timestamp"]))
    events: list[dict] = []
    last_swing_high: Optional[tuple[float, str]] = None
    last_swing_low: Optional[tuple[float, str]] = None
    last_break_dir = 0
    prev_close: Optional[float] = None

    for i, bar in enumerate(bars):
        if i >= 2 * swing:
            start = i - (2 * swing)
            window = bars[start : i + 1]
            center = swing
            high = _unique_pivot_high(window, center)
            low = _unique_pivot_low(window, center)
            pivot_bar = window[center]
            if high is not None:
                last_swing_high = (high, str(pivot_bar["timestamp"]))
            if low is not None:
                last_swing_low = (low, str(pivot_bar["timestamp"]))

        close = _price(bar, "close")
        bull_break = (
            prev_close is not None
            and last_swing_high is not None
            and close > last_swing_high[0]
            and prev_close <= last_swing_high[0]
        )
        bear_break = (
            prev_close is not None
            and last_swing_low is not None
            and close < last_swing_low[0]
            and prev_close >= last_swing_low[0]
        )

        if bull_break:
            event_type = "BOS" if last_break_dir == 1 else "MSS" if last_break_dir == -1 else "FIRST_BREAK"
            if event_type != "FIRST_BREAK":
                events.append(
                    {
                        "event_type": event_type,
                        "direction": "LONG",
                        "event_bar_ts": str(bar["timestamp"]),
                        "event_ts": str(
                            bar.get("close_ts")
                            or (parse_ts(bar["timestamp"]) + timedelta(minutes=15)).isoformat()
                        ),
                        "event_price": close,
                        "broken_level": last_swing_high[0],
                        "pivot_ts": last_swing_high[1],
                        "swing": swing,
                    }
                )
            last_break_dir = 1

        if bear_break:
            event_type = "BOS" if last_break_dir == -1 else "MSS" if last_break_dir == 1 else "FIRST_BREAK"
            if event_type != "FIRST_BREAK":
                events.append(
                    {
                        "event_type": event_type,
                        "direction": "SHORT",
                        "event_bar_ts": str(bar["timestamp"]),
                        "event_ts": str(
                            bar.get("close_ts")
                            or (parse_ts(bar["timestamp"]) + timedelta(minutes=15)).isoformat()
                        ),
                        "event_price": close,
                        "broken_level": last_swing_low[0],
                        "pivot_ts": last_swing_low[1],
                        "swing": swing,
                    }
                )
            last_break_dir = -1

        prev_close = close

    return events


def first_retest(
    event: dict,
    fine_5m_rows: Iterable[dict],
    *,
    max_minutes: int = 120,
) -> dict:
    if max_minutes <= 0:
        raise ValueError("max_minutes must be positive")
    event_ts = parse_ts(event["event_ts"])
    end_ts = event_ts + timedelta(minutes=max_minutes)
    level = float(event["broken_level"])
    direction = str(event["direction"]).upper()

    ordered, times = _prepare_fine(fine_5m_rows)
    for bar in _fine_window(ordered, times, event_ts, end_ts):
        ts = row_ts(bar)
        high = _price(bar, "high")
        low = _price(bar, "low")
        close = _price(bar, "close")
        touched = low <= level if direction == "LONG" else high >= level
        if not touched:
            continue
        held = close > level if direction == "LONG" else close < level
        available_ts = ts + timedelta(minutes=5)
        return {
            "retest_status": RETEST_HOLD if held else RETEST_FAIL,
            "retest_bar_ts": ts.isoformat(),
            "retest_ts": available_ts.isoformat(),
            "retest_price": close,
            "retest_latency_minutes": int((available_ts - event_ts).total_seconds() // 60),
        }

    return {
        "retest_status": NO_RETEST,
        "retest_bar_ts": None,
        "retest_ts": None,
        "retest_price": None,
        "retest_latency_minutes": None,
    }


def forward_excursions(
    *,
    direction: str,
    reference_price: float,
    start_ts: object,
    fine_5m_rows: Iterable[dict],
    horizons_minutes: Iterable[int] = DEFAULT_HORIZONS_MINUTES,
) -> dict[str, dict]:
    start = parse_ts(start_ts)
    direction = str(direction).upper()
    if direction not in {"LONG", "SHORT"}:
        raise ValueError("direction must be LONG or SHORT")
    rows, times = _prepare_fine(fine_5m_rows)
    out: dict[str, dict] = {}
    for horizon in horizons_minutes:
        if horizon <= 0:
            raise ValueError("horizons must be positive")
        end = start + timedelta(minutes=int(horizon))
        selected = _fine_window(rows, times, start, end)
        if not selected:
            out[str(horizon)] = {
                "bars": 0,
                "signed_close_points": None,
                "mfe_points": None,
                "mae_points": None,
            }
            continue
        last_close = _price(selected[-1], "close")
        if direction == "LONG":
            signed_close = last_close - reference_price
            mfe = max(_price(row, "high") - reference_price for row in selected)
            mae = max(reference_price - _price(row, "low") for row in selected)
        else:
            signed_close = reference_price - last_close
            mfe = max(reference_price - _price(row, "low") for row in selected)
            mae = max(_price(row, "high") - reference_price for row in selected)
        out[str(horizon)] = {
            "bars": len(selected),
            "signed_close_points": round(signed_close, 6),
            "mfe_points": round(max(0.0, mfe), 6),
            "mae_points": round(max(0.0, mae), 6),
        }
    return out


def _first_retest_indexed(
    event: dict,
    ordered: list[dict],
    times: list[datetime],
    *,
    max_minutes: int,
) -> dict:
    event_ts = parse_ts(event["event_ts"])
    end_ts = event_ts + timedelta(minutes=max_minutes)
    level = float(event["broken_level"])
    direction = str(event["direction"]).upper()
    for bar in _fine_window(ordered, times, event_ts, end_ts):
        ts = row_ts(bar)
        high = _price(bar, "high")
        low = _price(bar, "low")
        close = _price(bar, "close")
        touched = low <= level if direction == "LONG" else high >= level
        if not touched:
            continue
        held = close > level if direction == "LONG" else close < level
        available_ts = ts + timedelta(minutes=5)
        return {
            "retest_status": RETEST_HOLD if held else RETEST_FAIL,
            "retest_bar_ts": ts.isoformat(),
            "retest_ts": available_ts.isoformat(),
            "retest_price": close,
            "retest_latency_minutes": int((available_ts - event_ts).total_seconds() // 60),
        }
    return {
        "retest_status": NO_RETEST,
        "retest_bar_ts": None,
        "retest_ts": None,
        "retest_price": None,
        "retest_latency_minutes": None,
    }


def _forward_excursions_indexed(
    *,
    direction: str,
    reference_price: float,
    start_ts: object,
    ordered: list[dict],
    times: list[datetime],
    horizons_minutes: Iterable[int],
) -> dict[str, dict]:
    start = parse_ts(start_ts)
    direction = str(direction).upper()
    out: dict[str, dict] = {}
    for horizon in horizons_minutes:
        end = start + timedelta(minutes=int(horizon))
        selected = _fine_window(ordered, times, start, end)
        if not selected:
            out[str(horizon)] = {
                "bars": 0,
                "signed_close_points": None,
                "mfe_points": None,
                "mae_points": None,
            }
            continue
        last_close = _price(selected[-1], "close")
        if direction == "LONG":
            signed_close = last_close - reference_price
            mfe = max(_price(row, "high") - reference_price for row in selected)
            mae = max(reference_price - _price(row, "low") for row in selected)
        else:
            signed_close = reference_price - last_close
            mfe = max(reference_price - _price(row, "low") for row in selected)
            mae = max(_price(row, "high") - reference_price for row in selected)
        out[str(horizon)] = {
            "bars": len(selected),
            "signed_close_points": round(signed_close, 6),
            "mfe_points": round(max(0.0, mfe), 6),
            "mae_points": round(max(0.0, mae), 6),
        }
    return out


def build_event_records(
    fine_5m_rows: Iterable[dict],
    *,
    swing: int = DEFAULT_SWING,
    horizons_minutes: Iterable[int] = DEFAULT_HORIZONS_MINUTES,
    retest_max_minutes: int = 120,
) -> tuple[list[dict], dict]:
    fine, fine_times = _prepare_fine(fine_5m_rows)
    bars_15m, aggregation = aggregate_5m_to_15m(fine)
    events = detect_structure_events(bars_15m, swing=swing)
    records = []
    for event in events:
        record = dict(event)
        record["event_forward"] = _forward_excursions_indexed(
            direction=event["direction"],
            reference_price=float(event["event_price"]),
            start_ts=event["event_ts"],
            ordered=fine,
            times=fine_times,
            horizons_minutes=horizons_minutes,
        )
        retest = _first_retest_indexed(
            event,
            fine,
            fine_times,
            max_minutes=retest_max_minutes,
        )
        record.update(retest)
        record["retest_forward"] = None
        if retest["retest_status"] == RETEST_HOLD:
            record["retest_forward"] = _forward_excursions_indexed(
                direction=event["direction"],
                reference_price=float(retest["retest_price"]),
                start_ts=retest["retest_ts"],
                ordered=fine,
                times=fine_times,
                horizons_minutes=horizons_minutes,
            )
        records.append(record)
    return records, {**aggregation, "structure_events": len(records)}


def _metric_summary(records: list[dict], field: str, horizon: str) -> dict:
    values = []
    mfes = []
    maes = []
    for record in records:
        metrics = record.get(field)
        if not metrics:
            continue
        cell = metrics.get(horizon)
        if not cell or cell.get("signed_close_points") is None:
            continue
        values.append(float(cell["signed_close_points"]))
        mfes.append(float(cell["mfe_points"]))
        maes.append(float(cell["mae_points"]))
    if not values:
        return {"n": 0}
    return {
        "n": len(values),
        "positive_close_pct": round(sum(v > 0 for v in values) / len(values) * 100.0, 2),
        "mean_signed_close_points": round(statistics.fmean(values), 4),
        "median_signed_close_points": round(statistics.median(values), 4),
        "median_mfe_points": round(statistics.median(mfes), 4),
        "median_mae_points": round(statistics.median(maes), 4),
        "mfe_gt_mae_pct": round(sum(m > a for m, a in zip(mfes, maes)) / len(values) * 100.0, 2),
    }


def summarize_records(
    records: Iterable[dict],
    *,
    horizons_minutes: Iterable[int] = DEFAULT_HORIZONS_MINUTES,
) -> dict:
    rows = list(records)
    horizons = [str(int(h)) for h in horizons_minutes]
    by_type = {kind: [r for r in rows if r.get("event_type") == kind] for kind in ("BOS", "MSS")}
    by_direction = {d: [r for r in rows if r.get("direction") == d] for d in ("LONG", "SHORT")}
    retest_counts = {
        status: sum(r.get("retest_status") == status for r in rows)
        for status in (RETEST_HOLD, RETEST_FAIL, NO_RETEST)
    }
    return {
        "authority": "research_event_study_only",
        "trade_strategy_defined": False,
        "choch_claimed": False,
        "structure_definition": "RiskSentinel-compatible BOS/MSS state machine with fail-closed pivot ties",
        "events": len(rows),
        "by_event_type": {key: len(value) for key, value in by_type.items()},
        "by_direction": {key: len(value) for key, value in by_direction.items()},
        "retest_counts": retest_counts,
        "event_forward": {
            horizon: _metric_summary(rows, "event_forward", horizon) for horizon in horizons
        },
        "retest_hold_forward": {
            horizon: _metric_summary(
                [r for r in rows if r.get("retest_status") == RETEST_HOLD],
                "retest_forward",
                horizon,
            )
            for horizon in horizons
        },
        "event_forward_by_type": {
            kind: {
                horizon: _metric_summary(group, "event_forward", horizon)
                for horizon in horizons
            }
            for kind, group in by_type.items()
        },
        "retest_hold_forward_by_type": {
            kind: {
                horizon: _metric_summary(
                    [r for r in group if r.get("retest_status") == RETEST_HOLD],
                    "retest_forward",
                    horizon,
                )
                for horizon in horizons
            }
            for kind, group in by_type.items()
        },
    }
