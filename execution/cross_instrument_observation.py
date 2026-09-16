"""cross_instrument_observation_v1 — observation-only evidence across six micros.

This campaign records what setups OCCUR on MNQ, MES, M2K, MGC, MCL and MBT and,
for structural populations, how the bar-derived bracket would have resolved.
It grants nothing: no ingestion, strategy, risk, broker or execution
eligibility is inferred from a row in this file. It is a SEPARATE campaign from
the frozen MNQ ``forward_ab_2026_08_v1`` file, which it never reads or writes.

Identity: ``campaign × strategy × instrument × variant × evidence_epoch``.
Every configured population is enumerated by the report even at zero count.

Activation is an explicit operator act: the campaign is enabled only when
``CROSS_INSTRUMENT_OBSERVATION`` equals the campaign id AND
``CROSS_INSTRUMENT_OBSERVATION_EPOCH`` is non-empty. Both default to unset.

Hard boundary: this module imports no DecisionEngine, RiskEngine, PaperBroker
or Tradovate code, and ``tests/test_cross_instrument_observation_transport.py``
proves that transitively.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import threading
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional
from zoneinfo import ZoneInfo

from config.futures_contracts import contract_economics, contract_root

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None

CAMPAIGN_ID = "cross_instrument_observation_v1"
SCHEMA_VERSION = "1.0.0"
ENV_NAME = "CROSS_INSTRUMENT_OBSERVATION"
EPOCH_ENV_NAME = "CROSS_INSTRUMENT_OBSERVATION_EPOCH"
EVIDENCE_FILENAME = f"{CAMPAIGN_ID}.jsonl"
STATE_FILENAME = f"{CAMPAIGN_ID}_state.json"
CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "cross_instrument_observation.json"

OBSERVATION_UNIVERSE: tuple[str, ...] = ("MNQ", "MES", "M2K", "MGC", "MCL", "MBT")
COLLECTION_ONLY_ROOTS: tuple[str, ...] = ("M2K", "MGC", "MCL", "MBT")
STRUCTURAL_OUTCOME = "structural_outcome"
SIGNAL_METRICS = "signal_metrics"
TERMINAL_RESULTS = ("WIN", "LOSS")
DECISION_OBSERVATION_ONLY = "OBSERVATION_ONLY"
DECISION_TIMEFRAME_MINUTES = 15
_ET = ZoneInfo("America/New_York")

PopulationKey = tuple[str, str, str, str]  # strategy, instrument, variant, evidence_epoch


class ObservationError(ValueError):
    """Campaign identity / configuration violation."""


# ── activation ────────────────────────────────────────────────────────────────

def evidence_epoch() -> Optional[str]:
    value = os.getenv(EPOCH_ENV_NAME, "").strip()
    return value or None


def campaign_enabled() -> bool:
    """Enabled only with BOTH the campaign switch and an explicit epoch."""
    return os.getenv(ENV_NAME, "").strip() == CAMPAIGN_ID and evidence_epoch() is not None


def is_collection_only(symbol: object) -> bool:
    """True for roots that may ONLY ever be observed (never executed)."""
    return contract_root(symbol) in COLLECTION_ONLY_ROOTS


def is_observation_instrument(symbol: object) -> bool:
    return contract_root(symbol) in OBSERVATION_UNIVERSE


def normalize_timeframe_minutes(value: object) -> Optional[int]:
    """Normalize a timeframe token without importing the trading runner."""
    if value is None:
        return None
    s = str(value).strip().lower()
    if not s:
        return None
    if s.isdigit():
        return int(s)
    for suffix, mult in (("min", 1), ("m", 1), ("hr", 60), ("h", 60)):
        if s.endswith(suffix):
            head = s[: -len(suffix)].strip()
            if head.isdigit():
                return int(head) * mult
    return None


def _parse_timestamp(value: object) -> Optional[datetime]:
    if isinstance(value, datetime):
        dt = value
    else:
        raw = str(value or "").strip()
        if not raw:
            return None
        try:
            if raw.replace(".", "", 1).isdigit():
                number = float(raw)
                if number > 1e12:
                    number /= 1000.0
                dt = datetime.fromtimestamp(number, tz=timezone.utc)
            else:
                dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except (TypeError, ValueError, OSError, OverflowError):
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def observation_day(instrument: str, timestamp: object, *, for_date: Optional[date] = None) -> date:
    """Stable evidence day that does not roll at UTC midnight.

    Globex equity/metals/energy products use the CME-style 18:00 ET trading-day
    boundary: bars at/after the reopen belong to the following trading date.
    MBT is 24/7 in 2026, so its observation horizon uses the ET calendar date.

    ``for_date`` is accepted for call compatibility only. Evidence identity is
    derived from the bar timestamp itself so a caller's UTC ``date.today()``
    cannot split a live overnight sequence at midnight UTC.
    """
    _ = for_date
    dt = _parse_timestamp(timestamp)
    if dt is None:
        raise ObservationError(f"invalid observation timestamp {timestamp!r}")
    et = dt.astimezone(_ET)
    if instrument == "MBT":
        return et.date()
    if et.time() >= time(18, 0):
        return et.date() + timedelta(days=1)
    return et.date()


# ── configuration ─────────────────────────────────────────────────────────────

def load_config() -> dict:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    if config.get("campaign_id") != CAMPAIGN_ID:
        raise ObservationError("wrong campaign config identity")
    if config.get("evidence_schema_version") != SCHEMA_VERSION:
        raise ObservationError("wrong campaign schema version")
    if tuple(config.get("instruments") or ()) != OBSERVATION_UNIVERSE:
        raise ObservationError("campaign instruments must equal the observation universe")
    if tuple(config.get("collection_only_instruments") or ()) != COLLECTION_ONLY_ROOTS:
        raise ObservationError("collection-only roots drifted from the module constant")
    if int(config.get("decision_timeframe", "15m").lower().rstrip("m")) != DECISION_TIMEFRAME_MINUTES:
        raise ObservationError("campaign decision timeframe must remain 15m")
    return config


def configured_populations(epoch: Optional[str] = None) -> tuple[dict, ...]:
    """Every configured population as a dict with its full identity."""
    config = load_config()
    epoch = epoch if epoch is not None else evidence_epoch()
    modes = set(config.get("collection_modes") or {})
    out: list[dict] = []
    seen: set[tuple] = set()
    for row in config["populations"]:
        strategy = str(row["strategy"])
        variant = str(row.get("variant") or "observer")
        mode = str(row["collection_mode"])
        if mode not in modes:
            raise ObservationError(f"unknown collection_mode {mode!r} for {strategy}")
        for instrument in row["instruments"]:
            if instrument not in OBSERVATION_UNIVERSE:
                raise ObservationError(f"{instrument!r} is outside the observation universe")
            contract_economics(instrument)
            key = (strategy, instrument, variant)
            if key in seen:
                raise ObservationError(f"duplicate population {key}")
            seen.add(key)
            out.append({
                "campaign_id": CAMPAIGN_ID,
                "strategy": strategy,
                "instrument": instrument,
                "variant": variant,
                "evidence_epoch": epoch,
                "collection_mode": mode,
                "collection_only": instrument in COLLECTION_ONLY_ROOTS,
            })
    return tuple(out)


def population_for(strategy: str, instrument: str, variant: str = "observer") -> Optional[dict]:
    for pop in configured_populations():
        if (pop["strategy"], pop["instrument"], pop["variant"]) == (strategy, instrument, variant):
            return pop
    return None


def population_key(record: dict) -> PopulationKey:
    if record.get("campaign_id") != CAMPAIGN_ID or record.get("evidence_schema_version") != SCHEMA_VERSION:
        raise ObservationError("wrong campaign/schema identity")
    fields = (record.get("strategy"), record.get("instrument"), record.get("variant"), record.get("evidence_epoch"))
    if not all(isinstance(v, str) and v.strip() and v == v.strip() for v in fields):
        raise ObservationError("explicit strategy/instrument/variant/evidence_epoch required")
    if fields[1] not in OBSERVATION_UNIVERSE:
        raise ObservationError(f"instrument {fields[1]!r} outside the observation universe")
    return fields  # type: ignore[return-value]


# ── storage ───────────────────────────────────────────────────────────────────

_STATE_LOCK = threading.RLock()


@contextmanager
def _state_lock(log_dir: str | Path):
    path = Path(log_dir) / (STATE_FILENAME + ".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with _STATE_LOCK, path.open("a") as handle:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _state_path(log_dir: str | Path) -> Path:
    return Path(log_dir) / STATE_FILENAME


def _empty_state() -> dict:
    return {"campaign_id": CAMPAIGN_ID, "pending": {}, "seen_candidate_ids": [], "seen_bars": [], "strat_212_122": {}}


def _load_state(log_dir: str | Path) -> dict:
    try:
        raw = json.loads(_state_path(log_dir).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _empty_state()
    if not isinstance(raw, dict) or raw.get("campaign_id") != CAMPAIGN_ID:
        raise ObservationError("invalid observation state; refusing reset")
    for key in ("pending", "strat_212_122"):
        if not isinstance(raw.get(key), dict):
            raise ObservationError("invalid observation state; refusing reset")
    for key in ("seen_candidate_ids", "seen_bars"):
        if not isinstance(raw.get(key), list):
            raise ObservationError("invalid observation state; refusing reset")
    return raw


def _save_state(log_dir: str | Path, state: dict) -> None:
    path = _state_path(log_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True, default=str), encoding="utf-8")
    tmp.replace(path)


def _evidence_identity(record: dict) -> Optional[tuple[str, str]]:
    record_type = str(record.get("record_type") or "")
    candidate = record.get("candidate_id")
    if record_type in ("CANDIDATE", "SIGNAL", "OUTCOME") and isinstance(candidate, str) and candidate:
        return record_type, candidate
    return None


def _append_evidence(log_dir: str | Path, record: dict) -> bool:
    """Append one row exactly once by (record_type, candidate_id)."""
    path = Path(log_dir) / EVIDENCE_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    full = {"observed_at": datetime.now(timezone.utc).isoformat(), **record}
    identity = _evidence_identity(record)
    with path.open("a+", encoding="utf-8") as handle:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            if identity is not None:
                handle.seek(0)
                for line in handle:
                    try:
                        existing = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(existing, dict) and _evidence_identity(existing) == identity:
                        return False
            handle.seek(0, os.SEEK_END)
            handle.write(json.dumps(full, separators=(",", ":"), default=str) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
            return True
        finally:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def read_evidence(log_dir: str | Path) -> list[dict]:
    path = Path(log_dir) / EVIDENCE_FILENAME
    if not path.exists():
        return []
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("campaign_id") == CAMPAIGN_ID:
            rows.append(row)
    return rows


def generating_sha() -> tuple[Optional[str], str]:
    for name in ("AFS_RELEASE_SHA", "RELEASE_SHA", "GIT_SHA"):
        value = os.getenv(name, "").strip()
        if value:
            return value, f"environment:{name}"
    return None, "unknown"


# ── identity ──────────────────────────────────────────────────────────────────

def candidate_id(pop: dict, *, bar_ts: str, direction: str, entry: float) -> str:
    raw = json.dumps([CAMPAIGN_ID, pop["strategy"], pop["instrument"], pop["variant"],
                      pop["evidence_epoch"], bar_ts, direction, float(entry)], separators=(",", ":"))
    return "cio-cand-" + hashlib.sha256(raw.encode()).hexdigest()[:24]


def _bar_key(instrument: str, timeframe: str, bar_ts: str, epoch: str) -> str:
    return f"{epoch}|{instrument}|{timeframe}|{bar_ts}"


# ── observation (one bar) ─────────────────────────────────────────────────────

def _finite(value: object) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(float(value))


def _strat_212_122_candidate(
    state_obj,
    campaign_state: dict,
    instrument: str,
    trading_date: str,
    epoch: str,
) -> Optional[dict]:
    from strategy.strat_212_122 import advance_strat_212_122

    strat = getattr(state_obj, "strat", None)
    ohlc = state_obj.ohlc
    tick, _ = contract_economics(instrument)
    state_key = f"{epoch}|{instrument}"
    persisted = campaign_state["strat_212_122"].get(state_key, {})
    next_state, candidate = advance_strat_212_122(
        current_bar_type=getattr(strat, "current_bar_type", None) if strat else None,
        previous_bar_type=getattr(strat, "previous_bar_type", None) if strat else None,
        current_open=float(ohlc.open), current_high=float(ohlc.high), current_low=float(ohlc.low),
        tick_size=tick, trading_date=trading_date, persisted_state=persisted,
    )
    campaign_state["strat_212_122"][state_key] = next_state
    if not candidate:
        return None
    pattern = str(candidate.get("pattern") or candidate.get("strategy") or "")
    if pattern not in ("strat_212", "strat_122"):
        return None
    out = {
        "strategy": pattern,
        "direction": str(candidate.get("direction") or "").upper(),
        "entry": candidate.get("entry"),
        "stop": candidate.get("stop"),
        "target": candidate.get("target"),
        "notes": "canonical 2-1-2 / 1-2-2 state machine (structural, tick-size only)",
        "filled_on_signal_bar": True,
    }
    if candidate.get("kind") == "RESOLVED":
        out["pre_resolved"] = {
            "result": candidate.get("result"),
            "exit_price": candidate.get("exit"),
            "exit_reason": candidate.get("exit_reason"),
        }
    return out


def observe_bar(
    log_dir: str | Path,
    state_obj,
    candidates: Iterable[dict],
    *,
    timeframe: str,
    for_date: Optional[date] = None,
    source: str,
    pine_advisory_ignored: Optional[dict] = None,
    include_strat_212_122: bool = True,
) -> dict:
    """Record this bar's observations for every CONFIGURED population."""
    if not campaign_enabled():
        return {"enabled": False, "written": 0}
    tf_minutes = normalize_timeframe_minutes(timeframe)
    if tf_minutes != DECISION_TIMEFRAME_MINUTES:
        return {"enabled": True, "written": 0, "skipped": "unsupported observation timeframe", "timeframe_minutes": tf_minutes}
    instrument = contract_root(getattr(state_obj, "instrument", None))
    if instrument not in OBSERVATION_UNIVERSE:
        return {"enabled": True, "written": 0, "skipped": "outside observation universe"}
    bar_ts = state_obj.timestamp.isoformat()
    trading_date = observation_day(instrument, state_obj.timestamp).isoformat()
    epoch = evidence_epoch()
    if epoch is None:
        return {"enabled": False, "written": 0}
    pops = {(p["strategy"], p["variant"]): p for p in configured_populations(epoch) if p["instrument"] == instrument}
    sha, provenance = generating_sha()
    summary = {"enabled": True, "instrument": instrument, "bar_ts": bar_ts, "written": 0,
               "structural": 0, "signal": 0, "unconfigured_dropped": [], "duplicate": False,
               "duplicate_rows_skipped": 0}

    with _state_lock(log_dir):
        campaign_state = _load_state(log_dir)
        bar_key = _bar_key(instrument, str(tf_minutes), bar_ts, epoch)
        if bar_key in campaign_state["seen_bars"]:
            summary["duplicate"] = True
            return summary
        campaign_state["seen_bars"].append(bar_key)
        campaign_state["seen_bars"] = campaign_state["seen_bars"][-5000:]

        rows = [dict(c) for c in candidates if isinstance(c, dict)]
        if include_strat_212_122:
            canonical = _strat_212_122_candidate(state_obj, campaign_state, instrument, trading_date, epoch)
            if canonical:
                rows.append(canonical)

        for cand in rows:
            strategy = str(cand.get("strategy") or "")
            pop = pops.get((strategy, "observer"))
            if pop is None:
                summary["unconfigured_dropped"].append(strategy)
                continue
            direction = str(cand.get("direction") or "").upper()
            entry, stop, target = cand.get("entry"), cand.get("stop"), cand.get("target")
            if direction not in ("LONG", "SHORT") or not all(_finite(v) for v in (entry, stop, target)):
                summary["unconfigured_dropped"].append(f"{strategy}:malformed")
                continue
            entry, stop, target = float(entry), float(stop), float(target)
            risk = abs(entry - stop)
            cid = candidate_id(pop, bar_ts=bar_ts, direction=direction, entry=entry)
            if cid in campaign_state["seen_candidate_ids"]:
                continue
            campaign_state["seen_candidate_ids"].append(cid)
            campaign_state["seen_candidate_ids"] = campaign_state["seen_candidate_ids"][-20000:]
            tick, tick_value = contract_economics(instrument)
            record = {
                "evidence_schema_version": SCHEMA_VERSION,
                "campaign_id": CAMPAIGN_ID,
                "record_type": "CANDIDATE" if pop["collection_mode"] == STRUCTURAL_OUTCOME else "SIGNAL",
                "candidate_id": cid,
                "strategy": strategy,
                "instrument": instrument,
                "variant": pop["variant"],
                "evidence_epoch": epoch,
                "collection_mode": pop["collection_mode"],
                "collection_only": pop["collection_only"],
                "direction": direction,
                "signal_timestamp": bar_ts,
                "trading_date": trading_date,
                "observation_date": trading_date,
                "source_timeframe": str(tf_minutes),
                "session": getattr(state_obj, "session", None),
                "market_condition": getattr(state_obj, "market_condition", None),
                "entry": entry, "stop": stop, "target": target,
                "risk_points": round(risk, 6),
                "risk_ticks": round(risk / tick, 4),
                "reward_to_risk": round(abs(target - entry) / risk, 4) if risk > 0 else None,
                "bracket_authoritative": pop["collection_mode"] == STRUCTURAL_OUTCOME,
                "bar": {"open": float(state_obj.ohlc.open), "high": float(state_obj.ohlc.high),
                        "low": float(state_obj.ohlc.low), "close": float(state_obj.ohlc.close)},
                "tick_size": tick, "tick_value_dollars": tick_value,
                "source": source,
                "pine_advisory_ignored": pine_advisory_ignored,
                "detector_notes": cand.get("notes"),
                "execution_reachable": False,
                "generating_git_sha": sha,
                "provenance_status": provenance,
            }
            population_key(record)
            appended = _append_evidence(log_dir, record)
            if appended:
                summary["written"] += 1
            else:
                summary["duplicate_rows_skipped"] += 1
            if pop["collection_mode"] == STRUCTURAL_OUTCOME and risk > 0:
                summary["structural"] += 1
                pending = {
                    "record": record, "filled": bool(cand.get("filled_on_signal_bar")),
                    "fill_ts": bar_ts if cand.get("filled_on_signal_bar") else None,
                    "mae_points": 0.0, "mfe_points": 0.0, "bars_seen": 0,
                }
                pre = cand.get("pre_resolved")
                if isinstance(pre, dict) and pre.get("result") in TERMINAL_RESULTS and _finite(pre.get("exit_price")):
                    outcome = _terminal(pending, str(pre["result"]), float(pre["exit_price"]), bar_ts,
                                        str(pre.get("exit_reason") or "PRE_RESOLVED_ON_SIGNAL_BAR"))
                    row = {**record, "record_type": "OUTCOME", "resolved_at_bar_ts": bar_ts, **outcome}
                    population_key(row)
                    if not _append_evidence(log_dir, row):
                        summary["duplicate_rows_skipped"] += 1
                    summary["pre_resolved"] = summary.get("pre_resolved", 0) + 1
                else:
                    campaign_state["pending"][cid] = pending
            else:
                summary["signal"] += 1
        _save_state(log_dir, campaign_state)
    return summary


# ── resolution (structural populations only) ─────────────────────────────────

def _resolve_one(pending: dict, forward: list[dict]) -> Optional[dict]:
    rec = pending["record"]
    is_long = rec["direction"] == "LONG"
    entry, stop, target = rec["entry"], rec["stop"], rec["target"]
    for bar in forward:
        try:
            high, low = float(bar["high"]), float(bar["low"])
        except (KeyError, TypeError, ValueError):
            continue
        ts = str(bar.get("ts") or "")
        pending["bars_seen"] += 1
        if not pending["filled"]:
            if low <= entry <= high:
                pending["filled"] = True
                pending["fill_ts"] = ts
                stop_hit = (low <= stop) if is_long else (high >= stop)
                if stop_hit:
                    return _terminal(pending, "LOSS", stop, ts, "STOP_HIT_ON_FILL_BAR")
                adverse = (entry - low) if is_long else (high - entry)
                pending["mae_points"] = max(pending["mae_points"], adverse)
            continue
        adverse = (entry - low) if is_long else (high - entry)
        favorable = (high - entry) if is_long else (entry - low)
        pending["mae_points"] = max(pending["mae_points"], adverse)
        pending["mfe_points"] = max(pending["mfe_points"], favorable)
        stop_hit = (low <= stop) if is_long else (high >= stop)
        target_hit = (high >= target) if is_long else (low <= target)
        if stop_hit:
            return _terminal(pending, "LOSS", stop, ts, "STOP_HIT" if not target_hit else "BOTH_HIT_PESSIMISTIC")
        if target_hit:
            return _terminal(pending, "WIN", target, ts, "TARGET_HIT")
    return None


def _terminal(pending: dict, result: str, exit_price: float, exit_ts: str, reason: str) -> dict:
    rec = pending["record"]
    is_long = rec["direction"] == "LONG"
    entry, stop = rec["entry"], rec["stop"]
    risk = abs(entry - stop)
    tick, tick_value = contract_economics(rec["instrument"])
    points = (exit_price - entry) if is_long else (entry - exit_price)
    return {
        "result": result, "exit_reason": reason, "exit_price": exit_price, "exit_timestamp": exit_ts,
        "entry_filled": True, "fill_timestamp": pending["fill_ts"],
        "pnl_points": round(points, 6), "pnl_ticks": round(points / tick, 4),
        "pnl_r": round(points / risk, 4) if risk > 0 else None,
        "gross_pnl_dollars_1_contract": round((points / tick) * tick_value, 2),
        "mae_points": round(pending["mae_points"], 6), "mfe_points": round(pending["mfe_points"], 6),
        "mae_r": round(pending["mae_points"] / risk, 4) if risk > 0 else None,
        "mfe_r": round(pending["mfe_points"] / risk, 4) if risk > 0 else None,
        "bars_seen": pending["bars_seen"],
        "commission_assumption_dollars": None, "slippage_assumption_ticks": None,
        "costs_note": "no cost proof for this instrument; gross geometry only",
    }


def _expired(pending: dict, last_close: Optional[float], reason: str) -> dict:
    if not pending["filled"] or last_close is None:
        return {"result": "NO_FILL" if not pending["filled"] else "EXPIRED", "exit_reason": reason,
                "exit_price": None, "exit_timestamp": None, "entry_filled": pending["filled"],
                "fill_timestamp": pending["fill_ts"], "pnl_points": None, "pnl_ticks": None, "pnl_r": None,
                "mae_points": round(pending["mae_points"], 6), "mfe_points": round(pending["mfe_points"], 6),
                "bars_seen": pending["bars_seen"]}
    return _terminal(pending, "EXPIRED", float(last_close), "", reason)


def resolve_pending(
    log_dir: str | Path,
    *,
    instrument: str,
    bars: list[dict],
    current_bar_ts: str,
    for_date: Optional[date] = None,
) -> list[dict]:
    """Resolve active-epoch structural candidates on their product-aware day."""
    if not campaign_enabled():
        return []
    instrument = contract_root(instrument) or instrument
    epoch = evidence_epoch()
    if epoch is None:
        return []
    current_dt = _parse_timestamp(current_bar_ts)
    if current_dt is None:
        return []
    today = observation_day(instrument, current_dt).isoformat()

    # Callers historically passed a one-UTC-day BarHistory window. Around 00:00Z
    # that omits the prior 23:45Z bar even though the CME session is continuous.
    # Prefer a two-file canonical history when available; explicit ``bars`` stay
    # as a fallback for isolated tests and callers without BarHistory files.
    try:
        from context.bar_history import BarHistory

        canonical = BarHistory(log_dir=str(log_dir)).recent(
            instrument, 500, for_date=current_dt.date(), lookback_days=2
        )
        if canonical:
            bars = canonical
    except Exception:
        pass

    resolved: list[dict] = []
    with _state_lock(log_dir):
        campaign_state = _load_state(log_dir)
        changed = False
        for cid, pending in list(campaign_state["pending"].items()):
            rec = pending["record"]
            if rec["instrument"] != instrument or rec.get("evidence_epoch") != epoch:
                continue
            signal_ts = rec["signal_timestamp"]
            signal_day = str(rec.get("observation_date") or rec.get("trading_date") or "")
            same_day: list[dict] = []
            for bar in bars:
                bar_ts = str(bar.get("ts") or "")
                if not (signal_ts < bar_ts <= current_bar_ts):
                    continue
                try:
                    bar_day = observation_day(instrument, bar_ts).isoformat()
                except ObservationError:
                    continue
                if bar_day == signal_day:
                    same_day.append(bar)
            outcome = None
            if signal_day < today:
                outcome = _resolve_one(pending, same_day[pending["bars_seen"]:]) if same_day else None
                if outcome is None:
                    last_close = None
                    if same_day:
                        try:
                            last_close = float(same_day[-1]["close"])
                        except (KeyError, TypeError, ValueError):
                            last_close = None
                    outcome = _expired(pending, last_close, "OBSERVATION_DATE_ROLLED")
            else:
                outcome = _resolve_one(pending, same_day[pending["bars_seen"]:])
            if outcome is None:
                changed = True
                continue
            row = {**rec, "record_type": "OUTCOME", "resolved_at_bar_ts": current_bar_ts, **outcome}
            population_key(row)
            _append_evidence(log_dir, row)
            resolved.append(row)
            del campaign_state["pending"][cid]
            changed = True
        if changed:
            _save_state(log_dir, campaign_state)
    return resolved


# ── report ────────────────────────────────────────────────────────────────────

def _day(row: dict) -> Optional[str]:
    explicit = row.get("observation_date") or row.get("trading_date")
    if isinstance(explicit, str) and explicit:
        return explicit
    ts = row.get("signal_timestamp")
    return ts[:10] if isinstance(ts, str) and len(ts) >= 10 else None


def _dedupe_rows(rows: list[dict]) -> tuple[list[dict], int]:
    seen: set[tuple[str, str]] = set()
    out: list[dict] = []
    duplicates = 0
    for row in rows:
        identity = _evidence_identity(row)
        if identity is not None:
            if identity in seen:
                duplicates += 1
                continue
            seen.add(identity)
        out.append(row)
    return out, duplicates


def build_report(log_dir: str | Path, *, epoch: Optional[str] = None) -> dict:
    """Every configured population (zero counts included), never pooled."""
    config = load_config()
    gate = config["review_gate"]
    epoch = epoch if epoch is not None else evidence_epoch()
    raw_rows = read_evidence(log_dir)
    rows, duplicate_rows_ignored = _dedupe_rows(raw_rows)
    by_pop: dict[tuple, list[dict]] = {}
    unconfigured: dict[tuple, int] = {}
    pops = configured_populations(epoch)
    configured_keys = {(p["strategy"], p["instrument"], p["variant"], p["evidence_epoch"]) for p in pops}
    for row in rows:
        try:
            key = population_key(row)
        except ObservationError:
            continue
        if key in configured_keys:
            by_pop.setdefault(key, []).append(row)
        else:
            unconfigured[key] = unconfigured.get(key, 0) + 1
    try:
        pending = _load_state(log_dir)["pending"]
    except ObservationError:
        pending = {}
    populations = []
    for pop in pops:
        key = (pop["strategy"], pop["instrument"], pop["variant"], pop["evidence_epoch"])
        group = by_pop.get(key, [])
        cands = [r for r in group if r.get("record_type") == "CANDIDATE"]
        signals = [r for r in group if r.get("record_type") == "SIGNAL"]
        outcomes = [r for r in group if r.get("record_type") == "OUTCOME"]
        terminal = [r for r in outcomes if r.get("result") in TERMINAL_RESULTS]
        days = {d for r in terminal if (d := _day(r))}
        wins = sum(1 for r in terminal if r["result"] == "WIN")
        r_values = [r["pnl_r"] for r in terminal if _finite(r.get("pnl_r"))]
        ready = (len(terminal) >= gate["per_population_minimum_terminal_outcomes"]
                 and len(days) >= gate["per_population_minimum_distinct_days"])
        populations.append({
            **pop,
            "candidates": len(cands), "signals": len(signals), "outcomes": len(outcomes),
            "terminal_outcomes": len(terminal), "wins": wins, "losses": len(terminal) - wins,
            "no_fill": sum(1 for r in outcomes if r.get("result") == "NO_FILL"),
            "expired": sum(1 for r in outcomes if r.get("result") == "EXPIRED"),
            "pending": sum(1 for p in pending.values() if (p["record"]["strategy"], p["record"]["instrument"],
                                                            p["record"]["variant"], p["record"]["evidence_epoch"]) == key),
            "distinct_terminal_days": len(days),
            "net_r": round(sum(r_values), 4) if r_values else None,
            "status": ("NOT ARMED" if pop["evidence_epoch"] is None else
                       "READY FOR REVIEW" if ready else
                       "INSUFFICIENT SAMPLE" if terminal else
                       "COLLECTING" if (cands or signals) else "NOT COLLECTING"),
        })
    return {
        "campaign_id": CAMPAIGN_ID,
        "evidence_schema_version": SCHEMA_VERSION,
        "enabled": campaign_enabled(),
        "evidence_epoch": epoch,
        "identity": ["campaign_id", "strategy", "instrument", "variant", "evidence_epoch"],
        "pooling_across_populations": False,
        "grants_execution_eligibility": False,
        "collection_only_instruments": list(COLLECTION_ONLY_ROOTS),
        "review_gate": gate,
        "populations": populations,
        "unconfigured_rows": [{"strategy": k[0], "instrument": k[1], "variant": k[2], "evidence_epoch": k[3], "rows": n}
                              for k, n in sorted(unconfigured.items(), key=str)],
        "evidence_rows": len(rows),
        "evidence_rows_raw": len(raw_rows),
        "duplicate_rows_ignored": duplicate_rows_ignored,
    }
