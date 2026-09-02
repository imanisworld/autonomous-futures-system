"""Read-only evidence audit for ``RANGE_BREAK_CLOSE`` semantics.

Purpose
-------
The runtime implementation now fixes two proven defects (directional target
selection and one-shot arming), but three questions remain deliberately
unanswered by policy:

* how far beyond a wall may a close be before the setup is too extended?
* does wall freshness materially change outcomes?
* should the existing retest/watch-state plumbing be promoted from dormant
  observation code?

This module answers only the first two questions from already-journaled evidence.
It does NOT invent a maximum break distance, change freshness behavior, alter
``context/range_signal.py``, or simulate a new fill model.  Historical rows are
reduced with the exact one-shot/re-arm semantics now used by
``RangeBreakArmState`` so pre-fix repeated candidates do not masquerade as
independent events.

All P&L values are the journaled shadow resolver's gross ticks.  No commission
or slippage adjustment is added here; callers must not read the output as net
expectancy.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import mean
from typing import Any, Iterable, Iterator, Optional, Sequence

BREAK = "RANGE_BREAK_CLOSE"
BREAK_REPEAT = "RANGE_BREAK_CLOSE_REPEAT"
NO_DATA = "RANGE_NO_DATA"
LANE = "range_signal"
STRATEGY = BREAK.lower()


@dataclass(frozen=True)
class BreakEvent:
    candidate_key: str
    ts: str
    instrument: str
    direction: str
    entry: float
    stop: float
    target: float
    wall_name: Optional[str]
    wall_kind: Optional[str]
    wall_price: Optional[float]
    wall_fresh: Optional[bool]
    break_pct: Optional[float]
    outcome_result: Optional[str]
    pnl_ticks: Optional[float]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _finite_float(value: Any) -> Optional[float]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _signal_from_row(row: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Return the first range signal exactly as the live resolver does."""
    for field in ("range_signal", "shadow_range_signal"):
        signal = row.get(field)
        if isinstance(signal, dict):
            return signal
    return None


def _candidate_key(
    instrument: str,
    bar_ts: str,
    direction: str,
    entry: float,
) -> str:
    """Mirror ``strategy.shadow_resolver._candidate_key`` for this lane."""
    return f"{LANE}|{instrument}|{bar_ts}|{STRATEGY}|{direction}|{entry}"


def _wall_price(wall: dict[str, Any]) -> Optional[float]:
    value = _finite_float(wall.get("value"))
    if value is not None:
        return value
    upper = _finite_float(wall.get("upper"))
    lower = _finite_float(wall.get("lower"))
    if upper is not None and lower is not None:
        return (upper + lower) / 2.0
    return upper if upper is not None else lower


def _broken_wall(
    row: dict[str, Any],
    *,
    direction: str,
    stop: float,
) -> Optional[dict[str, Any]]:
    """Recover the wall whose unchanged stop formula produced ``stop``.

    ``RANGE_BREAK_CLOSE`` still constructs its stop as ``wall * 0.999`` for
    LONG and ``wall * 1.001`` for SHORT.  Matching that formula is more reliable
    than simply taking the nearest wall because WallContext can place at-price
    levels in both lists.
    """
    wall_ctx = row.get("wall_context")
    if not isinstance(wall_ctx, dict):
        return None
    walls_key = "walls_below" if direction == "LONG" else "walls_above"
    expected_kind = "resistance" if direction == "LONG" else "support"
    walls = wall_ctx.get(walls_key)
    if not isinstance(walls, list):
        return None

    matches: list[tuple[float, dict[str, Any]]] = []
    for wall in walls:
        if not isinstance(wall, dict) or str(wall.get("kind") or "") != expected_kind:
            continue
        cp = _wall_price(wall)
        if cp is None or cp <= 0:
            continue
        predicted = round(cp * (0.999 if direction == "LONG" else 1.001), 2)
        error = abs(predicted - stop)
        if error <= 0.011:
            matches.append((error, wall))
    if not matches:
        return None
    matches.sort(key=lambda item: item[0])
    return matches[0][1]


def _outcome_index(rows: Sequence[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Index terminal range-lane outcomes by their resolver candidate key."""
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row.get("type") != "SHADOW_OUTCOME" or row.get("lane") != LANE:
            continue
        key = row.get("candidate_key")
        shadow = row.get("shadow_outcome")
        if not isinstance(key, str) or not isinstance(shadow, dict):
            continue
        # First terminal row wins.  Duplicate keys are an integrity problem for
        # the caller to inspect rather than something this audit silently sums.
        out.setdefault(key, shadow)
    return out


def _parse_break_event(
    row: dict[str, Any],
    outcomes: dict[str, dict[str, Any]],
) -> Optional[BreakEvent]:
    signal = _signal_from_row(row)
    if not isinstance(signal, dict) or signal.get("signal_type") != BREAK:
        return None
    instrument = str(row.get("instrument") or "").upper()
    ts = row.get("ts") or row.get("timestamp")
    direction = str(signal.get("direction") or "").upper()
    entry = _finite_float(signal.get("entry_candidate"))
    stop = _finite_float(signal.get("stop_candidate"))
    target = _finite_float(signal.get("target_candidate"))
    if (
        not instrument
        or not isinstance(ts, str)
        or direction not in {"LONG", "SHORT"}
        or entry is None
        or stop is None
        or target is None
    ):
        return None

    key = _candidate_key(instrument, ts, direction, entry)
    wall = _broken_wall(row, direction=direction, stop=stop)
    wall_name: Optional[str] = None
    wall_kind: Optional[str] = None
    wall_price: Optional[float] = None
    wall_fresh: Optional[bool] = None
    break_pct: Optional[float] = None
    if wall is not None:
        wall_name = str(wall.get("name") or "") or None
        wall_kind = str(wall.get("kind") or "") or None
        wall_price = _wall_price(wall)
        fresh = wall.get("fresh")
        wall_fresh = fresh if isinstance(fresh, bool) else None
        if wall_price is not None and wall_price > 0:
            break_pct = abs(entry - wall_price) / wall_price

    outcome = outcomes.get(key, {})
    outcome_result = str(outcome.get("result")) if outcome.get("result") is not None else None
    pnl_ticks = _finite_float(outcome.get("pnl_ticks"))
    return BreakEvent(
        candidate_key=key,
        ts=ts,
        instrument=instrument,
        direction=direction,
        entry=entry,
        stop=stop,
        target=target,
        wall_name=wall_name,
        wall_kind=wall_kind,
        wall_price=wall_price,
        wall_fresh=wall_fresh,
        break_pct=break_pct,
        outcome_result=outcome_result,
        pnl_ticks=pnl_ticks,
    )


def one_shot_break_events(rows: Sequence[dict[str, Any]]) -> list[BreakEvent]:
    """Reconstruct the current one-shot / clear-rearm policy over old journals.

    This intentionally mirrors ``RangeBreakArmState`` rather than deduplicating
    by day or by an arbitrary time gap:

    * first BREAK for ``(direction, stop_candidate)`` passes;
    * same-key BREAK rows are repeats and are skipped;
    * BREAK_REPEAT preserves the arm;
    * NO_DATA preserves the arm;
    * any other observed range signal clears the arm.
    """
    outcomes = _outcome_index(rows)
    armed: dict[str, tuple[str, float]] = {}
    accepted: list[BreakEvent] = []

    for row in rows:
        signal = _signal_from_row(row)
        if not isinstance(signal, dict):
            continue
        instrument = str(row.get("instrument") or "").upper()
        signal_type = str(signal.get("signal_type") or "")
        if signal_type == BREAK:
            direction = str(signal.get("direction") or "").upper()
            stop = _finite_float(signal.get("stop_candidate"))
            if not instrument or direction not in {"LONG", "SHORT"} or stop is None:
                continue
            arm_key = (direction, stop)
            if armed.get(instrument) == arm_key:
                continue
            event = _parse_break_event(row, outcomes)
            if event is None:
                continue
            armed[instrument] = arm_key
            accepted.append(event)
            continue
        if signal_type in {BREAK_REPEAT, NO_DATA}:
            continue
        if instrument:
            armed.pop(instrument, None)

    return accepted


def raw_break_events(rows: Sequence[dict[str, Any]]) -> list[BreakEvent]:
    outcomes = _outcome_index(rows)
    events: list[BreakEvent] = []
    for row in rows:
        event = _parse_break_event(row, outcomes)
        if event is not None:
            events.append(event)
    return events


def _trade_metrics(events: Sequence[BreakEvent]) -> dict[str, Any]:
    resolved = [e for e in events if e.outcome_result in {"WIN", "LOSS"}]
    wins = [e for e in resolved if e.outcome_result == "WIN"]
    pnl = [e.pnl_ticks for e in resolved if e.pnl_ticks is not None]
    return {
        "events": len(events),
        "resolved_win_loss": len(resolved),
        "wins": len(wins),
        "losses": len(resolved) - len(wins),
        "win_rate": (len(wins) / len(resolved)) if resolved else None,
        "gross_pnl_ticks": sum(pnl) if pnl else None,
        "avg_pnl_ticks": mean(pnl) if pnl else None,
        "gross_only_no_cost_model": True,
    }


def _percentile(values: Sequence[float], q: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * q
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return ordered[lo]
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def _distance_summary(events: Sequence[BreakEvent]) -> dict[str, Any]:
    values = [e.break_pct for e in events if e.break_pct is not None]
    return {
        "known": len(values),
        "unknown": len(events) - len(values),
        "fraction_quantiles": {
            "p50": _percentile(values, 0.50),
            "p75": _percentile(values, 0.75),
            "p90": _percentile(values, 0.90),
            "p95": _percentile(values, 0.95),
            "max": max(values) if values else None,
        },
    }


def _group_metrics(events: Sequence[BreakEvent], key_fn) -> dict[str, Any]:
    grouped: dict[str, list[BreakEvent]] = {}
    for event in events:
        grouped.setdefault(str(key_fn(event)), []).append(event)
    return {key: _trade_metrics(group) for key, group in sorted(grouped.items())}


def build_report(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    raw = raw_break_events(rows)
    one_shot = one_shot_break_events(rows)
    return {
        "verdict_scope": "READ_ONLY_SEMANTICS_AUDIT",
        "policy_changes": [],
        "raw_break_rows": len(raw),
        "one_shot_break_events": len(one_shot),
        "repeat_rows_removed_by_current_policy": len(raw) - len(one_shot),
        "one_shot_metrics": _trade_metrics(one_shot),
        "distance": _distance_summary(one_shot),
        "by_instrument": _group_metrics(one_shot, lambda e: e.instrument),
        "by_direction": _group_metrics(one_shot, lambda e: e.direction),
        "by_wall_freshness": _group_metrics(
            one_shot,
            lambda e: "fresh" if e.wall_fresh is True else "stale" if e.wall_fresh is False else "unknown",
        ),
        "by_wall_name": _group_metrics(one_shot, lambda e: e.wall_name or "unknown"),
        "caveats": [
            "Journal shadow outcomes are gross ticks; no commission/slippage adjustment is added here.",
            "No maximum break distance is proposed by this report; quantiles are descriptive only.",
            "Freshness is measured, not promoted to an entry gate.",
            "Retest/watch-state behavior is not changed by this audit.",
        ],
    }


def read_journal_rows(log_dir: str | Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(Path(log_dir).glob("journal_*.jsonl")):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except (TypeError, ValueError):
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def _filtered(rows: Iterable[dict[str, Any]], instrument: Optional[str]) -> list[dict[str, Any]]:
    if not instrument:
        return list(rows)
    wanted = instrument.upper()
    return [row for row in rows if str(row.get("instrument") or "").upper() in {"", wanted}]


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log-dir", default="logs", help="Directory containing journal_*.jsonl")
    parser.add_argument("--instrument", choices=("MES", "MNQ"), default=None)
    args = parser.parse_args(argv)

    rows = _filtered(read_journal_rows(args.log_dir), args.instrument)
    print(json.dumps(build_report(rows), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
