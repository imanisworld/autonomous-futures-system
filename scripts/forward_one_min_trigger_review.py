#!/usr/bin/env python3
"""Read-only review of prospective MNQ 1m trigger-observer evidence."""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.futures_contracts import optional_tick_size
from context.bar_history import _parse_dt
from strategy.four_hr_retrigger import aggregate_et_bars
from strategy.strat_322_first_live import advance_strat_322_first_live

ET = ZoneInfo("America/New_York")
INSTRUMENT = "MNQ"
FOUR_HR = "strat_4hr_retrigger"
FIRST_LIVE = "strat_322_first_live"
TOLERANCE_TICKS = {FOUR_HR: 8.0, FIRST_LIVE: 32.0}


def _read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    if not path.exists():
        return rows
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            rows.append({"_parse_error": f"{path}:{number}: {exc}"})
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _glob_jsonl(root: Path, pattern: str) -> list[dict]:
    rows: list[dict] = []
    for path in sorted(root.glob(pattern)):
        rows.extend(_read_jsonl(path))
    return rows


def _dt(value: Any) -> datetime | None:
    parsed = _parse_dt(str(value or ""))
    return parsed.astimezone(ET) if parsed is not None else None


def _same_num(left: Any, right: Any, tol: float = 1e-9) -> bool:
    try:
        return abs(float(left) - float(right)) <= tol
    except (TypeError, ValueError):
        return False


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * q
    lo = math.floor(position)
    hi = math.ceil(position)
    if lo == hi:
        return ordered[lo]
    weight = position - lo
    return ordered[lo] * (1.0 - weight) + ordered[hi] * weight


def _load_bars(log_dir: Path, lane: str) -> list[dict]:
    return _glob_jsonl(log_dir / lane, "bars_MNQ_*.jsonl")


def _bar_index(rows: list[dict]) -> dict[datetime, dict]:
    out: dict[datetime, dict] = {}
    for row in rows:
        ts = _dt(row.get("ts") or row.get("timestamp"))
        if ts is not None:
            out[ts.replace(second=0, microsecond=0)] = row
    return out


def _expected_322_slots(day: date) -> list[datetime]:
    start = datetime(day.year, day.month, day.day, 7, 0, tzinfo=ET)
    return [start + timedelta(minutes=5 * i) for i in range(36)]



def _full_window_days(one_rows: list[dict], strategy: str) -> list[str]:
    by_day: dict[date, set[tuple[int, int]]] = defaultdict(set)
    for row in one_rows:
        ts = _dt(row.get("ts") or row.get("timestamp"))
        if ts is None:
            continue
        by_day[ts.date()].add((ts.hour, ts.minute))
    if strategy == FOUR_HR:
        expected = {
            (9 + ((30 + i) // 60), (30 + i) % 60)
            for i in range(90)
        }
    else:
        expected = {(10, i) for i in range(60)}
    return [
        day.isoformat()
        for day, slots in sorted(by_day.items())
        if expected.issubset(slots)
    ]


def _event_response_matches(event: dict, audit_rows: list[dict]) -> list[dict]:
    strategy = str(event.get("strategy") or "")
    field = "one_min_trigger" if strategy == FOUR_HR else "one_min_322_observer"
    return [row for row in audit_rows if row.get(field) == event]


def _response_problems(event: dict, matches: list[dict]) -> list[str]:
    if not matches:
        return ["MISSING_DURABLE_RESPONSE_AUDIT"]
    if len(matches) != 1:
        return [f"RESPONSE_AUDIT_MATCH_COUNT_{len(matches)}"]
    row = matches[0]
    response = row.get("response") or {}
    payload = row.get("payload") or {}
    strategy = str(event.get("strategy") or "")
    event_type = str(event.get("event") or "")
    expected_decision = (
        "FIVE_MIN_CONTEXT"
        if strategy == FIRST_LIVE
        and event_type in {"ARMED", "ARM_BLOCKED", "SETUP_NOT_ARMED", "EXPIRED"}
        else "ONE_MIN_CONTEXT"
    )
    expected_tf = "5m" if expected_decision == "FIVE_MIN_CONTEXT" else "1m"
    problems: list[str] = []
    if response.get("decision") != expected_decision:
        problems.append(f"RESPONSE_DECISION_{response.get('decision')!s}")
    if response.get("fill_is_none") is not True:
        problems.append("RESPONSE_FILL_PRESENT")
    if response.get("risk_is_none") is not True:
        problems.append("RESPONSE_RISK_PRESENT")
    if response.get("execution_reachable") is not False:
        problems.append("RESPONSE_EXECUTION_REACHABLE")
    if str(payload.get("timeframe") or "").lower() not in {expected_tf, expected_tf[:-1]}:
        problems.append(f"RESPONSE_TIMEFRAME_{payload.get('timeframe')!s}")
    if row.get("one_min_error"):
        problems.append("ONE_MIN_OBSERVER_ERROR")
    if row.get("one_min_322_observer_error"):
        problems.append("322_OBSERVER_ERROR")
    return problems


def _authority_problems(event: dict) -> list[str]:
    strategy = str(event.get("strategy") or "")
    kind = str(event.get("event") or "")
    problems: list[str] = []
    if strategy == FIRST_LIVE:
        if event.get("mode") != "observation_only":
            problems.append("322_MODE_NOT_OBSERVATION_ONLY")
        if event.get("trade_authorized") is not False:
            problems.append("322_TRADE_AUTHORIZED")
        if event.get("paper_fill_authorized") is not False:
            problems.append("322_PAPER_FILL_AUTHORIZED")
        if event.get("external_broker") is not False:
            problems.append("322_EXTERNAL_BROKER")
    elif strategy == FOUR_HR and kind == "TRIGGER_TOUCH":
        if event.get("mode") != "paper_evidence_only":
            problems.append("4HR_MODE_NOT_EVIDENCE_ONLY")
        if event.get("trade_authorized") is not False:
            problems.append("4HR_TRADE_AUTHORIZED")
        if event.get("external_broker") is not False:
            problems.append("4HR_EXTERNAL_BROKER")
    return problems


def _four_hr_problems(
    event: dict, one_min: dict[datetime, dict], five_rows: list[dict]
) -> list[str]:
    kind = str(event.get("event") or "")
    if kind != "TRIGGER_TOUCH":
        return []
    problems: list[str] = []
    ts = _dt(event.get("bar_ts"))
    if ts is None:
        return ["4HR_INVALID_BAR_TS"]
    local_time = ts.timetz().replace(tzinfo=None)
    if not (time(9, 30) <= local_time < time(11, 0)):
        problems.append("4HR_TOUCH_OUTSIDE_WINDOW")
    state = event.get("source_state") or {}
    if state.get("status") != "ARMED":
        problems.append("4HR_SOURCE_NOT_ARMED")
    if state.get("trading_date") != ts.date().isoformat():
        problems.append("4HR_SOURCE_DATE_MISMATCH")
    direction = str(event.get("direction") or "").upper()
    if direction not in {"LONG", "SHORT"}:
        problems.append("4HR_INVALID_DIRECTION")
    if not _same_num(event.get("trigger"), state.get("trigger")):
        problems.append("4HR_TRIGGER_STATE_MISMATCH")
    if not _same_num(event.get("target"), state.get("target")):
        problems.append("4HR_TARGET_STATE_MISMATCH")

    bar = one_min.get(ts.replace(second=0, microsecond=0))
    if bar is None:
        problems.append("4HR_MISSING_RAW_1M_BAR")
        return problems
    trigger = float(event["trigger"])
    open_px = float(bar["open"])
    high = float(bar["high"])
    low = float(bar["low"])
    touched = high >= trigger if direction == "LONG" else low <= trigger
    if not touched:
        problems.append("4HR_RAW_1M_DOES_NOT_TOUCH")
    expected_ref = max(trigger, open_px) if direction == "LONG" else min(trigger, open_px)
    if not _same_num(event.get("fill_reference"), expected_ref):
        problems.append("4HR_FILL_REFERENCE_MISMATCH")
    expected_gap = open_px >= trigger if direction == "LONG" else open_px <= trigger
    if event.get("gap_through") is not expected_gap:
        problems.append("4HR_GAP_THROUGH_MISMATCH")

    available = []
    for raw in five_rows:
        bar_ts = _dt(raw.get("ts") or raw.get("timestamp"))
        if bar_ts is not None and bar_ts + timedelta(minutes=5) <= ts:
            available.append(raw)
    one_hour = aggregate_et_bars(available, 60)
    completed = [
        bar_1h for bar_1h in one_hour
        if bar_1h.get("count", 0) >= 12 and bar_1h["ts"] + timedelta(hours=1) <= ts
    ]
    if not completed:
        problems.append("4HR_COMPLETED_1H_STOP_NOT_RECONSTRUCTABLE")
        return problems
    ref = completed[-1]
    expected_stop = ref["low"] if direction == "LONG" else ref["high"]
    stop_bar_ts = _dt(event.get("stop_bar_ts"))
    if stop_bar_ts != ref["ts"]:
        problems.append("4HR_STOP_BAR_TS_MISMATCH")
    if not _same_num(event.get("stop"), expected_stop):
        problems.append("4HR_STOP_VALUE_MISMATCH")
    target = float(event["target"])
    stop = float(event["stop"])
    if direction == "LONG":
        valid = stop < expected_ref < target
    else:
        valid = target < expected_ref < stop
    if not valid:
        problems.append("4HR_ACCEPTED_TOUCH_INVALID_BRACKET")
    return problems


def _reference_322_bars(five_rows: list[dict], day: date) -> list[dict]:
    expected = set(_expected_322_slots(day))
    rows = []
    for raw in five_rows:
        ts = _dt(raw.get("ts") or raw.get("timestamp"))
        if ts is not None and ts.replace(second=0, microsecond=0) in expected:
            rows.append(raw)
    return rows


def _three_two_two_problems(
    event: dict, one_min: dict[datetime, dict], five_rows: list[dict]
) -> list[str]:
    problems: list[str] = []
    kind = str(event.get("event") or "")
    ts = _dt(event.get("bar_ts"))
    if ts is None:
        return ["322_INVALID_BAR_TS"]
    direction = str(event.get("direction") or "").upper()
    if kind in {"ARMED", "SETUP_NOT_ARMED", "ARM_BLOCKED"}:
        refs = _reference_322_bars(five_rows, ts.date())
        have = {
            _dt(row.get("ts") or row.get("timestamp")).replace(second=0, microsecond=0)
            for row in refs
            if _dt(row.get("ts") or row.get("timestamp")) is not None
        }
        missing = [slot for slot in _expected_322_slots(ts.date()) if slot not in have]
        if kind == "ARM_BLOCKED":
            if event.get("reason") != "REFERENCE_DATA_INCOMPLETE":
                problems.append("322_UNEXPECTED_ARM_BLOCK_REASON")
            if not missing:
                problems.append("322_ARM_BLOCK_WITH_COMPLETE_REFERENCE")
            if int(event.get("missing_reference_count") or -1) != len(missing):
                problems.append("322_MISSING_REFERENCE_COUNT_MISMATCH")
            return problems
        if missing:
            problems.append("322_REFERENCE_DATA_INCOMPLETE_BUT_NOT_BLOCKED")
            return problems
        boundary = datetime(ts.year, ts.month, ts.day, 10, 0, tzinfo=ET)
        state, candidate = advance_strat_322_first_live(
            bars_5m=refs,
            current_bar_ts=boundary,
            instrument=INSTRUMENT,
            persisted_state={},
        )
        if candidate is not None:
            problems.append("322_UNEXPECTED_CANDIDATE_AT_ARM_BOUNDARY")
        if kind == "ARMED":
            if state.get("status") != "ARMED":
                problems.append("322_CANONICAL_STATE_NOT_ARMED")
            for key in ("direction", "trigger", "stop", "target", "setup_bar_ts", "expires_at"):
                if key in {"trigger", "stop", "target"}:
                    if not _same_num(event.get(key), state.get(key)):
                        problems.append(f"322_{key.upper()}_MISMATCH")
                elif str(event.get(key) or "") != str(state.get(key) or ""):
                    problems.append(f"322_{key.upper()}_MISMATCH")
        elif str(event.get("reason") or "") != str(state.get("invalidation") or ""):
            problems.append("322_NOT_ARMED_REASON_MISMATCH")
        return problems

    if kind == "EXPIRED":
        if ts.timetz().replace(tzinfo=None) != time(11, 0):
            problems.append("322_EXPIRY_NOT_AT_1100")
        return problems

    if kind not in {"TRIGGER_TOUCH", "TRIGGER_BLOCKED"}:
        return problems
    local_time = ts.timetz().replace(tzinfo=None)
    if not (time(10, 0) <= local_time < time(11, 0)):
        problems.append("322_TOUCH_OUTSIDE_WINDOW")
    bar = one_min.get(ts.replace(second=0, microsecond=0))
    if bar is None:
        problems.append("322_MISSING_RAW_1M_BAR")
        return problems
    if direction not in {"LONG", "SHORT"}:
        problems.append("322_INVALID_DIRECTION")
        return problems
    trigger = float(event["trigger"])
    open_px = float(bar["open"])
    high = float(bar["high"])
    low = float(bar["low"])
    crossed = high > trigger if direction == "LONG" else low < trigger
    if not crossed:
        problems.append("322_RAW_1M_NOT_STRICT_BREAK")
    expected_gap = open_px > trigger if direction == "LONG" else open_px < trigger
    expected_ref = open_px if expected_gap else trigger
    if event.get("gap_through") is not expected_gap:
        problems.append("322_GAP_THROUGH_MISMATCH")
    if not _same_num(event.get("trigger_reference"), expected_ref):
        problems.append("322_TRIGGER_REFERENCE_MISMATCH")
    try:
        stop = float(event["stop"])
        target = float(event["target"])
    except (KeyError, TypeError, ValueError):
        return problems + ["322_INVALID_BRACKET_FIELDS"]
    valid = stop < expected_ref < target if direction == "LONG" else target < expected_ref < stop
    if kind == "TRIGGER_TOUCH" and not valid:
        problems.append("322_ACCEPTED_TOUCH_INVALID_BRACKET")
    if kind == "TRIGGER_BLOCKED" and valid:
        problems.append("322_BLOCKED_TOUCH_BRACKET_WAS_VALID")
    return problems


def _timing_row(event: dict, five_min: dict[datetime, dict]) -> dict | None:
    if event.get("event") != "TRIGGER_TOUCH":
        return None
    ts = _dt(event.get("bar_ts"))
    decision = _dt(event.get("decision_time"))
    if ts is None or decision is None:
        return None
    bucket = ts.replace(minute=(ts.minute // 5) * 5, second=0, microsecond=0)
    bar = five_min.get(bucket)
    if bar is None:
        return None
    complete_at = bucket + timedelta(minutes=5)
    tick = float(optional_tick_size(INSTRUMENT) or 0.25)
    trigger = float(event["trigger"])
    close = float(bar["close"])
    direction = str(event.get("direction") or "").upper()
    adverse = max(0.0, close - trigger) / tick if direction == "LONG" else max(0.0, trigger - close) / tick
    return {
        "arm_key": event.get("arm_key"),
        "bar_ts": ts.isoformat(),
        "decision_time": decision.isoformat(),
        "five_min_decision_time": complete_at.isoformat(),
        "timing_advantage_seconds": (complete_at - decision).total_seconds(),
        "five_min_close": close,
        "trigger_to_five_min_close_ticks": abs(close - trigger) / tick,
        "adverse_detachment_ticks": adverse,
    }


def _timing_summary(strategy: str, rows: list[dict]) -> dict:
    adverse = [float(row["adverse_detachment_ticks"]) for row in rows]
    advantages = [float(row["timing_advantage_seconds"]) for row in rows]
    tol = TOLERANCE_TICKS[strategy]
    return {
        "n": len(rows),
        "median_adverse_detachment_ticks": statistics.median(adverse) if adverse else None,
        "p90_adverse_detachment_ticks": _percentile(adverse, 0.9),
        "max_adverse_detachment_ticks": max(adverse) if adverse else None,
        "share_beyond_historical_ioc_tolerance": (
            sum(value > tol for value in adverse) / len(adverse) if adverse else None
        ),
        "historical_ioc_tolerance_ticks": tol,
        "median_timing_advantage_seconds": statistics.median(advantages) if advantages else None,
        "p90_timing_advantage_seconds": _percentile(advantages, 0.9),
    }


def _strategy_report(
    strategy: str,
    events: list[dict],
    audits: list[dict],
    one_min: dict[datetime, dict],
    five_rows: list[dict],
    five_min: dict[datetime, dict],
    full_window_days: list[str],
) -> dict:
    relevant = [row for row in events if row.get("strategy") == strategy]
    counts = Counter(str(row.get("event") or "") for row in relevant)
    problems: list[dict] = []

    audit_field = "one_min_trigger" if strategy == FOUR_HR else "one_min_322_observer"
    audit_events = [
        row.get(audit_field)
        for row in audits
        if isinstance(row.get(audit_field), dict)
    ]
    response_only = [event for event in audit_events if event not in relevant]
    response_only_counts = Counter(str(event.get("event") or "") for event in response_only)
    for event in response_only:
        if event.get("event") == "TRIGGER_DUPLICATE":
            continue
        problems.append({
            "event": event.get("event"),
            "bar_ts": event.get("bar_ts"),
            "arm_key": event.get("arm_key"),
            "problems": ["RESPONSE_EVENT_WITHOUT_PERSISTED_EVIDENCE"],
        })
    touch_keys = [
        str(row.get("arm_key") or "")
        for row in relevant
        if row.get("event") == "TRIGGER_TOUCH"
    ]
    duplicates = sorted(key for key, count in Counter(touch_keys).items() if key and count > 1)
    if duplicates:
        problems.append({"event": None, "problems": ["DUPLICATE_ACCEPTED_TOUCH_ARM_KEY"], "arm_keys": duplicates})

    timing_rows: list[dict] = []
    for event in relevant:
        local_problems = []
        local_problems.extend(_authority_problems(event))
        matches = _event_response_matches(event, audits)
        local_problems.extend(_response_problems(event, matches))
        if strategy == FOUR_HR:
            local_problems.extend(_four_hr_problems(event, one_min, five_rows))
        else:
            local_problems.extend(_three_two_two_problems(event, one_min, five_rows))
            if event.get("event") == "TRIGGER_TOUCH":
                touch_ts = _dt(event.get("bar_ts"))
                touch_day = str(event.get("trading_date") or (touch_ts.date().isoformat() if touch_ts else ""))
                preceding_arms = [
                    prior
                    for prior in relevant
                    if prior.get("event") == "ARMED"
                    and str(prior.get("trading_date") or "") == touch_day
                    and _dt(prior.get("bar_ts")) is not None
                    and touch_ts is not None
                    and _dt(prior.get("bar_ts")) <= touch_ts
                ]
                if not preceding_arms:
                    local_problems.append("322_TOUCH_WITHOUT_PRECEDING_ARM")
        if local_problems:
            problems.append({
                "event": event.get("event"),
                "bar_ts": event.get("bar_ts"),
                "arm_key": event.get("arm_key"),
                "problems": sorted(set(local_problems)),
            })
        timing = _timing_row(event, five_min)
        if timing is not None:
            timing_rows.append(timing)

    touches = [row for row in relevant if row.get("event") == "TRIGGER_TOUCH"]
    directions = Counter(str(row.get("direction") or "").upper() for row in touches)
    unsafe_codes = {
        "RESPONSE_FILL_PRESENT", "RESPONSE_RISK_PRESENT", "RESPONSE_EXECUTION_REACHABLE",
        "4HR_TRADE_AUTHORIZED", "4HR_EXTERNAL_BROKER", "322_TRADE_AUTHORIZED",
        "322_PAPER_FILL_AUTHORIZED", "322_EXTERNAL_BROKER",
    }
    all_codes = {code for item in problems for code in item.get("problems", [])}
    if all_codes & unsafe_codes:
        classification = "UNSAFE"
    elif problems:
        classification = "HOLD / DEFECT FOUND"
    elif len(touches) >= 3:
        classification = "MECHANISM CLEAN SO FAR"
    else:
        classification = "COLLECT"

    dated_events = [(_dt(row.get("bar_ts")), row) for row in relevant]
    dated_events = [(ts, row) for ts, row in dated_events if ts is not None]
    first_sample_day = min((ts.date() for ts, _ in dated_events), default=None)
    full_since = [
        day
        for day in full_window_days
        if first_sample_day is None or day >= first_sample_day.isoformat()
    ]
    months_since = sorted({day[:7] for day in full_since})
    long_count = int(directions.get("LONG", 0))
    short_count = int(directions.get("SHORT", 0))
    threshold_met = (
        not problems
        and len(touches) >= 10
        and len(full_since) >= 20
        and len(months_since) >= 2
        and long_count >= 3
        and short_count >= 3
    )
    return {
        "classification": classification,
        "event_counts": dict(sorted(counts.items())),
        "response_only_event_counts": dict(sorted(response_only_counts.items())),
        "touches": len(touches),
        "distinct_touch_arm_keys": len({key for key in touch_keys if key}),
        "direction_counts": dict(sorted(directions.items())),
        "duplicate_touch_arm_keys": duplicates,
        "problems": problems,
        "timing": _timing_summary(strategy, timing_rows),
        "timing_rows": timing_rows,
        "progress": {
            "early_three_touch_checkpoint_met": len(touches) >= 3 and not problems,
            "touch_count_minimum_10_met": len(touches) >= 10,
            "full_observer_window_days_since_first_persisted_event": len(full_since),
            "calendar_months_since_first_persisted_event": months_since,
            "direction_balance_3_long_3_short_met": long_count >= 3 and short_count >= 3,
            "minimum_mechanism_threshold_conservative_met": threshold_met,
            "span_anchor": (
                first_sample_day.isoformat() if first_sample_day is not None else None
            ),
        },
    }


def build_report(log_dir: Path) -> dict:
    tf1m = log_dir / "tf1m"
    four_events = _glob_jsonl(tf1m, "4hr_trigger_evidence_*.jsonl")
    first_live_events = _glob_jsonl(tf1m / "322_first_live", "evidence_*.jsonl")
    audits = _glob_jsonl(tf1m, "observer_response_audit_*.jsonl")
    one_rows = _load_bars(log_dir, "tf1m")
    five_rows = _load_bars(log_dir, "tf5m")
    one_min = _bar_index(one_rows)
    five_min = _bar_index(five_rows)

    parse_errors = [
        row["_parse_error"]
        for row in four_events + first_live_events + audits + one_rows + five_rows
        if "_parse_error" in row
    ]
    four_events = [row for row in four_events if "_parse_error" not in row]
    first_live_events = [row for row in first_live_events if "_parse_error" not in row]
    audits = [row for row in audits if "_parse_error" not in row]

    report = {
        "tool": "forward_one_min_trigger_review",
        "log_dir": str(log_dir),
        "parse_errors": parse_errors,
        "response_audit_rows": len(audits),
        "four_hr": _strategy_report(
            FOUR_HR,
            four_events,
            audits,
            one_min,
            five_rows,
            five_min,
            _full_window_days(one_rows, FOUR_HR),
        ),
        "strat_322_first_live": _strategy_report(
            FIRST_LIVE,
            first_live_events,
            audits,
            one_min,
            five_rows,
            five_min,
            _full_window_days(one_rows, FIRST_LIVE),
        ),
    }
    classifications = {
        report["four_hr"]["classification"],
        report["strat_322_first_live"]["classification"],
    }
    if parse_errors:
        report["overall"] = "HOLD / DEFECT FOUND"
    elif "UNSAFE" in classifications:
        report["overall"] = "UNSAFE"
    elif "HOLD / DEFECT FOUND" in classifications:
        report["overall"] = "HOLD / DEFECT FOUND"
    elif classifications == {"MECHANISM CLEAN SO FAR"}:
        report["overall"] = "MECHANISM CLEAN SO FAR"
    else:
        report["overall"] = "COLLECT"
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log-dir", required=True)
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    report = build_report(Path(args.log_dir))
    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if report["overall"] not in {"UNSAFE", "HOLD / DEFECT FOUND"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
