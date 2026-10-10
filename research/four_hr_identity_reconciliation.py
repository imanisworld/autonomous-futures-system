"""Offline 4HR natural-1m / canonical-5m identity reconciliation.

No I/O, broker, P&L, order or promotion path. Consume only a separately
authorized, *prospectively collected* unsealed cohort; never reconstruct lost
historical identities or access blinded forward trial results.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta
from math import isfinite
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

from context.wide_stop_4hr_join_provenance import SCHEMA, compare_with_1m_touch


def _dt(raw: object) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None and parsed.utcoffset() is not None else None


def _touch_clock(touch: Mapping[str, Any]) -> tuple[datetime | None, str | None]:
    opened = _dt(touch.get("bar_ts"))
    decided = _dt(touch.get("decision_time"))
    if (
        opened is None or decided is None or
        opened.second != 0 or opened.microsecond != 0 or
        decided != opened + timedelta(minutes=1)
    ):
        return None, "INVALID_NATURAL_ONE_MIN_CLOCK"
    arm = touch.get("arm_key")
    if not isinstance(arm, str) or not arm or arm.split("|", 1)[0] != opened.astimezone(ET).date().isoformat():
        return None, "NATURAL_TOUCH_ARM_DATE_MISMATCH"
    state = touch.get("source_state")
    if not isinstance(state, Mapping):
        return None, "ARM_AVAILABILITY_UNPROVEN"
    available = _dt(state.get("armed_available_at"))
    if available is None or available > opened:
        return None, "ARM_NOT_AVAILABLE_AT_TOUCH_OPEN"
    if state.get("executable") is not False or state.get("trade_authorized") is not False:
        return None, "OBSERVER_SOURCE_AUTHORITY_INCONSISTENT"
    if touch.get("external_broker") is not False or touch.get("trade_authorized") is not False:
        return None, "NOT_OBSERVATION_ONLY"
    if not isinstance(touch.get("arm_key"), str) or not touch["arm_key"]:
        return None, "ARM_KEY_MISSING"
    return opened, None


def _five_clock(five: Mapping[str, Any]) -> str | None:
    if (
        five.get("schema") != SCHEMA or
        five.get("kind") != "5M_CANONICAL_CANDIDATE_IDENTITY" or
        five.get("source_timeframe") != "5m" or
        five.get("strategy") != "strat_4hr_retrigger" or
        five.get("joinability") != "IDENTITY_AVAILABLE"
    ):
        return "FIVE_MIN_SOURCE_IDENTITY_UNPROVEN"
    # Don't trust an arm_key supplied alongside independently inconsistent
    # fields. The persisted 5m key must reproduce the source detector state
    # rather than merely equal a natural event's claimed arm_key.
    day = five.get("trading_date")
    direction = five.get("direction")
    trigger = five.get("trigger")
    setup_ts = five.get("setup_bar_ts")
    four_am_ts = five.get("four_am_bar_ts")
    if (
        not isinstance(day, str) or not isinstance(direction, str) or
        direction not in {"LONG", "SHORT"} or
        not isinstance(trigger, (int, float)) or isinstance(trigger, bool) or
        not isfinite(trigger) or trigger <= 0 or
        not isinstance(setup_ts, str) or not isinstance(four_am_ts, str) or
        _dt(setup_ts) is None or _dt(four_am_ts) is None
    ):
        return "FIVE_MIN_ARM_FIELDS_UNPROVEN"
    rebuilt = "|".join(
        (day, direction, f"{trigger:.8f}", setup_ts, four_am_ts)
    )
    if rebuilt != five.get("arm_key"):
        return "FIVE_MIN_ARM_KEY_INCONSISTENT"
    opened = _dt(five.get("source_bar_ts"))
    decided = _dt(five.get("candidate_decision_at"))
    if (
        opened is None or decided is None or
        opened.second != 0 or opened.microsecond != 0 or
        opened.astimezone(ET).minute % 5 != 0 or
        decided != opened + timedelta(minutes=5)
    ):
        return "FIVE_MIN_DECISION_CLOCK_UNPROVEN"
    if five.get("trading_date") != opened.astimezone(ET).date().isoformat():
        return "FIVE_MIN_TRADING_DATE_MISMATCH"
    if five.get("observation_only") is not True:
        return "FIVE_MIN_AUTHORITY_INCONSISTENT"
    if five.get("broker_authorized") is not False or five.get("execution_reachable") is not False:
        return "FIVE_MIN_AUTHORITY_INCONSISTENT"
    return None


def reconcile_identity_only(
    touches: Sequence[Mapping[str, Any]],
    five_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Match ALL eligible natural touches to unique 5m source identities.

    Duplicates, multiple 5m candidates, blocked touches, unmatchable and
    unmatched rows remain explicit. This deliberately never reports return,
    fill/stop execution parity or a candidate ready for DEMO.
    """
    five_by_arm: dict[str, list[tuple[int, Mapping[str, Any]]]] = defaultdict(list)
    for j, row in enumerate(five_rows):
        if not isinstance(row, Mapping):
            continue
        arm_key = row.get("arm_key")
        if isinstance(arm_key, str) and arm_key:
            five_by_arm[arm_key].append((j, row))
    touch_arms = Counter(
        row.get("arm_key") for row in touches
        if isinstance(row, Mapping) and row.get("event") == "TRIGGER_TOUCH"
        and isinstance(row.get("arm_key"), str) and row.get("arm_key")
    )
    results: list[dict[str, Any]] = []
    used_five: set[int] = set()
    for i, touch in enumerate(touches):
        if not isinstance(touch, Mapping):
            results.append({
                "touch_index": i, "arm_key": None,
                "status": "UNMATCHABLE", "reason": "MALFORMED_NATURAL_RECORD",
            })
            continue
        status, reason = "UNMATCHABLE", ""
        item: dict[str, Any] = {"touch_index": i, "arm_key": touch.get("arm_key")}
        if touch.get("event") != "TRIGGER_TOUCH":
            status, reason = "NON_TOUCH_EVENT", str(touch.get("event") or "MISSING_EVENT")
        else:
            opened, clock_error = _touch_clock(touch)
            if clock_error is not None:
                status, reason = "UNMATCHABLE", clock_error
            elif touch_arms[touch["arm_key"]] != 1:
                status, reason = "AMBIGUOUS", "DUPLICATE_NATURAL_TOUCH_ARM"
            else:
                matches = five_by_arm.get(touch["arm_key"], [])
                if not matches:
                    status, reason = "UNMATCHED", "NO_FIVE_MIN_FULL_ARM_IDENTITY"
                elif len(matches) != 1:
                    status, reason = "AMBIGUOUS", "MULTIPLE_FIVE_MIN_FOR_ONE_ARM"
                else:
                    j, five = matches[0]
                    item["five_index"] = j
                    five_error = _five_clock(five)
                    if five_error:
                        status, reason = "UNMATCHABLE", five_error
                    else:
                        classification = compare_with_1m_touch(touch, five)
                        status, reason = classification["status"], classification["reason"]
                        if status == "MATCHED_IDENTITY_ONLY":
                            used_five.add(j)
                            five_decision = _dt(five["candidate_decision_at"])
                            item["five_decision_at"] = five_decision.isoformat()
                            item["one_min_observed_at"] = opened.isoformat()
                            item["five_after_one_min_seconds"] = round(
                                (five_decision - opened).total_seconds()
                            )
                            item["five_signal_after_one_min_touch"] = five_decision > opened
                            item["after_one_min_decision_seconds"] = round(
                                (five_decision - _dt(touch["decision_time"])).total_seconds()
                            )
                            stop_one = touch.get("stop")
                            stop_five = five.get("planned_stop")
                            if (
                                isinstance(stop_one, (float, int)) and
                                isinstance(stop_five, (float, int)) and
                                not isinstance(stop_one, bool) and
                                not isinstance(stop_five, bool)
                            ):
                                item["stop_price_equal"] = stop_one == stop_five
                            else:
                                item["stop_price_equal"] = None
                            item["entry_fill_parity"] = "UNPROVEN"
                            item["outcome_parity"] = "UNPROVEN"
        item.update(status=status, reason=reason)
        results.append(item)
    unused = [
        {"five_index": j,
         "candidate_key": row.get("candidate_key") if isinstance(row, Mapping) else None,
         "arm_key": row.get("arm_key") if isinstance(row, Mapping) else None,
         "reason": (
             (row.get("reason") or "NOT_IDENTITY_MATCHED")
             if isinstance(row, Mapping) else "MALFORMED_FIVE_MIN_RECORD"
         )}
        for j, row in enumerate(five_rows) if j not in used_five
    ]
    counts = dict(sorted(Counter(r["status"] for r in results).items()))
    return {
        "schema": "4hr_identity_reconciliation_read_only_v1",
        "scope": "MECHANISM_IDENTITY_ONLY_NOT_RETURNS",
        "touch_records": len(touches),
        "five_records": len(five_rows),
        "counts": counts,
        "touch_classifications": results,
        "unused_five_candidates": unused,
        "order_authority": False,
        "profitability_proven": False,
        "demo_ready": False,
    }
