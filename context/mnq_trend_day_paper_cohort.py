"""MNQ trend-day existing-family forward paper cohort.

Purpose
-------
Collect realistic prospective paper evidence for the EXISTING MNQ 15m LONG
families that repeatedly appear on sustained up-trend days, without changing
the executable strategy stack.

This is not a new strategy and it has no promotion path.  Each family is an
independent hypothetical sub-lane so performance is never improved by a
post-hoc ranker or cross-family cherry-pick.

Default is OFF.  Activation requires BOTH:

    MNQ_TREND_DAY_PAPER_MODE=paper_sim
    MNQ_TREND_DAY_PAPER_EPOCH_START=<offset-aware ISO timestamp>

Any other posture performs no file I/O and no evaluation.

Frozen mechanics
----------------
* instrument: MNQ only
* decision timeframe: 15m only
* direction: LONG only
* existing shadow candidates only; detector definitions are untouched
* four independent sub-lanes:
    - ema_pullback_trend
    - impulse_first_pullback_observed
    - strat_22_continuation_observed
    - trend_consolidation_break_observed
* original candidate entry / stop / target are preserved exactly
* canonical PaperBroker IOC at decision-bar close
* 32-tick MNQ IOC tolerance
* 1 adverse tick entry slippage
* pessimistic same-bar handling
* one open position at a time PER independent family lane
* maximum 3 FILLED trades per observation day PER independent family lane
* no breakeven, runner, averaging, target rewrite, stop rewrite, or fallback
* positions expire at the CME observation-day roll

The four sub-lanes are alternatives, not a combined portfolio.  Their P&L must
never be summed to imply one executable system.

Paper-only by construction: execution reuses the Asia cohort's canonical
PaperBroker helpers.  There is no external broker lookup, Tradovate import, or
order route in this module.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional

from context.asia_d_ema_paper_cohort import (
    INSTRUMENT,
    _bar_context,
    _et_hour,
    _outcome_fields,
    _resolve_forward,
    advance,
    instrument_root,
    ioc_open,
    is_decision_timeframe,
    valid_geometry,
)
from execution.cross_instrument_observation import observation_day

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

CAMPAIGN_ID = "mnq_trend_day_existing_families_2026_09_v1"
MODE_ENV = "MNQ_TREND_DAY_PAPER_MODE"
EPOCH_ENV = "MNQ_TREND_DAY_PAPER_EPOCH_START"
MODE_OFF = "off"
MODE_PAPER = "paper_sim"
DEFAULT_MODE = MODE_OFF
VALID_MODES = (MODE_OFF, MODE_PAPER)

STRATEGIES = (
    "ema_pullback_trend",
    "impulse_first_pullback_observed",
    "strat_22_continuation_observed",
    "trend_consolidation_break_observed",
)
LANES = STRATEGIES
TIMEFRAME_MINUTES = 15
MAX_FILLED_TRADES_PER_DAY = 3
MAX_SEEN_KEYS = 4000
STATE_VERSION = 1
_EVIDENCE_TAIL_LINES = 1024
_POSITION_REQUIRED = (
    "candidate_key",
    "lane",
    "strategy",
    "direction",
    "entry",
    "stop",
    "target",
    "actual_entry",
    "entry_ts",
    "day",
    "paper_order_id",
)


def mode(cfg=None) -> str:
    raw = getattr(cfg, "mnq_trend_day_paper_mode", None) if cfg is not None else None
    if raw is None:
        raw = os.getenv(MODE_ENV, DEFAULT_MODE)
    value = str(raw or DEFAULT_MODE).strip().lower()
    return MODE_PAPER if value == MODE_PAPER else MODE_OFF


def epoch_start(cfg=None) -> Optional[str]:
    raw = getattr(cfg, "mnq_trend_day_paper_epoch_start", None) if cfg is not None else None
    if raw is None:
        raw = os.getenv(EPOCH_ENV)
    text = str(raw).strip() if raw is not None else ""
    return text or None


def epoch(cfg=None) -> Optional[datetime]:
    raw = epoch_start(cfg)
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else None


def is_active(cfg=None) -> bool:
    return mode(cfg) == MODE_PAPER and epoch(cfg) is not None


def lane_dir(log_dir) -> Path:
    return Path(log_dir) / "mnq_trend_day_cohort"


def evidence_path(log_dir) -> Path:
    return lane_dir(log_dir) / "evidence.jsonl"


def state_path(log_dir) -> Path:
    return lane_dir(log_dir) / "state.json"


class LaneStateError(RuntimeError):
    pass


def _empty_counts() -> dict[str, dict[str, Any]]:
    return {lane: {"day": None, "fills": 0} for lane in LANES}


def _empty_state() -> dict[str, Any]:
    return {
        "version": STATE_VERSION,
        "campaign_id": CAMPAIGN_ID,
        "seen": [],
        "positions": {lane: None for lane in LANES},
        "trade_counts": _empty_counts(),
        "pending_events": [],
    }


def load_state(log_dir) -> dict[str, Any]:
    path = state_path(log_dir)
    if not path.exists():
        return _empty_state()
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise LaneStateError(f"lane state unreadable: {exc}") from exc
    if not isinstance(raw, dict):
        raise LaneStateError("lane state is not an object")
    if raw.get("campaign_id") != CAMPAIGN_ID or raw.get("version") != STATE_VERSION:
        raise LaneStateError("lane state campaign/version mismatch")

    seen = raw.get("seen")
    positions = raw.get("positions")
    trade_counts = raw.get("trade_counts")
    pending = raw.get("pending_events", [])

    if not isinstance(seen, list):
        raise LaneStateError("lane state 'seen' is malformed")
    if not isinstance(positions, dict) or set(positions) != set(LANES):
        raise LaneStateError("lane state 'positions' is malformed")
    if not isinstance(trade_counts, dict) or set(trade_counts) != set(LANES):
        raise LaneStateError("lane state 'trade_counts' is malformed")
    if not isinstance(pending, list) or any(
        not isinstance(e, dict) or not e.get("event_id") for e in pending
    ):
        raise LaneStateError("lane state 'pending_events' is malformed")

    for lane, position in positions.items():
        if position is None:
            continue
        if not isinstance(position, dict) or any(position.get(k) is None for k in _POSITION_REQUIRED):
            raise LaneStateError(f"lane position for {lane!r} is incomplete")
        if position.get("lane") != lane:
            raise LaneStateError(f"lane position mismatch for {lane!r}")

    normalized_counts: dict[str, dict[str, Any]] = {}
    for lane, row in trade_counts.items():
        if not isinstance(row, dict):
            raise LaneStateError(f"lane count for {lane!r} is malformed")
        try:
            fills = int(row.get("fills") or 0)
        except (TypeError, ValueError) as exc:
            raise LaneStateError(f"lane count for {lane!r} is malformed") from exc
        if fills < 0:
            raise LaneStateError(f"lane count for {lane!r} is negative")
        normalized_counts[lane] = {"day": row.get("day"), "fills": fills}

    return {
        "version": STATE_VERSION,
        "campaign_id": CAMPAIGN_ID,
        "seen": [str(k) for k in seen][-MAX_SEEN_KEYS:],
        "positions": {lane: positions[lane] for lane in LANES},
        "trade_counts": normalized_counts,
        "pending_events": pending,
    }


def save_state(log_dir, state: dict[str, Any]) -> None:
    path = state_path(log_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(state, handle, sort_keys=True)
            handle.write("\n")
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def append_event(log_dir, event: dict[str, Any]) -> None:
    path = evidence_path(log_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            handle.write(json.dumps(event, separators=(",", ":"), sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        finally:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _written_event_ids(log_dir) -> set[str]:
    path = evidence_path(log_dir)
    if not path.exists():
        return set()
    try:
        lines = path.read_text().splitlines()[-_EVIDENCE_TAIL_LINES:]
    except OSError:
        return set()
    out: set[str] = set()
    for line in lines:
        try:
            out.add(str(json.loads(line).get("event_id") or ""))
        except (json.JSONDecodeError, AttributeError):
            continue
    return out


def _flush_pending(log_dir, state: dict[str, Any]) -> None:
    pending = list(state.get("pending_events") or [])
    if not pending:
        return
    written = _written_event_ids(log_dir)
    for event in pending:
        if event["event_id"] in written:
            continue
        append_event(log_dir, event)
        written.add(event["event_id"])
    state["pending_events"] = []
    save_state(log_dir, state)


def commit(log_dir, state: dict[str, Any], events: list[dict[str, Any]]) -> None:
    state["pending_events"] = list(events)
    save_state(log_dir, state)
    _flush_pending(log_dir, state)


def recover_pending(log_dir, state: dict[str, Any]) -> int:
    count = len(state.get("pending_events") or [])
    if count:
        logger.warning("mnq trend-day cohort: replaying %d pending evidence event(s)", count)
        _flush_pending(log_dir, state)
    return count


def candidate_key(day: str, candidate: dict) -> str:
    return "|".join(
        [
            day,
            str(candidate.get("strategy")),
            str(candidate.get("direction")),
            repr(float(candidate["entry"])),
            repr(float(candidate["stop"])),
            repr(float(candidate["target"])),
        ]
    )


def candidates_for_bar(
    shadow_candidates: list[dict], day: str, seen: set[str]
) -> list[dict[str, Any]]:
    """Return untouched existing LONG candidates, one identity per observation day."""
    picks: list[dict[str, Any]] = []
    for candidate in shadow_candidates or []:
        strategy = str(candidate.get("strategy") or "")
        if strategy not in STRATEGIES:
            continue
        if str(candidate.get("direction") or "").upper() != "LONG":
            continue
        if not valid_geometry(candidate):
            continue
        key = candidate_key(day, candidate)
        if key in seen:
            continue
        seen.add(key)
        picks.append(
            {
                "candidate_key": key,
                "lane": strategy,
                "strategy": strategy,
                "direction": "LONG",
                "entry": float(candidate["entry"]),
                "stop": float(candidate["stop"]),
                "target": float(candidate["target"]),
            }
        )
    return picks


def _event_id(event: str, ts: str, lane: Optional[str], candidate_key_value: Optional[str]) -> str:
    return f"{CAMPAIGN_ID}|{event}|{ts}|{lane or '-'}|{candidate_key_value or '-'}"


def _base_event(event: str, ts: str, day: str, lane: Optional[str], context: dict) -> dict[str, Any]:
    return {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "campaign_id": CAMPAIGN_ID,
        "event": event,
        "lane": lane,
        "instrument": INSTRUMENT,
        "timeframe": TIMEFRAME_MINUTES,
        "session": context.get("session"),
        "ts": ts,
        "day": day,
        "et_hour": _et_hour(ts),
        "market_condition": context.get("market_condition"),
        "structural_market_condition": context.get("structural_market_condition"),
        "structural_direction": context.get("structural_direction"),
        "trend_direction": (context.get("trend") or {}).get("direction"),
        "trend_strength": (context.get("trend") or {}).get("strength"),
        "broker_route": "PaperBroker",
        "observation_only": True,
        "execution_authority": False,
        "normal_execution_affected": False,
        "independent_family_lane": True,
    }


def _candidate_fields(candidate: dict) -> dict[str, Any]:
    return {
        key: candidate[key]
        for key in ("candidate_key", "lane", "strategy", "direction", "entry", "stop", "target")
    }


def _position_fields(position: dict) -> dict[str, Any]:
    return {
        **_candidate_fields(position),
        "actual_entry": position["actual_entry"],
        "paper_order_id": position["paper_order_id"],
        "entry_ts": position["entry_ts"],
        "entry_et_hour": position.get("et_hour"),
    }


def _reset_count_for_day(counter: dict[str, Any], day: str) -> dict[str, Any]:
    if str(counter.get("day") or "") != day:
        return {"day": day, "fills": 0}
    return {"day": day, "fills": int(counter.get("fills") or 0)}


def process_bar(
    *,
    state,
    cfg,
    log_dir,
    shadow_candidates: Optional[list[dict]],
    for_date: Optional[date] = None,
) -> Optional[dict[str, Any]]:
    """Advance the four independent paper sub-lanes by one authoritative 15m MNQ bar."""
    if not is_active(cfg):
        return None

    instrument = getattr(state, "instrument", None)
    ohlc = getattr(state, "ohlc", None)
    if instrument_root(instrument) != INSTRUMENT or ohlc is None:
        return None
    if not is_decision_timeframe(getattr(ohlc, "timeframe", None)):
        return None

    ts = state.timestamp.isoformat()
    day = observation_day(INSTRUMENT, ts, for_date=for_date).isoformat()
    bar_dt = state.timestamp if state.timestamp.tzinfo else state.timestamp.replace(tzinfo=timezone.utc)
    epoch_dt = epoch(cfg)
    if epoch_dt is None or bar_dt < epoch_dt:
        return {
            "campaign_id": CAMPAIGN_ID,
            "lane_result": "PRE_EPOCH",
            "events": [],
            "positions_open": {lane: False for lane in LANES},
        }

    context = _bar_context(state)
    bar = {
        "ts": ts,
        "open": float(ohlc.open),
        "high": float(ohlc.high),
        "low": float(ohlc.low),
        "close": float(ohlc.close),
    }

    try:
        lane_state = load_state(log_dir)
        recovered = recover_pending(log_dir, lane_state)
    except (LaneStateError, OSError) as exc:
        logger.warning("mnq trend-day cohort: state invalid, failing closed: %s", exc)
        return {
            "campaign_id": CAMPAIGN_ID,
            "lane_result": "STATE_INVALID",
            "detail": str(exc),
            "events": [],
        }

    events: list[dict[str, Any]] = []
    positions = lane_state["positions"]
    counts = lane_state["trade_counts"]

    # Resolve / expire each family independently.
    for lane in LANES:
        counts[lane] = _reset_count_for_day(counts[lane], day)
        position = positions.get(lane)
        if position is None or ts <= str(position["entry_ts"]):
            continue
        if str(position["day"]) != day:
            outcome = _resolve_forward(position, [], roll_bar_ts=ts)
            events.append(
                {
                    **_base_event("OUTCOME", ts, day, lane, context),
                    **_position_fields(position),
                    **outcome,
                }
            )
            positions[lane] = None
            continue

        terminal = advance(position, bar)
        if terminal is None:
            positions[lane] = position
            continue
        actual_risk = abs(float(position["actual_entry"]) - float(position["stop"]))
        outcome = _outcome_fields(position, terminal, ts, actual_risk)
        events.append(
            {
                **_base_event("OUTCOME", ts, day, lane, context),
                **_position_fields(position),
                **outcome,
            }
        )
        positions[lane] = None

    seen_before = set(lane_state.get("seen") or [])
    seen = set(seen_before)
    picks = candidates_for_bar(shadow_candidates or [], day, seen)
    lane_state["seen"] = (list(lane_state.get("seen") or []) + sorted(seen - seen_before))[
        -MAX_SEEN_KEYS:
    ]

    for candidate in sorted(picks, key=lambda row: (row["lane"], row["candidate_key"])):
        lane = candidate["lane"]
        if positions.get(lane) is not None:
            events.append(
                {
                    **_base_event("CANDIDATE_SKIPPED_BUSY", ts, day, lane, context),
                    **_candidate_fields(candidate),
                    "open_candidate_key": positions[lane]["candidate_key"],
                }
            )
            continue

        count = _reset_count_for_day(counts[lane], day)
        counts[lane] = count
        if count["fills"] >= MAX_FILLED_TRADES_PER_DAY:
            events.append(
                {
                    **_base_event("CANDIDATE_SKIPPED_DAILY_CAP", ts, day, lane, context),
                    **_candidate_fields(candidate),
                    "filled_trades_today": count["fills"],
                    "daily_cap": MAX_FILLED_TRADES_PER_DAY,
                }
            )
            continue

        fill = ioc_open(candidate, bar["close"])
        if fill.result != "OPEN":
            events.append(
                {
                    **_base_event("NO_FILL", ts, day, lane, context),
                    **_candidate_fields(candidate),
                    "result": "NO_FILL" if fill.result == "CANCELLED" else fill.result,
                    "exit_reason": fill.exit_reason
                    or fill.no_fill_reason
                    or "ENTRY_NOT_FILLED",
                    "decision_close": bar["close"],
                    "entry_price": fill.entry_price,
                }
            )
            continue

        position = {
            **candidate,
            "actual_entry": float(fill.entry_price),
            "paper_order_id": fill.paper_order_id,
            "entry_ts": ts,
            "day": day,
            "et_hour": _et_hour(ts),
            "mae_points": 0.0,
            "mfe_points": 0.0,
            "bars_seen": 0,
        }
        positions[lane] = position
        counts[lane] = {"day": day, "fills": count["fills"] + 1}
        events.append(
            {
                **_base_event("CANDIDATE_FILLED", ts, day, lane, context),
                **_position_fields(position),
                "decision_close": bar["close"],
                "filled_trades_today": counts[lane]["fills"],
                "daily_cap": MAX_FILLED_TRADES_PER_DAY,
            }
        )

    for event in events:
        event["event_id"] = _event_id(
            event["event"], ts, event.get("lane"), event.get("candidate_key")
        )

    commit(log_dir, lane_state, events)
    return {
        "campaign_id": CAMPAIGN_ID,
        "lane_result": "ADVANCED",
        "positions_open": {lane: positions.get(lane) is not None for lane in LANES},
        "filled_trades_today": {lane: counts[lane]["fills"] for lane in LANES},
        "recovered_pending_events": recovered,
        "events": [
            {
                "event": event["event"],
                "lane": event.get("lane"),
                "candidate_key": event.get("candidate_key"),
            }
            for event in events
        ],
    }
