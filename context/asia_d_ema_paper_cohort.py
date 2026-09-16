"""Asia-session D+EMA forward paper cohort (MNQ, 15m) — isolated sibling lane.

Approved 2026-09-16 as a PAPER-ONLY forward test after the Asia-only audit of
the archived D+EMA counterfactual (v3 historical + v4 September). It exists so
the collapsed one-position Asia stream can be observed PROSPECTIVELY under the
exact archived definition, with its own campaign id, epoch, evidence file and
state file. It never touches the real book, the MES 1-2-2 lane, the MNQ Strat
evidence lanes, the forward A/B campaign or the cross-instrument campaign.

Default is OFF. ``ASIA_D_EMA_PAPER_MODE=off`` (the default, and any value that
is not exactly ``paper_sim``) means this module performs NO file I/O and NO
evaluation at all: ``process_bar`` returns ``None`` before touching the disk.
Activation requires BOTH ``ASIA_D_EMA_PAPER_MODE=paper_sim`` AND a valid
offset-aware ``ASIA_D_EMA_PAPER_EPOCH_START``. Anything else fails closed.

Definition (reproduces the archived producer ``counterfactual_representation_v3.py``,
lines 341 / 368 / 479-486, exactly — see tests/test_asia_d_ema_paper_cohort.py
for the parity proof against archived candidate rows):

* candidate source   = the runner's ``shadow_candidates`` for the decision bar
                       (``strategy/shadow_setups.evaluate_shadow_setups``), with
                       structurally valid geometry (stop < entry < target for
                       LONG, target < entry < stop for SHORT);
* persistent-geometry dedupe: within one observation day, an identical
  (strategy, direction, entry, stop, target) is ONE setup;
* cohort D           = payload ``market_condition != "TRENDING"`` AND
                       ``structural_market_condition`` not in
                       {STRUCTURAL_TREND_UP, STRUCTURAL_TREND_DOWN};
* EMA alignment      = candidate direction == payload ``trend.direction``
                       mapped UP->LONG / DOWN->SHORT (anything else -> no match);
* scope (this cohort, narrower than the producer's population): MNQ only,
  ``session == "asian"`` only, strategies ``ema_pullback_trend`` and
  ``strat_22_continuation_observed`` only, 15-minute decision bars only;
* fill               = canonical PaperBroker ``ioc_limit`` evaluated ONCE at the
                       decision bar close, 1 adverse tick, 32-tick tolerance,
                       pessimistic both-hit, original 1.0R bracket, no
                       breakeven, no runner;
* resolution         = strictly later 15m bars of the SAME observation day
                       (CME 18:00 ET boundary via ``observation_day``); an open
                       position at the day roll is EXPIRED, never carried;
* ONE open cohort position at a time. A candidate arriving while a position is
  open is journaled as ``CANDIDATE_SKIPPED_BUSY`` and never traded. Several
  candidates on the same bar are attempted in (strategy, direction) order — the
  same deterministic, non-optimized tie-break the audit used.

Deliberately NOT a filter: the ET hour of the decision bar is logged on every
row (``et_hour``) because the audit's 18-19 ET vs 20-02 ET split is a
post-hoc, pre-registered SECONDARY hypothesis. Nothing here reads it.

Paper-only by construction: the only execution object in this module is
``execution.paper_broker.PaperBroker``. There is no broker interface lookup,
no Tradovate import, no order route; the runner hook passes nothing that
could reach one.

Known, deliberate difference from the offline producer's POPULATION (not its
condition): the producer read journal rows whose real-book decision was
NO_TRADE / TRADE / RISK_REJECTED. This lane evaluates every claimed,
authoritative 15m MNQ bar the runner processes, regardless of what the real
book decides about that bar later. The D+EMA condition, the geometry dedupe,
the fill model and the resolution horizon are identical.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from execution.broker_interface import BracketOrder
from execution.cross_instrument_observation import observation_day
from execution.paper_broker import NextBarOHLC, PaperBroker

try:  # POSIX advisory lock for the append-only evidence file
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX
    fcntl = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

CAMPAIGN_ID = "asia_d_ema_2026_09_v1"
INSTRUMENT = "MNQ"
SESSION = "asian"
STRATEGIES = ("ema_pullback_trend", "strat_22_continuation_observed")
TIMEFRAME_MINUTES = 15
_TIMEFRAME_TOKENS = {"15", "15m"}

MODE_ENV = "ASIA_D_EMA_PAPER_MODE"
EPOCH_ENV = "ASIA_D_EMA_PAPER_EPOCH_START"
MODE_OFF = "off"
MODE_PAPER = "paper_sim"
DEFAULT_MODE = MODE_OFF
VALID_MODES = (MODE_OFF, MODE_PAPER)

# Canonical counterfactual fill model (archived producer constants).
TICK = 0.25
TICK_VALUE = 0.50
SLIP_TICKS = 1.0
IOC_TOLERANCE_TICKS = 32.0
CONTRACTS = 1

STRUCTURAL_TREND = {"STRUCTURAL_TREND_UP", "STRUCTURAL_TREND_DOWN"}
_EMA_TO_SIDE = {"UP": "LONG", "DOWN": "SHORT"}
TERMINAL = {"WIN", "LOSS", "BREAKEVEN"}
MAX_SEEN_KEYS = 2000
STATE_VERSION = 1
_ET = ZoneInfo("America/New_York")

_POSITION_REQUIRED = (
    "candidate_key", "strategy", "direction", "entry", "stop", "target",
    "actual_entry", "entry_ts", "day", "paper_order_id",
)


# ─────────────────────────────── configuration ──────────────────────────────


def mode(cfg=None) -> str:
    """``paper_sim`` only when that exact token is configured; everything else is OFF."""
    raw = getattr(cfg, "asia_d_ema_paper_mode", None) if cfg is not None else None
    if raw is None:
        raw = os.getenv(MODE_ENV, DEFAULT_MODE)
    value = str(raw or DEFAULT_MODE).strip().lower()
    return MODE_PAPER if value == MODE_PAPER else MODE_OFF


def epoch_start(cfg=None) -> Optional[str]:
    raw = getattr(cfg, "asia_d_ema_paper_epoch_start", None) if cfg is not None else None
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


def cohort_dir(log_dir) -> Path:
    return Path(log_dir) / "asia_d_ema_cohort"


def evidence_path(log_dir) -> Path:
    return cohort_dir(log_dir) / "evidence.jsonl"


def state_path(log_dir) -> Path:
    return cohort_dir(log_dir) / "state.json"


class CohortStateError(RuntimeError):
    """The persisted cohort state is unreadable or malformed — fail closed."""


def _empty_state() -> dict[str, Any]:
    return {"version": STATE_VERSION, "campaign_id": CAMPAIGN_ID, "seen": [], "position": None}


def load_state(log_dir) -> dict[str, Any]:
    """A MISSING state file is a fresh cohort. Anything unreadable or malformed raises."""
    path = state_path(log_dir)
    if not path.exists():
        return _empty_state()
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise CohortStateError(f"cohort state unreadable: {exc}") from exc
    if not isinstance(raw, dict):
        raise CohortStateError("cohort state is not an object")
    if raw.get("campaign_id") != CAMPAIGN_ID or raw.get("version") != STATE_VERSION:
        raise CohortStateError("cohort state campaign/version mismatch")
    seen = raw.get("seen")
    position = raw.get("position")
    if not isinstance(seen, list):
        raise CohortStateError("cohort state 'seen' is not a list")
    if position is not None:
        if not isinstance(position, dict) or any(
            position.get(k) is None for k in _POSITION_REQUIRED
        ):
            raise CohortStateError("cohort state position is incomplete")
    return {
        "version": STATE_VERSION,
        "campaign_id": CAMPAIGN_ID,
        "seen": [str(k) for k in seen][-MAX_SEEN_KEYS:],
        "position": position,
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
        finally:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


# ─────────────────────────────── definition ─────────────────────────────────


def instrument_root(instrument: Optional[str]) -> str:
    return str(instrument or "").upper().replace("1!", "")


def is_decision_timeframe(timeframe: Any) -> bool:
    return str(timeframe).strip().lower() in _TIMEFRAME_TOKENS


def valid_geometry(candidate: dict) -> bool:
    """Archived producer ``valid_candidate``."""
    try:
        entry = float(candidate["entry"])
        stop = float(candidate["stop"])
        target = float(candidate["target"])
    except (KeyError, TypeError, ValueError):
        return False
    direction = str(candidate.get("direction") or "").upper()
    if direction == "LONG":
        return stop < entry < target
    if direction == "SHORT":
        return target < entry < stop
    return False


def is_cohort_d(context: dict) -> bool:
    """Archived producer line 341: neither Pine TRENDING nor a structural trend."""
    pine = context.get("market_condition")
    structural = context.get("structural_market_condition") or ""
    return pine != "TRENDING" and structural not in STRUCTURAL_TREND


def ema_side(context: dict) -> Optional[str]:
    """Archived producer line 368/485: payload ``trend.direction`` UP->LONG, DOWN->SHORT."""
    trend = context.get("trend") or {}
    return _EMA_TO_SIDE.get(str(trend.get("direction") or "").upper())


def candidate_key(day: str, candidate: dict) -> str:
    """Archived producer dedupe key: identical geometry within one observation day is one setup."""
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


def d_ema_candidates(context: dict, shadow_candidates: list[dict], day: str, seen: set[str]) -> list[dict]:
    """The EXACT archived D+EMA pick for one decision bar (all sessions, all strategies).

    ``seen`` is the observation-day dedupe set and is MUTATED for every valid
    geometry, exactly as the producer's ``seen_cands`` was: a persistent
    identical setup is consumed the first time it appears whether or not the
    bar it appears on is cohort D / EMA-aligned.
    """
    side = ema_side(context)
    cohort_d = is_cohort_d(context)
    picks: list[dict] = []
    for candidate in shadow_candidates or []:
        if not valid_geometry(candidate):
            continue
        key = candidate_key(day, candidate)
        if key in seen:
            continue
        seen.add(key)
        if not cohort_d:
            continue
        if side is None or str(candidate.get("direction")).upper() != side:
            continue
        picks.append({
            "candidate_key": key,
            "strategy": str(candidate.get("strategy")),
            "direction": str(candidate.get("direction")).upper(),
            "entry": float(candidate["entry"]),
            "stop": float(candidate["stop"]),
            "target": float(candidate["target"]),
        })
    return picks


def in_scope(instrument: Optional[str], session: Optional[str], strategy: Optional[str], timeframe: Any) -> bool:
    """This cohort's allowlists on top of the producer condition. All four must hold."""
    return (
        instrument_root(instrument) == INSTRUMENT
        and str(session or "") == SESSION
        and str(strategy or "") in STRATEGIES
        and is_decision_timeframe(timeframe)
    )


# ─────────────────────────────── fill model ─────────────────────────────────


def _broker() -> PaperBroker:
    return PaperBroker(
        starting_balance=100_000.0,
        slippage_ticks=SLIP_TICKS,
        pessimistic_both_hit=True,
        breakeven_at_1r=False,
        runner_mode=False,
        entry_fill_model="ioc_limit",
        entry_tolerance_ticks_by_root={INSTRUMENT: IOC_TOLERANCE_TICKS},
    )


def _order(candidate: dict) -> BracketOrder:
    risk = abs(candidate["entry"] - candidate["stop"])
    rr = abs(candidate["target"] - candidate["entry"]) / risk if risk > 0 else 0.0
    return BracketOrder(
        instrument=INSTRUMENT,
        direction=candidate["direction"],
        entry=candidate["entry"],
        stop=candidate["stop"],
        target=candidate["target"],
        rr_ratio=rr,
        strategy=candidate["strategy"],
        contracts=CONTRACTS,
        post_fill_validation_required=False,
    )


def ioc_open(candidate: dict, decision_close: float):
    """Canonical IOC at the decision-bar close. Returns the PaperBroker Fill."""
    return _broker().execute_bracket(_order(candidate), market_price=float(decision_close))


def advance(position: dict, bar: dict):
    """Resolve an open position against one strictly-later same-day bar.

    Rebuilds the broker from the persisted position (the broker is stateless
    between webhook calls) with the same slippage / pessimistic settings the
    producer's single long-lived broker had; with no breakeven and no runner the
    two are equivalent. Mutates the position's MAE/MFE/bars_seen. Returns the
    terminal Fill or None.
    """
    broker = _broker()
    broker.restore_position(
        INSTRUMENT,
        position["direction"],
        float(position["actual_entry"]),
        float(position["stop"]),
        float(position["target"]),
        CONTRACTS,
        paper_order_id=position.get("paper_order_id"),
    )
    entry = float(position["actual_entry"])
    high = float(bar["high"])
    low = float(bar["low"])
    if position["direction"] == "LONG":
        adverse, favorable = entry - low, high - entry
    else:
        adverse, favorable = high - entry, entry - low
    position["mae_points"] = max(float(position.get("mae_points") or 0.0), adverse)
    position["mfe_points"] = max(float(position.get("mfe_points") or 0.0), favorable)
    position["bars_seen"] = int(position.get("bars_seen") or 0) + 1
    return broker.resolve_position(
        NextBarOHLC(open=float(bar["open"]), high=high, low=low)
    )


def resolve_offline(candidate: dict, decision_bar: dict, forward_bars: list[dict]) -> dict:
    """Producer-shaped outcome for one candidate in isolation (used by the parity test).

    ``forward_bars`` must be the strictly-later bars of the same observation
    day, in order. This calls the SAME ``ioc_open`` / ``advance`` the runtime
    hook uses; it is not a second implementation.
    """
    first = ioc_open(candidate, decision_bar["close"])
    if first.result == "CANCELLED":
        return {"result": "NO_FILL", "exit_reason": first.exit_reason or first.no_fill_reason or "ENTRY_NOT_FILLED",
                "bars_seen": 0, "entry_price": first.entry_price, "pnl_r": None, "pnl_dollars": None}
    if first.result != "OPEN":
        return {"result": first.result, "exit_reason": first.exit_reason, "bars_seen": 0,
                "entry_price": first.entry_price, "pnl_r": None, "pnl_dollars": first.pnl_dollars}
    position = {
        **candidate,
        "actual_entry": float(first.entry_price),
        "paper_order_id": first.paper_order_id,
        "entry_ts": decision_bar["ts"],
        "day": "offline",
    }
    return _resolve_forward(position, forward_bars)


def _resolve_forward(position: dict, forward_bars: list[dict]) -> dict:
    actual_entry = float(position["actual_entry"])
    actual_risk = abs(actual_entry - float(position["stop"]))
    for bar in forward_bars:
        terminal = advance(position, bar)
        if terminal is None:
            continue
        return _outcome_fields(position, terminal, bar["ts"], actual_risk)
    return {
        "result": "EXPIRED",
        "exit_reason": "OBSERVATION_DATE_ROLLED",
        "exit_ts": forward_bars[-1]["ts"] if forward_bars else position["entry_ts"],
        "bars_seen": int(position.get("bars_seen") or 0),
        "entry_price": actual_entry,
        "pnl_r": None,
        "pnl_dollars": None,
        "mae_r": float(position.get("mae_points") or 0.0) / actual_risk if actual_risk > 0 else None,
        "mfe_r": float(position.get("mfe_points") or 0.0) / actual_risk if actual_risk > 0 else None,
    }


def _outcome_fields(position: dict, terminal, exit_ts: str, actual_risk: float) -> dict:
    pnl_points = float(terminal.pnl_ticks or 0.0) * TICK
    return {
        "result": terminal.result,
        "exit_reason": terminal.exit_reason,
        "exit_ts": exit_ts,
        "bars_seen": int(position.get("bars_seen") or 0),
        "entry_price": float(position["actual_entry"]),
        "exit_price": terminal.exit_price,
        "pnl_r": pnl_points / actual_risk if actual_risk > 0 else None,
        "pnl_dollars": terminal.pnl_dollars,
        "mae_r": float(position.get("mae_points") or 0.0) / actual_risk if actual_risk > 0 else None,
        "mfe_r": float(position.get("mfe_points") or 0.0) / actual_risk if actual_risk > 0 else None,
    }


# ─────────────────────────────── runtime hook ───────────────────────────────


def _et_hour(ts: str) -> Optional[int]:
    try:
        parsed = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(_ET).hour


def _bar_context(state) -> dict:
    trend = getattr(state, "trend", None)
    structural = getattr(state, "structural_regime", None) or {}
    return {
        "session": getattr(state, "session", None),
        "market_condition": getattr(state, "market_condition", None),
        "structural_market_condition": structural.get("structural_market_condition") if isinstance(structural, dict) else None,
        "structural_direction": structural.get("structural_direction") if isinstance(structural, dict) else None,
        "trend": {
            "direction": getattr(trend, "direction", None) if trend is not None else None,
            "strength": getattr(trend, "strength", None) if trend is not None else None,
        },
    }


def _base_event(event: str, ts: str, day: str, context: dict) -> dict:
    return {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "campaign_id": CAMPAIGN_ID,
        "event": event,
        "instrument": INSTRUMENT,
        "timeframe": TIMEFRAME_MINUTES,
        "session": context.get("session"),
        "ts": ts,
        "day": day,
        "et_hour": _et_hour(ts),
        "cohort_d": is_cohort_d(context),
        "ema_direction": (context.get("trend") or {}).get("direction"),
        "market_condition": context.get("market_condition"),
        "structural_market_condition": context.get("structural_market_condition"),
        "broker_route": "PaperBroker",
        "observation_only": True,
        "normal_execution_affected": False,
    }


def process_bar(
    *,
    state,
    cfg,
    log_dir,
    shadow_candidates: Optional[list[dict]],
    for_date: Optional[date] = None,
) -> Optional[dict[str, Any]]:
    """Advance the cohort by one authoritative 15m bar. Returns a summary or None.

    ``None`` means the lane did nothing and touched nothing: OFF (default),
    no valid epoch, wrong instrument, or wrong timeframe. Session and strategy
    are checked per candidate so an open position can still resolve on
    non-Asian bars of the same observation day.
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
        cohort_state = load_state(log_dir)
    except CohortStateError as exc:
        logger.warning("asia_d_ema cohort: state invalid, failing closed: %s", exc)
        return {"campaign_id": CAMPAIGN_ID, "cohort_result": "STATE_INVALID", "detail": str(exc), "events": []}

    events: list[dict] = []
    position = cohort_state.get("position")

    # 1. Advance / expire the open position on strictly-later bars.
    if position is not None and ts > str(position["entry_ts"]):
        if str(position["day"]) != day:
            outcome = _resolve_forward(position, [])
            events.append({**_base_event("OUTCOME", ts, day, context), **_position_fields(position), **outcome})
            cohort_state["position"] = position = None
        else:
            terminal = advance(position, bar)
            if terminal is not None:
                actual_risk = abs(float(position["actual_entry"]) - float(position["stop"]))
                outcome = _outcome_fields(position, terminal, ts, actual_risk)
                events.append({**_base_event("OUTCOME", ts, day, context), **_position_fields(position), **outcome})
                cohort_state["position"] = position = None
            else:
                cohort_state["position"] = position

    # 2. This bar's candidates (exact producer pick, then this cohort's scope).
    epoch_dt = epoch(cfg)
    bar_dt = state.timestamp if state.timestamp.tzinfo else state.timestamp.replace(tzinfo=timezone.utc)
    seen = set(cohort_state.get("seen") or [])
    picks = d_ema_candidates(context, shadow_candidates or [], day, seen)
    cohort_state["seen"] = (list(cohort_state.get("seen") or []) + sorted(seen - set(cohort_state.get("seen") or [])))[-MAX_SEEN_KEYS:]
    for candidate in sorted(picks, key=lambda c: (c["strategy"], c["direction"])):
        if not in_scope(instrument, context.get("session"), candidate["strategy"], ohlc.timeframe):
            continue
        if epoch_dt is not None and bar_dt < epoch_dt:
            events.append({**_base_event("CANDIDATE_PRE_EPOCH", ts, day, context), **_candidate_fields(candidate)})
            continue
        if position is not None:
            events.append({**_base_event("CANDIDATE_SKIPPED_BUSY", ts, day, context), **_candidate_fields(candidate),
                           "open_candidate_key": position["candidate_key"]})
            continue
        fill = ioc_open(candidate, bar["close"])
        if fill.result != "OPEN":
            events.append({**_base_event("NO_FILL", ts, day, context), **_candidate_fields(candidate),
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
        cohort_state["position"] = position
        events.append({**_base_event("CANDIDATE_FILLED", ts, day, context), **_position_fields(position),
                       "decision_close": bar["close"]})

    save_state(log_dir, cohort_state)
    for event in events:
        append_event(log_dir, event)
    return {
        "campaign_id": CAMPAIGN_ID,
        "cohort_result": "ADVANCED",
        "position_open": cohort_state.get("position") is not None,
        "events": [{"event": e["event"], "candidate_key": e.get("candidate_key")} for e in events],
    }


def _candidate_fields(candidate: dict) -> dict:
    return {k: candidate[k] for k in ("candidate_key", "strategy", "direction", "entry", "stop", "target")}


def _position_fields(position: dict) -> dict:
    return {
        **_candidate_fields(position),
        "actual_entry": position["actual_entry"],
        "paper_order_id": position["paper_order_id"],
        "entry_ts": position["entry_ts"],
        "entry_et_hour": position.get("et_hour"),
    }
