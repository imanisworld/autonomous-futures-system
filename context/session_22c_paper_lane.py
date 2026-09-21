"""Session-scoped 2-2 continuation forward paper lane (MNQ, 15m) — isolated sibling.

Pre-registered 2026-09-21 as hypotheses H6 and H7 in
``docs/prereg-mnq-volume-label-and-sunday-reopen-2026-09-21.md`` after the
313-day replay grid. It exists so the two grid cells that beat every random
permutation can be observed PROSPECTIVELY, with their own campaign id, epoch,
evidence file and state file. It never touches the real book, the MES 1-2-2
lane, the Asia D+EMA cohort, the MNQ Strat evidence lanes, the forward A/B
campaign or the cross-instrument campaign.

Default is OFF. ``SESSION_22C_PAPER_MODE=off`` (the default, and any value that
is not exactly ``paper_sim``) means this module performs NO file I/O and NO
evaluation at all: ``process_bar`` returns ``None`` before touching the disk.
Activation requires BOTH ``SESSION_22C_PAPER_MODE=paper_sim`` AND a valid
offset-aware ``SESSION_22C_PAPER_EPOCH_START``. Anything else fails closed.

Two sub-lanes, each with ONE open position at a time, resolved independently:

* ``asia``   (H6): decision bar ``session == "asian"`` and NOT inside the Sunday
             reopen window; candidate direction must equal the payload EMA
             trend (``trend.direction`` UP->LONG / DOWN->SHORT); the Pine
             ``market_condition`` label must NOT be CHOPPY or DEAD (TRENDING
             and RANGE_BOUND both trade — the by-label split of the one-at-a-
             time series showed RANGE_BOUND PF 1.42 / TRENDING 1.26 vs CHOPPY
             0.83 / DEAD 0.89, so the TRENDING-only gate is wrong for this
             cell and the CHOPPY/DEAD gate is right). Target is re-anchored to
             **1.5R** from the candidate's own entry/stop.
* ``sunday`` (H7): decision bar inside Sun 22:00Z <= ts < Mon 01:00Z (the first
             three hours of the CME Globex reopen); NO condition / EMA filter.
             Target re-anchored to **1.0R**.

Shared definition (identical mechanics to ``context/asia_d_ema_paper_cohort.py``,
whose fill / advance / resolve functions are imported, not re-implemented):

* candidate source   = the runner's ``shadow_candidates`` for the decision bar,
                       strategy ``strat_22_continuation_observed`` ONLY, with
                       structurally valid geometry;
* persistent-geometry dedupe: within one observation day, an identical
  (strategy, direction, entry, stop) is ONE setup per sub-lane — the target is
  ours, so it is not part of the key;
* fill               = canonical PaperBroker ``ioc_limit`` evaluated ONCE at the
                       decision bar close, 1 adverse tick, 32-tick tolerance,
                       pessimistic both-hit, no breakeven, no runner;
* resolution         = strictly later 15m bars of the SAME observation day
                       (CME 18:00 ET boundary via ``observation_day``); an open
                       position at the day roll is EXPIRED, never carried. This
                       is more generous than the grid's 48-bar horizon for the
                       Asia sub-lane and identical for the Sunday one; recorded
                       per row so the scorer can truncate if it wants to.
* a candidate arriving while that sub-lane's position is open is journaled as
  ``CANDIDATE_SKIPPED_BUSY`` and never traded.

Paper-only by construction: the only execution object reachable from this
module is ``execution.paper_broker.PaperBroker`` via the cohort's ``ioc_open`` /
``advance``. No broker interface lookup, no Tradovate import, no order route.

Persistence is write-ahead and idempotent (same scheme as the cohort): the
bar's events are saved inside state.json as ``pending_events`` before any
evidence line is appended, each event carries a deterministic ``event_id``,
and the next call replays only the events that are not yet durable.
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

try:  # POSIX advisory lock for the append-only evidence file
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX
    fcntl = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

CAMPAIGN_ID = "session_22c_paper_2026_09_v1"
STRATEGY = "strat_22_continuation_observed"
TIMEFRAME_MINUTES = 15

MODE_ENV = "SESSION_22C_PAPER_MODE"
EPOCH_ENV = "SESSION_22C_PAPER_EPOCH_START"
MODE_OFF = "off"
MODE_PAPER = "paper_sim"
DEFAULT_MODE = MODE_OFF
VALID_MODES = (MODE_OFF, MODE_PAPER)

LANE_ASIA = "asia"
LANE_SUNDAY = "sunday"
LANES = (LANE_ASIA, LANE_SUNDAY)
# Registered targets (H6 / H7). Re-anchored from the candidate's own risk.
TARGET_R = {LANE_ASIA: 1.5, LANE_SUNDAY: 1.0}
ASIA_SESSION = "asian"
# Sunday reopen window, UTC: Sun 22:00 <= ts < Mon 01:00.
SUNDAY_OPEN_HOUR_UTC = 22
MONDAY_CLOSE_HOUR_UTC = 1

_EMA_TO_SIDE = {"UP": "LONG", "DOWN": "SHORT"}
# Asia sub-lane: labels that are NOT tradable (prereg H6 amendment 2026-09-21).
ASIA_EXCLUDED_LABELS = frozenset({"CHOPPY", "DEAD"})
MAX_SEEN_KEYS = 2000
STATE_VERSION = 1
_POSITION_REQUIRED = (
    "candidate_key", "strategy", "direction", "entry", "stop", "target",
    "actual_entry", "entry_ts", "day", "paper_order_id", "lane",
)
_EVIDENCE_TAIL_LINES = 512


# ─────────────────────────────── configuration ──────────────────────────────


def mode(cfg=None) -> str:
    """``paper_sim`` only when that exact token is configured; everything else is OFF."""
    raw = getattr(cfg, "session_22c_paper_mode", None) if cfg is not None else None
    if raw is None:
        raw = os.getenv(MODE_ENV, DEFAULT_MODE)
    value = str(raw or DEFAULT_MODE).strip().lower()
    return MODE_PAPER if value == MODE_PAPER else MODE_OFF


def epoch_start(cfg=None) -> Optional[str]:
    raw = getattr(cfg, "session_22c_paper_epoch_start", None) if cfg is not None else None
    if raw is None:
        raw = os.getenv(EPOCH_ENV)
    text = str(raw).strip() if raw is not None else ""
    return text or None


def epoch(cfg=None) -> Optional[datetime]:
    """Offset-aware epoch start, or None (naive or unparsable timestamps are rejected)."""
    raw = epoch_start(cfg)
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else None


def is_active(cfg=None) -> bool:
    """Fail closed: explicit ``paper_sim`` AND a valid offset-aware epoch."""
    return mode(cfg) == MODE_PAPER and epoch(cfg) is not None


# ─────────────────────────────── files ──────────────────────────────────────


def lane_dir(log_dir) -> Path:
    return Path(log_dir) / "session_22c_lane"


def evidence_path(log_dir) -> Path:
    return lane_dir(log_dir) / "evidence.jsonl"


def state_path(log_dir) -> Path:
    return lane_dir(log_dir) / "state.json"


class LaneStateError(RuntimeError):
    """The persisted lane state is unreadable or malformed — fail closed."""


def _empty_state() -> dict[str, Any]:
    return {
        "version": STATE_VERSION,
        "campaign_id": CAMPAIGN_ID,
        "seen": [],
        "positions": {LANE_ASIA: None, LANE_SUNDAY: None},
        "pending_events": [],
    }


def load_state(log_dir) -> dict[str, Any]:
    """A MISSING state file is a fresh lane. Anything unreadable or malformed raises."""
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
    pending = raw.get("pending_events", [])
    if not isinstance(seen, list):
        raise LaneStateError("lane state 'seen' is not a list")
    if not isinstance(positions, dict) or set(positions) != set(LANES):
        raise LaneStateError("lane state 'positions' is malformed")
    if not isinstance(pending, list) or any(
        not isinstance(e, dict) or not e.get("event_id") for e in pending
    ):
        raise LaneStateError("lane state 'pending_events' is malformed")
    for lane, position in positions.items():
        if position is None:
            continue
        if not isinstance(position, dict) or any(position.get(k) is None for k in _POSITION_REQUIRED):
            raise LaneStateError(f"lane state position for {lane!r} is incomplete")
        if position.get("lane") != lane:
            raise LaneStateError(f"lane state position for {lane!r} carries lane {position.get('lane')!r}")
    return {
        "version": STATE_VERSION,
        "campaign_id": CAMPAIGN_ID,
        "seen": [str(k) for k in seen][-MAX_SEEN_KEYS:],
        "positions": {lane: positions[lane] for lane in LANES},
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
    ids: set[str] = set()
    for line in lines:
        try:
            ids.add(str(json.loads(line).get("event_id") or ""))
        except (json.JSONDecodeError, AttributeError):
            continue
    return ids


def commit(log_dir, state: dict[str, Any], events: list[dict[str, Any]]) -> None:
    """Write-ahead in state, then evidence; idempotent on replay (see cohort)."""
    state["pending_events"] = list(events)
    save_state(log_dir, state)
    _flush_pending(log_dir, state)


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


def recover_pending(log_dir, state: dict[str, Any]) -> int:
    count = len(state.get("pending_events") or [])
    if count:
        logger.warning("session_22c lane: replaying %d pending evidence event(s)", count)
        _flush_pending(log_dir, state)
    return count


# ─────────────────────────────── definition ─────────────────────────────────


def in_sunday_window(ts: str) -> bool:
    """Sun 22:00Z <= ts < Mon 01:00Z, evaluated in UTC."""
    try:
        parsed = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    parsed = parsed.astimezone(timezone.utc)
    if parsed.weekday() == 6 and parsed.hour >= SUNDAY_OPEN_HOUR_UTC:
        return True
    return parsed.weekday() == 0 and parsed.hour < MONDAY_CLOSE_HOUR_UTC


def lane_for_bar(ts: str, session: Optional[str]) -> Optional[str]:
    """Which sub-lane (if any) this decision bar belongs to. Sunday takes precedence."""
    if in_sunday_window(ts):
        return LANE_SUNDAY
    if str(session or "") == ASIA_SESSION:
        return LANE_ASIA
    return None


def ema_side(context: dict) -> Optional[str]:
    trend = context.get("trend") or {}
    return _EMA_TO_SIDE.get(str(trend.get("direction") or "").upper())


def candidate_key(day: str, lane: str, candidate: dict) -> str:
    """Identical (strategy, direction, entry, stop) within one day and sub-lane is one setup."""
    return "|".join(
        [
            day,
            lane,
            str(candidate.get("strategy")),
            str(candidate.get("direction")),
            repr(float(candidate["entry"])),
            repr(float(candidate["stop"])),
        ]
    )


def reanchor_target(candidate: dict, target_r: float) -> float:
    entry = float(candidate["entry"])
    risk = abs(entry - float(candidate["stop"]))
    direction = str(candidate.get("direction") or "").upper()
    return entry + target_r * risk if direction == "LONG" else entry - target_r * risk


def asia_label_ok(context: dict) -> bool:
    """TRENDING and RANGE_BOUND trade; CHOPPY / DEAD do not. Missing label -> not ok."""
    label = str(context.get("market_condition") or "").upper()
    return bool(label) and label not in ASIA_EXCLUDED_LABELS


def lane_candidates(lane: str, context: dict, shadow_candidates: list[dict], day: str, seen: set[str]) -> list[dict]:
    """This bar's picks for one sub-lane. ``seen`` is MUTATED for every valid geometry."""
    side = ema_side(context) if lane == LANE_ASIA else None
    asia_ok = asia_label_ok(context) if lane == LANE_ASIA else True
    picks: list[dict] = []
    for candidate in shadow_candidates or []:
        if str(candidate.get("strategy")) != STRATEGY or not valid_geometry(candidate):
            continue
        key = candidate_key(day, lane, candidate)
        if key in seen:
            continue
        seen.add(key)
        direction = str(candidate.get("direction")).upper()
        if lane == LANE_ASIA and (not asia_ok or side is None or direction != side):
            continue
        picks.append({
            "candidate_key": key,
            "lane": lane,
            "strategy": STRATEGY,
            "direction": direction,
            "entry": float(candidate["entry"]),
            "stop": float(candidate["stop"]),
            "target": reanchor_target(candidate, TARGET_R[lane]),
            "target_r": TARGET_R[lane],
            "original_target": float(candidate["target"]),
        })
    return picks


# ─────────────────────────────── runtime hook ───────────────────────────────


def _event_id(event: str, ts: str, candidate_key: Optional[str]) -> str:
    return f"{CAMPAIGN_ID}|{event}|{ts}|{candidate_key or '-'}"


def _base_event(event: str, ts: str, day: str, lane: Optional[str], context: dict) -> dict:
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
        "sunday_window": in_sunday_window(ts),
        "ema_direction": (context.get("trend") or {}).get("direction"),
        "market_condition": context.get("market_condition"),
        "structural_market_condition": context.get("structural_market_condition"),
        "broker_route": "PaperBroker",
        "observation_only": True,
        "normal_execution_affected": False,
    }


def _candidate_fields(candidate: dict) -> dict:
    return {k: candidate[k] for k in ("candidate_key", "lane", "strategy", "direction", "entry", "stop", "target", "target_r", "original_target")}


def _position_fields(position: dict) -> dict:
    return {
        **_candidate_fields(position),
        "actual_entry": position["actual_entry"],
        "paper_order_id": position["paper_order_id"],
        "entry_ts": position["entry_ts"],
        "entry_et_hour": position.get("et_hour"),
    }


def process_bar(
    *,
    state,
    cfg,
    log_dir,
    shadow_candidates: Optional[list[dict]],
    for_date: Optional[date] = None,
) -> Optional[dict[str, Any]]:
    """Advance both sub-lanes by one authoritative 15m bar. Returns a summary or None.

    ``None`` means the lane did nothing and touched nothing: OFF (default),
    no valid epoch, wrong instrument, or wrong timeframe. Open positions in
    either sub-lane resolve on every later same-day bar regardless of session.
    """
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
    context = _bar_context(state)
    bar = {"ts": ts, "open": float(ohlc.open), "high": float(ohlc.high), "low": float(ohlc.low), "close": float(ohlc.close)}

    try:
        lane_state = load_state(log_dir)
    except LaneStateError as exc:
        logger.warning("session_22c lane: state invalid, failing closed: %s", exc)
        return {"campaign_id": CAMPAIGN_ID, "lane_result": "STATE_INVALID", "detail": str(exc), "events": []}
    try:
        recovered = recover_pending(log_dir, lane_state)
    except OSError as exc:
        logger.warning("session_22c lane: pending evidence replay failed, failing closed: %s", exc)
        return {"campaign_id": CAMPAIGN_ID, "lane_result": "STATE_INVALID", "detail": f"pending replay failed: {exc}", "events": []}

    events: list[dict] = []
    positions = lane_state["positions"]

    # 1. Advance / expire each sub-lane's open position on strictly-later bars.
    for lane in LANES:
        position = positions.get(lane)
        if position is None or ts <= str(position["entry_ts"]):
            continue
        if str(position["day"]) != day:
            outcome = _resolve_forward(position, [], roll_bar_ts=ts)
            events.append({**_base_event("OUTCOME", ts, day, lane, context), **_position_fields(position), **outcome})
            positions[lane] = None
            continue
        terminal = advance(position, bar)
        if terminal is not None:
            actual_risk = abs(float(position["actual_entry"]) - float(position["stop"]))
            outcome = _outcome_fields(position, terminal, ts, actual_risk)
            events.append({**_base_event("OUTCOME", ts, day, lane, context), **_position_fields(position), **outcome})
            positions[lane] = None
        else:
            positions[lane] = position

    # 2. This bar's candidates for the sub-lane the bar belongs to (if any).
    lane = lane_for_bar(ts, context.get("session"))
    if lane is not None:
        epoch_dt = epoch(cfg)
        bar_dt = state.timestamp if state.timestamp.tzinfo else state.timestamp.replace(tzinfo=timezone.utc)
        seen = set(lane_state.get("seen") or [])
        picks = lane_candidates(lane, context, shadow_candidates or [], day, seen)
        lane_state["seen"] = (list(lane_state.get("seen") or []) + sorted(seen - set(lane_state.get("seen") or [])))[-MAX_SEEN_KEYS:]
        for candidate in sorted(picks, key=lambda c: c["direction"]):
            if epoch_dt is not None and bar_dt < epoch_dt:
                events.append({**_base_event("CANDIDATE_PRE_EPOCH", ts, day, lane, context), **_candidate_fields(candidate)})
                continue
            if positions.get(lane) is not None:
                events.append({**_base_event("CANDIDATE_SKIPPED_BUSY", ts, day, lane, context), **_candidate_fields(candidate),
                               "open_candidate_key": positions[lane]["candidate_key"]})
                continue
            fill = ioc_open(candidate, bar["close"])
            if fill.result != "OPEN":
                events.append({**_base_event("NO_FILL", ts, day, lane, context), **_candidate_fields(candidate),
                               "result": "NO_FILL" if fill.result == "CANCELLED" else fill.result,
                               "exit_reason": fill.exit_reason or fill.no_fill_reason or "ENTRY_NOT_FILLED",
                               "decision_close": bar["close"], "entry_price": fill.entry_price})
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
            events.append({**_base_event("CANDIDATE_FILLED", ts, day, lane, context), **_position_fields(position),
                           "decision_close": bar["close"]})

    for event in events:
        event["event_id"] = _event_id(event["event"], ts, event.get("candidate_key"))
    commit(log_dir, lane_state, events)
    return {
        "campaign_id": CAMPAIGN_ID,
        "lane_result": "ADVANCED",
        "bar_lane": lane,
        "positions_open": {k: positions.get(k) is not None for k in LANES},
        "recovered_pending_events": recovered,
        "events": [{"event": e["event"], "lane": e.get("lane"), "candidate_key": e.get("candidate_key")} for e in events],
    }
