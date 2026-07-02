"""Causal LIVE resolver for journaled observe-only candidates.

Replay already resolves shadow candidates inline (replay_engine.py passes the
rest of the day's candles to resolve_shadow_candidate). The live path only
journals candidates, so /status/evidence-readiness showed shadow_setups and
range_signal stuck at resolved_examples=0 forever — the lanes could never earn
the gate. This module closes that loop CAUSALLY: on each new live bar it
resolves pending candidates from PRIOR bars against only the bars ingested
since, and appends the outcome as a new journal row.

Guarantees:
  • Read-only with respect to trading: never touches decisions, risk, sizing,
    or execution. The caller (webhook/runner.py) wraps it fail-soft.
  • Causal: a candidate journaled on bar T is only ever measured against bars
    with ts > T that have ALREADY been ingested — there is no future data to
    look at. Same-day-only forward window, matching replay semantics.
  • Additive-only journal writes: outcomes are NEW rows of type
    "SHADOW_OUTCOME" carrying a `shadow_outcome` field. They deliberately do
    NOT use the `outcome` or `decision` keys, and their `ts` is wall-clock —
    so claim_bar, daily-state reconstruction, and trade-pair matching all
    ignore them.
  • Terminal-once: WIN/LOSS is final on the bar that produces it (first-hit
    scan order cannot change with more bars). NO_FILL/OPEN stay pending until
    the candidate's journal day is over (first bar of a later day finalizes
    them), matching the replay end-of-day window.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from context.bar_history import BarHistory, _parse_dt
from journal.journal_logger import JournalLogger
from strategy.shadow_setups import ShadowSetupCandidate, resolve_shadow_candidate

logger = logging.getLogger(__name__)

# How many calendar days back to scan for still-pending candidates. Covers a
# weekend gap (Friday candidates finalized by Monday's first bar). Candidates
# older than this are abandoned unresolved — acceptable for an evidence lane.
LOOKBACK_DAYS = 4

# Upper bound on same-day forward bars read per candidate (15m RTH+ETH day is
# well under this).
_MAX_DAY_BARS = 500


def _candidate_key(
    lane: str,
    instrument: str,
    bar_ts: str,
    strategy: str,
    direction: str,
    entry: float,
) -> str:
    return f"{lane}|{instrument}|{bar_ts}|{strategy}|{direction}|{entry}"


def _pending_from_row(row: dict, instrument: str) -> list[dict]:
    """Extract resolvable candidates from one journal row (both lanes)."""
    if str(row.get("instrument") or "") != instrument:
        return []
    bar_ts = row.get("ts") or row.get("timestamp")
    if not isinstance(bar_ts, str) or _parse_dt(bar_ts) is None:
        return []
    out: list[dict] = []

    raw_candidates = row.get("shadow_candidates")
    if isinstance(raw_candidates, list):
        for cand in raw_candidates:
            parsed = _bracket(cand, "strategy")
            if parsed:
                out.append({"lane": "shadow_setups", "bar_ts": bar_ts, **parsed})

    # NO_TRADE rows journal `range_signal`; TRADE rows journal the same dict as
    # `shadow_range_signal` — one lane, first present key wins.
    for field in ("range_signal", "shadow_range_signal"):
        range_signal = row.get(field)
        if isinstance(range_signal, dict):
            parsed = _range_bracket(range_signal)
            if parsed:
                out.append({"lane": "range_signal", "bar_ts": bar_ts, **parsed})
            break
    return out


def _bracket(cand: Any, strategy_field: str) -> Optional[dict]:
    if not isinstance(cand, dict):
        return None
    direction = str(cand.get("direction") or "").upper()
    if direction not in ("LONG", "SHORT"):
        return None
    try:
        entry = float(cand["entry"])
        stop = float(cand["stop"])
        target = float(cand["target"])
    except (KeyError, TypeError, ValueError):
        return None
    strategy = str(cand.get(strategy_field) or "unknown")
    return {
        "strategy": strategy,
        "direction": direction,
        "entry": entry,
        "stop": stop,
        "target": target,
    }


def _range_bracket(signal: dict) -> Optional[dict]:
    """A RangeSignal is resolvable only when it carries a full bracket."""
    direction = str(signal.get("direction") or "").upper()
    if direction not in ("LONG", "SHORT"):
        return None
    entry = signal.get("entry_candidate")
    stop = signal.get("stop_candidate")
    target = signal.get("target_candidate")
    if entry is None or stop is None or target is None:
        return None
    try:
        entry, stop, target = float(entry), float(stop), float(target)
    except (TypeError, ValueError):
        return None
    strategy = str(signal.get("signal_type") or "range_signal").lower()
    return {
        "strategy": strategy,
        "direction": direction,
        "entry": entry,
        "stop": stop,
        "target": target,
    }


def resolve_pending_shadow_outcomes(
    *,
    log_dir: str,
    instrument: str,
    current_bar_ts: str,
    for_date: Optional[date] = None,
    lookback_days: int = LOOKBACK_DAYS,
) -> list[dict]:
    """Resolve pending journaled candidates against bars ingested so far.

    Called once per ingested live bar, AFTER the bar has been recorded to
    BarHistory and the bar claim has been taken. Returns the outcome rows
    appended this call (empty list when nothing newly resolved).
    """
    current_dt = _parse_dt(current_bar_ts)
    if current_dt is None:
        return []
    today = for_date or current_dt.date()
    journal = JournalLogger(log_dir=log_dir)
    bar_hist = BarHistory(log_dir=log_dir)

    pending: list[dict] = []
    resolved_keys: set[str] = set()
    days = [today - timedelta(days=k) for k in range(max(1, lookback_days))]
    for d in days:
        for row in journal.read_day(d):
            if not isinstance(row, dict):
                continue
            if row.get("type") == "SHADOW_OUTCOME":
                key = row.get("candidate_key")
                if isinstance(key, str):
                    resolved_keys.add(key)
                continue
            for cand in _pending_from_row(row, instrument):
                cand["candidate_day"] = d
                pending.append(cand)

    appended: list[dict] = []
    for cand in pending:
        key = _candidate_key(
            cand["lane"],
            instrument,
            cand["bar_ts"],
            cand["strategy"],
            cand["direction"],
            cand["entry"],
        )
        if key in resolved_keys:
            continue
        cand_dt = _parse_dt(cand["bar_ts"])
        if cand_dt is None or cand_dt >= current_dt:
            continue  # current or malformed bar — nothing forward of it yet

        # Same-day forward window, candidate's own bar excluded (replay parity:
        # candles[idx + 1:]). Every bar here was already ingested — causal by
        # construction.
        cand_day = cand["candidate_day"]
        day_bars = bar_hist.recent(
            instrument, _MAX_DAY_BARS, for_date=cand_day, lookback_days=1
        )
        forward: list[tuple[float, float]] = []
        for bar in day_bars:
            bar_dt = _parse_dt(str(bar.get("ts") or ""))
            if bar_dt is None or bar_dt <= cand_dt or bar_dt > current_dt:
                continue
            try:
                forward.append((float(bar["high"]), float(bar["low"])))
            except (KeyError, TypeError, ValueError):
                continue

        day_complete = cand_day < today
        if not forward and not day_complete:
            continue

        candidate = ShadowSetupCandidate(
            strategy=cand["strategy"],
            direction=cand["direction"],
            entry=cand["entry"],
            stop=cand["stop"],
            target=cand["target"],
            rr_ratio=0.0,
            risk_tier="",
            size_multiplier=0.0,
            notes="",
        )
        outcome = resolve_shadow_candidate(candidate, forward, instrument=instrument)
        if outcome.result not in ("WIN", "LOSS") and not day_complete:
            continue  # NO_FILL/OPEN can still change while the day is live

        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "type": "SHADOW_OUTCOME",
            "lane": cand["lane"],
            "instrument": instrument,
            "strategy": cand["strategy"],
            "direction": cand["direction"],
            "entry": cand["entry"],
            "stop": cand["stop"],
            "target": cand["target"],
            "candidate_key": key,
            "candidate_bar_ts": cand["bar_ts"],
            "candidate_day": cand_day.isoformat(),
            "resolved_at_bar_ts": current_bar_ts,
            "forward_bars_used": len(forward),
            "final": True,
            "shadow_outcome": outcome.to_dict(),
        }
        journal.log_shadow_outcome(record, for_date=today)
        resolved_keys.add(key)
        appended.append(record)

    if appended:
        logger.info(
            "shadow resolver: %d candidate(s) resolved for %s",
            len(appended),
            instrument,
        )
    return appended
