"""Visibility layer for the single-position BLOCKED_OPEN_POSITION early-return.

The runner blocks new candidate evaluation whenever a position slot is already
held (webhook/runner.py, the `daily_state.has_open_position` gate). That gate is
correct and unchanged — but before this module it left NO journal record: the
early-return fired ahead of every `log_decision` call, so a blocked bar wrote
nothing. The 2026-07-21 MES orphan exploited exactly this — it blinded the
pipeline for the whole 2026-07-22 session and the journal simply showed zero
decisions, indistinguishable from a quiet market (46-day audit: BLOCKED_OPEN_
POSITION was never journaled once).

This module builds the record + classification that makes the block visible. It
is PURE: no I/O, no broker calls, no order actions — it only DESCRIBES why
candidate evaluation did not run. Nothing here can submit, cancel, replace, or
flatten anything.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

# ── Classifications (operator-specified, 2026-07-23) ─────────────────────────
ACTIVE_POSITION_RESOLVING = "ACTIVE_POSITION_RESOLVING"  # open, young, resolving normally
STALE_RESOLVE = "STALE_RESOLVE"                          # open past the resolve threshold
BROKER_LOCAL_STATE_DRIFT = "BROKER_LOCAL_STATE_DRIFT"    # broker flat while local shows open
PIPELINE_BLOCKED = "PIPELINE_BLOCKED"                    # blocked, unclassifiable (no age, etc.)

# A 15m-bar strategy resolves within a few bars; the longest real trade in 46
# days ran 9 bars / ~2.5h. 180 min is a conservative ceiling so ordinary
# multi-bar holds never trip STALE_RESOLVE, but the 46.8h orphan trips it fast.
DEFAULT_STALE_MINUTES = 180.0

# Sentinel: the broker position state is not available in this caller's context.
# The runner's block path deliberately does NOT perform broker I/O (that would
# add a network call to every blocked bar), so it passes UNKNOWN. The reconciler
# — which already holds an authenticated broker read (#303) — can pass a real
# value. UNKNOWN must never be treated as drift.
BROKER_UNKNOWN = "__unknown__"


def _parse(ts) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except Exception:
        return None


def position_age_minutes(open_pos: dict, current_ts) -> Optional[float]:
    """Minutes between the position's open ts and the current bar. None if either
    timestamp is missing/unparseable (→ caller classifies PIPELINE_BLOCKED)."""
    opened = _parse((open_pos or {}).get("ts"))
    now = _parse(current_ts)
    if opened is None or now is None:
        return None
    return (now - opened).total_seconds() / 60.0


def classify_block(
    open_pos: dict,
    current_ts,
    *,
    broker_open: object = BROKER_UNKNOWN,
    stale_minutes: float = DEFAULT_STALE_MINUTES,
) -> str:
    """Classify a single-position block.

    broker_open: True (broker holds it), False (broker flat), or BROKER_UNKNOWN.
    Broker-flat-while-local-open is the dangerous drift (the phantom / erased-win
    class) and wins when we actually have the broker read. UNKNOWN never drifts.
    """
    if broker_open is False:
        return BROKER_LOCAL_STATE_DRIFT
    age = position_age_minutes(open_pos, current_ts)
    if age is None:
        return PIPELINE_BLOCKED
    if age > stale_minutes:
        return STALE_RESOLVE
    return ACTIVE_POSITION_RESOLVING


def _lifecycle_id(open_pos: dict) -> Optional[str]:
    ids = open_pos.get("order_ids") if isinstance(open_pos.get("order_ids"), dict) else {}
    entry = ids.get("entry")
    if entry is not None:
        return str(entry)
    # Paper / no-order-id positions: a stable composite from instrument + open ts.
    inst = open_pos.get("instrument")
    ts = open_pos.get("ts")
    if inst and ts:
        return f"{inst}@{ts}"
    return None


def build_block_record(
    open_pos: dict,
    current_ts,
    *,
    instrument: str,
    session: str,
    broker_open: object = BROKER_UNKNOWN,
    last_reconcile_ts=None,
    stale_minutes: float = DEFAULT_STALE_MINUTES,
) -> dict:
    """Build the journalable visibility record for one blocked bar. Pure."""
    open_pos = open_pos or {}
    classification = classify_block(
        open_pos, current_ts, broker_open=broker_open, stale_minutes=stale_minutes
    )
    age = position_age_minutes(open_pos, current_ts)
    broker_state = (
        "flat" if broker_open is False
        else "open" if broker_open is True
        else "unavailable"
    )
    pos_inst = open_pos.get("instrument")
    pos_dir = open_pos.get("direction")
    if age is not None:
        reason = (
            f"single-position gate: an open {pos_inst} position ({pos_dir}, "
            f"age {age:.0f}m, {classification}) holds the only position slot — "
            f"candidate evaluation for the incoming {instrument} alert did not run."
        )
    else:
        reason = (
            f"single-position gate: an open {pos_inst} position ({classification}) "
            f"holds the only position slot — candidate evaluation for the incoming "
            f"{instrument} alert did not run."
        )
    return {
        "type": "BLOCK_VISIBILITY",
        "blocked_decision": "BLOCKED_OPEN_POSITION",
        "instrument": instrument,
        "session": session,
        "lifecycle_id": _lifecycle_id(open_pos),
        "strategy": open_pos.get("strategy") or (open_pos.get("setup") or {}).get("strategy"),
        "position_instrument": pos_inst,
        "position_direction": pos_dir,
        "local_state": "OPEN",
        "broker_state": broker_state,
        "position_age_minutes": round(age, 1) if age is not None else None,
        "last_reconcile_ts": last_reconcile_ts,
        "classification": classification,
        "reason": reason,
    }


def should_escalate(record: dict, *, stale_minutes: float = DEFAULT_STALE_MINUTES) -> bool:
    """Whether a single block record warrants health escalation. Drift and a
    stale resolve both do; a young resolving position does not."""
    return record.get("classification") in (BROKER_LOCAL_STATE_DRIFT, STALE_RESOLVE)


def summarize_blocks(
    records: list,
    *,
    last_authorized_ts=None,
    stale_minutes: float = DEFAULT_STALE_MINUTES,
) -> dict:
    """Aggregate a day's BLOCK_VISIBILITY records into a health signal. Pure.

    `records` are the day's BLOCK_VISIBILITY rows (each carries a journaled
    `ts`), chronological order not required. `last_authorized_ts` is the
    timestamp of the most recent bar that REACHED evaluation (any decision that
    is not a BLOCKED_* early-return); None if the pipeline evaluated nothing all
    day.

    The pipeline-blind signal operates on the TRAILING run of blocked bars since
    that last evaluated bar — NOT whole-day totals. A normal morning (40 bars)
    followed by a stuck afternoon (30 consecutive blocked bars) must still trip:
    whole-day `blocked >= claimed` would read 30 >= 70 and stay silent, but the
    trailing run spans hours and is caught here.
    """
    by_class: dict = {}
    worst_age = 0.0
    for r in records or []:
        c = r.get("classification")
        by_class[c] = by_class.get(c, 0) + 1
        a = r.get("position_age_minutes")
        if isinstance(a, (int, float)) and a > worst_age:
            worst_age = a

    # Trailing consecutive-blocked run = blocked bars stamped after the last
    # evaluated bar. Its wall-clock span is how long the pipeline has gone
    # blind. Reuses the single stale threshold (no second constant).
    cutoff = _parse(last_authorized_ts)
    trailing = []
    for r in records or []:
        rt = _parse(r.get("ts"))
        if rt is None:
            continue
        if cutoff is None or rt > cutoff:
            trailing.append(rt)
    trailing.sort()
    blind_span_min = (
        (trailing[-1] - trailing[0]).total_seconds() / 60.0 if len(trailing) >= 2 else 0.0
    )

    return {
        "blocked_bars": sum(by_class.values()),
        "by_classification": by_class,
        "worst_position_age_minutes": worst_age,
        "has_stale_resolve": by_class.get(STALE_RESOLVE, 0) > 0,
        "has_state_drift": by_class.get(BROKER_LOCAL_STATE_DRIFT, 0) > 0,
        "trailing_blocked_bars": len(trailing),
        "trailing_blind_minutes": round(blind_span_min, 1),
        # Bars kept arriving and NONE reached evaluation for >= the stale window
        # (the 2026-07-22 blinded-pipeline signature), measured on the trailing
        # run so a partial-day blind window still fires.
        "bars_without_decisions": blind_span_min >= stale_minutes and len(trailing) >= 2,
    }
