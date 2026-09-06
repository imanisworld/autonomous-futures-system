"""Additive, fail-soft evidence envelope for the MNQ forward A/B campaign.

This module owns only hypothetical campaign state and append-only evidence.
It has no broker, risk-engine, DecisionOutput, or DailyState dependency.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from execution.entry_refresh_shadow import resolve_shadow_position

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None

CAMPAIGN_ID = "forward_ab_2026_08_v1"
SCHEMA_VERSION = "1.0.0"
EVIDENCE_FILENAME = f"{CAMPAIGN_ID}.jsonl"
STATE_FILENAME = f"{CAMPAIGN_ID}_state.json"
ENV_NAME = "FORWARD_EVIDENCE_CAMPAIGN"
TICK_SIZE = 0.25
TICK_VALUE = 0.50
SLIPPAGE_TICKS = 1.0
COMMISSION_DOLLARS = 1.48
TIMEOUT_HOURS = 8.0

REQUIRED_FIELDS = frozenset({
    "evidence_schema_version", "campaign_id", "record_type", "event_id",
    "candidate_id", "instrument", "strategy", "variant", "direction",
    "signal_timestamp", "source_timeframe", "session", "regime",
    "market_condition", "original_entry", "original_stop", "original_target",
    "modified_entry", "modified_stop", "modified_target", "entry_policy",
    "exit_policy", "detachment_ticks", "detachment_r", "failed_gates",
    "reject_reason", "fillable_state", "hypothetical_fill_price",
    "slippage_assumption_ticks", "commission_assumption_dollars", "mfe_points",
    "mae_points", "terminal_state", "exit_reason", "exit_timestamp",
    "gross_pnl_dollars", "net_pnl_dollars", "generating_git_sha",
    "provenance_status",
})


class EvidenceValidationError(ValueError):
    pass


def campaign_enabled() -> bool:
    return os.getenv(ENV_NAME, "").strip() == CAMPAIGN_ID


def generating_sha() -> tuple[str, str]:
    for name in ("AFS_RELEASE_SHA", "RELEASE_SHA", "GIT_SHA"):
        value = os.getenv(name, "").strip()
        if value:
            return value, f"environment:{name}"
    for path in (Path.cwd() / "release_manifest.json", Path(__file__).parents[1] / "release_manifest.json"):
        try:
            value = json.loads(path.read_text()).get("repo", {}).get("commit")
            if value:
                return str(value), f"manifest:{path.name}"
        except (OSError, ValueError, TypeError):
            continue
    return "UNAVAILABLE", "explicitly_unavailable"


def _iso(value: Any) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _episode_timestamp(signal_timestamp: str) -> str:
    """Canonical 15-minute BAR-OPEN bucket shared by 5m/15m variants.

    TradingView sends Pine's ``time`` (the bar-open timestamp), not
    ``time_close``; see tradingview/risksentinel_context.pine and
    webhook/state_builder.py's ``bar_start`` assignment.
    """
    try:
        dt = datetime.fromisoformat(signal_timestamp.replace("Z", "+00:00"))
        dt = dt.replace(minute=(dt.minute // 15) * 15, second=0, microsecond=0)
        return dt.isoformat()
    except ValueError:
        return signal_timestamp


def stable_event_id(
    *, instrument: str, strategy: str, direction: str, signal_timestamp: str
) -> str:
    raw = "|".join((CAMPAIGN_ID, instrument.upper(), strategy, direction, _episode_timestamp(signal_timestamp)))
    return "fab-evt-" + hashlib.sha256(raw.encode()).hexdigest()[:24]


def stable_candidate_id(event_id: str, variant: str, source_timeframe: str) -> str:
    raw = "|".join((event_id, variant, str(source_timeframe)))
    return "fab-cand-" + hashlib.sha256(raw.encode()).hexdigest()[:24]


def validate_record(record: dict[str, Any]) -> None:
    missing = sorted(REQUIRED_FIELDS - record.keys())
    if missing:
        raise EvidenceValidationError(f"missing campaign evidence fields: {', '.join(missing)}")
    if record["campaign_id"] != CAMPAIGN_ID or record["evidence_schema_version"] != SCHEMA_VERSION:
        raise EvidenceValidationError("wrong campaign/schema identity")
    if record["instrument"] != "MNQ":
        raise EvidenceValidationError("campaign evidence is MNQ-only")
    if record["variant"] not in {"control", "modified", "observer"}:
        raise EvidenceValidationError("invalid campaign variant")
    if not isinstance(record["failed_gates"], list):
        raise EvidenceValidationError("failed_gates must be a list")
    if not record["generating_git_sha"] or not record["provenance_status"]:
        raise EvidenceValidationError("explicit provenance is required")


def append_record(log_dir: str | Path, record: dict[str, Any]) -> bool:
    validate_record(record)
    if not campaign_enabled():
        return False
    path = Path(log_dir) / EVIDENCE_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    full = {"observed_at": datetime.now(timezone.utc).isoformat(), **record}
    with open(path, "a") as handle:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            handle.write(json.dumps(full, separators=(",", ":")) + "\n")
            handle.flush()
        finally:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    return True


def candidate_record(
    *, strategy: str, variant: str, direction: str, signal_timestamp: str,
    source_timeframe: str, session: Optional[str], regime: Optional[str],
    market_condition: Optional[str], original_entry: float, original_stop: float,
    original_target: float, entry_policy: str, exit_policy: str,
    modified_entry: Optional[float] = None, modified_stop: Optional[float] = None,
    modified_target: Optional[float] = None, detachment_ticks: Optional[float] = None,
    detachment_r: Optional[float] = None, failed_gates: Optional[list] = None,
    reject_reason: Optional[str] = None, fillable_state: str = "ARMED",
    hypothetical_fill_price: Optional[float] = None,
    terminal_state: str = "OPEN", event_id: Optional[str] = None,
) -> dict[str, Any]:
    signal_timestamp = _iso(signal_timestamp)
    event_id = event_id or stable_event_id(
        instrument="MNQ", strategy=strategy, direction=direction,
        signal_timestamp=signal_timestamp,
    )
    sha, provenance = generating_sha()
    record = {
        "evidence_schema_version": SCHEMA_VERSION,
        "campaign_id": CAMPAIGN_ID,
        "record_type": "CANDIDATE",
        "event_id": event_id,
        "candidate_id": stable_candidate_id(event_id, variant, source_timeframe),
        "instrument": "MNQ",
        "strategy": strategy,
        "variant": variant,
        "direction": direction,
        "signal_timestamp": signal_timestamp,
        "source_timeframe": str(source_timeframe),
        "session": session,
        "regime": regime,
        "market_condition": market_condition,
        "original_entry": original_entry,
        "original_stop": original_stop,
        "original_target": original_target,
        "modified_entry": modified_entry,
        "modified_stop": modified_stop,
        "modified_target": modified_target,
        "entry_policy": entry_policy,
        "exit_policy": exit_policy,
        "detachment_ticks": detachment_ticks,
        "detachment_r": detachment_r,
        "failed_gates": list(failed_gates or []),
        "reject_reason": reject_reason,
        "fillable_state": fillable_state,
        "hypothetical_fill_price": hypothetical_fill_price,
        "slippage_assumption_ticks": SLIPPAGE_TICKS,
        "commission_assumption_dollars": COMMISSION_DOLLARS,
        "mfe_points": None,
        "mae_points": None,
        "terminal_state": terminal_state,
        "exit_reason": reject_reason if terminal_state == "REJECTED" else None,
        "exit_timestamp": signal_timestamp if terminal_state == "REJECTED" else None,
        "gross_pnl_dollars": None,
        "net_pnl_dollars": None,
        "generating_git_sha": sha,
        "provenance_status": provenance,
    }
    validate_record(record)
    return record


def outcome_record(position: dict, outcome: dict[str, Any]) -> dict[str, Any]:
    base = dict(position["campaign_record"])
    signal_sha = base.get("generating_git_sha")
    outcome_sha, outcome_provenance = generating_sha()
    exit_price = outcome.get("exit_price")
    entry = base.get("hypothetical_fill_price")
    gross = None
    net = None
    if exit_price is not None and entry is not None:
        points = (float(exit_price) - float(entry)) * (1 if base["direction"] == "LONG" else -1)
        gross = round((points / TICK_SIZE) * TICK_VALUE, 2)
        net = round(gross - COMMISSION_DOLLARS - (SLIPPAGE_TICKS * TICK_VALUE), 2)
    result = str(outcome.get("result") or "EXPIRED")
    terminal = "EXPIRED" if result in {"TIMEOUT", "NO_FILL", "EXPIRED"} else result
    base.update({
        "record_type": "OUTCOME",
        "fillable_state": "NO_FILL" if result == "NO_FILL" else "FILLED",
        "mfe_points": outcome.get("max_favorable_excursion"),
        "mae_points": outcome.get("max_adverse_excursion"),
        "terminal_state": terminal,
        "exit_reason": outcome.get("exit_reason") or terminal,
        "exit_timestamp": outcome.get("exit_ts"),
        "gross_pnl_dollars": gross,
        "net_pnl_dollars": net,
        "signal_generating_git_sha": signal_sha,
        "generating_git_sha": outcome_sha,
        "provenance_status": outcome_provenance,
    })
    validate_record(base)
    return base


def _state_path(log_dir: str | Path) -> Path:
    return Path(log_dir) / STATE_FILENAME


def _load_state(log_dir: str | Path) -> dict:
    try:
        raw = json.loads(_state_path(log_dir).read_text())
    except (OSError, ValueError):
        raw = {}
    return {
        "positions": raw.get("positions", {}) if isinstance(raw.get("positions"), dict) else {},
        "seen_candidate_ids": raw.get("seen_candidate_ids", []) if isinstance(raw.get("seen_candidate_ids"), list) else [],
    }


def _save_state(log_dir: str | Path, state: dict) -> None:
    path = _state_path(log_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, separators=(",", ":")))
    tmp.replace(path)


def open_campaign_position(log_dir: str | Path, record: dict[str, Any]) -> bool:
    """Persist one hypothetical position; dedupe by deterministic candidate ID."""
    validate_record(record)
    if not campaign_enabled():
        return False
    state = _load_state(log_dir)
    candidate_id = record["candidate_id"]
    if candidate_id in state["seen_candidate_ids"]:
        return False
    if any(
        p.get("campaign_record", {}).get("strategy") == record["strategy"]
        and p.get("campaign_record", {}).get("variant") == record["variant"]
        for p in state["positions"].values()
    ):
        return False
    state["seen_candidate_ids"].append(candidate_id)
    state["positions"][candidate_id] = {
        "campaign_record": record,
        "direction": record["direction"],
        "entry": record["original_entry"],
        "stop": record["original_stop"],
        "target": record["original_target"],
        "signal_ts": record["signal_timestamp"],
        "entry_ts": record["signal_timestamp"] if record["fillable_state"] == "FILLED" else None,
        "opened_at": record["signal_timestamp"],
    }
    _save_state(log_dir, state)
    return append_record(log_dir, record)


def resolve_resting_bracket(
    position: dict,
    forward_bars: list[dict],
    *,
    final: bool = False,
    pessimistic_both_hit: bool = True,
) -> Optional[dict[str, Any]]:
    """Resolve a resting-entry bracket, including the intrabar fill bar.

    OHLC cannot reveal whether entry/stop/target was touched first inside the
    fill bar.  The pre-registered conservative convention treats a stop touch
    as a loss, resolves stop before target when both are present, and does not
    award a target-only fill-bar touch because target-before-entry is possible.
    Ambiguity is explicit instead of silently dropping or crediting the bar.
    """
    direction = str(position.get("direction") or "").upper()
    if direction not in {"LONG", "SHORT"}:
        return None
    try:
        entry = float(position["entry"])
        stop = float(position["stop"])
        target = float(position["target"])
    except (KeyError, TypeError, ValueError):
        return None

    fill_idx = None
    for idx, bar in enumerate(forward_bars):
        try:
            high, low = float(bar["high"]), float(bar["low"])
        except (KeyError, TypeError, ValueError):
            continue
        if low <= entry <= high:
            fill_idx = idx
            break
    if fill_idx is None:
        if not final:
            return None
        return {
            "result": "NO_FILL", "exit_reason": "NO_FILL",
            "exit_price": None, "exit_ts": None,
            "entry_filled": False, "fill_ts": None,
            "max_favorable_excursion": None,
            "max_adverse_excursion": None,
            "fill_bar_ambiguous": False,
            "intrabar_policy": "PESSIMISTIC_STOP_FIRST",
        }

    is_long = direction == "LONG"
    mfe = mae = 0.0
    fill_bar_target_ambiguous_ignored = False
    fill_ts = forward_bars[fill_idx].get("ts")
    for idx in range(fill_idx, len(forward_bars)):
        bar = forward_bars[idx]
        high, low = float(bar["high"]), float(bar["low"])
        # Full fill-bar excursion is not causally attributable: some of its
        # high/low may precede the entry touch. Track excursions only from
        # subsequent bars, while still resolving the fill bar conservatively.
        if idx > fill_idx:
            mfe = max(mfe, (high - entry) if is_long else (entry - low))
            mae = max(mae, (entry - low) if is_long else (high - entry))
        target_hit = high >= target if is_long else low <= target
        stop_hit = low <= stop if is_long else high >= stop
        if not (target_hit or stop_hit):
            continue
        if idx == fill_idx and target_hit and not stop_hit:
            # The target may have traded before the resting entry. It is not
            # earned without lower-timeframe/order evidence proving entry-first.
            fill_bar_target_ambiguous_ignored = True
            continue
        won = target_hit and (not stop_hit or not pessimistic_both_hit)
        return {
            "result": "WIN" if won else "LOSS",
            "exit_price": target if won else stop,
            "exit_reason": "TARGET_HIT" if won else "STOP_HIT",
            "exit_ts": bar.get("ts"),
            "entry_filled": True,
            "fill_ts": fill_ts,
            "max_favorable_excursion": None if idx == fill_idx else round(max(0.0, mfe), 4),
            "max_adverse_excursion": None if idx == fill_idx else round(max(0.0, mae), 4),
            "fill_bar_ambiguous": idx == fill_idx,
            "fill_bar_target_ambiguous_ignored": fill_bar_target_ambiguous_ignored,
            "intrabar_policy": "PESSIMISTIC_STOP_FIRST",
            "excursion_policy": "EXCLUDE_AMBIGUOUS_FILL_BAR",
        }
    if not final:
        return None
    return {
        "result": "OPEN", "exit_reason": "WINDOW_OPEN",
        "exit_price": None, "exit_ts": None,
        "entry_filled": True, "fill_ts": fill_ts,
        "max_favorable_excursion": round(max(0.0, mfe), 4),
        "max_adverse_excursion": round(max(0.0, mae), 4),
        "fill_bar_ambiguous": False,
        "fill_bar_target_ambiguous_ignored": fill_bar_target_ambiguous_ignored,
        "intrabar_policy": "PESSIMISTIC_STOP_FIRST",
        "excursion_policy": "EXCLUDE_AMBIGUOUS_FILL_BAR",
    }


def _runner_fill_bar_stop(position: dict, fill_bar: dict) -> Optional[dict[str, Any]]:
    """Conservative fill-bar check for a resting entry with runner exit.

    Only the original stop can terminate a runner on its fill bar. Favorable
    fill-bar movement is not used to activate/trail the runner because OHLC
    cannot prove that movement happened after the entry touch.
    """
    stop = float(position["stop"])
    high, low = float(fill_bar["high"]), float(fill_bar["low"])
    is_long = position["direction"] == "LONG"
    stop_hit = low <= stop if is_long else high >= stop
    if not stop_hit:
        return None
    return {
        "result": "LOSS", "exit_price": stop, "exit_reason": "STOP_HIT",
        "exit_ts": fill_bar.get("ts"), "runner_activated": False,
        "max_favorable_excursion": None,
        "max_adverse_excursion": None,
        "fill_bar_ambiguous": True,
        "intrabar_policy": "PESSIMISTIC_STOP_FIRST",
        "excursion_policy": "EXCLUDE_AMBIGUOUS_FILL_BAR",
    }


def record_canonical_candidates(log_dir: str | Path, state_obj, candidates: Iterable[Any]) -> list[str]:
    if not campaign_enabled() or getattr(state_obj, "instrument", None) != "MNQ":
        return []
    created = []
    for candidate in candidates:
        strategy_name = candidate.get("strategy") if isinstance(candidate, dict) else candidate.strategy
        if strategy_name not in {"vwap_hold_observed", "vwap_rejection_observed"}:
            continue
        def value(name):
            return candidate.get(name) if isinstance(candidate, dict) else getattr(candidate, name)
        strategy = strategy_name.removesuffix("_observed")
        variant = "control" if strategy == "vwap_hold" else "observer"
        record = candidate_record(
            strategy=strategy, variant=variant, direction=value("direction"),
            signal_timestamp=state_obj.timestamp.isoformat(),
            source_timeframe=getattr(state_obj.ohlc, "timeframe", "15"),
            session=state_obj.session, regime=getattr(state_obj, "structural_regime", None),
            market_condition=state_obj.market_condition,
            original_entry=value("entry"), original_stop=value("stop"),
            original_target=value("target"), entry_policy="canonical_resting_entry",
            exit_policy="runner_1R_0.5R" if strategy == "vwap_hold" else "fixed_bracket",
        )
        if open_campaign_position(log_dir, record):
            created.append(record["candidate_id"])
    return created


def resolve_canonical_positions(
    log_dir: str | Path, *, instrument: str, bars: list[dict], current_bar_ts: str,
    activation_r: float = 1.0, trail_r: float = 0.5,
) -> list[dict]:
    """Resolve retained canonical positions using only bars after signal/fill."""
    if not campaign_enabled() or instrument != "MNQ":
        return []
    state = _load_state(log_dir)
    resolved = []
    changed = False
    for candidate_id, position in list(state["positions"].items()):
        record = position.get("campaign_record", {})
        if record.get("instrument") != instrument:
            continue
        signal_ts = str(position.get("signal_ts") or "")
        forward = [b for b in bars if str(b.get("ts") or "") > signal_ts and str(b.get("ts") or "") <= current_bar_ts]
        fill_bar = None
        if not position.get("entry_ts"):
            entry = float(position["entry"])
            fill_bar = next((b for b in forward if float(b.get("low", entry + 1)) <= entry <= float(b.get("high", entry - 1))), None)
            if fill_bar is not None:
                position["entry_ts"] = str(fill_bar.get("ts"))
                record["hypothetical_fill_price"] = entry
                position["campaign_record"] = record
                if record.get("exit_policy") == "fixed_bracket":
                    high, low = float(fill_bar["high"]), float(fill_bar["low"])
                    is_long = position["direction"] == "LONG"
                    target_hit = high >= float(position["target"]) if is_long else low <= float(position["target"])
                    stop_hit = low <= float(position["stop"]) if is_long else high >= float(position["stop"])
                    if target_hit and not stop_hit:
                        position["fill_bar_target_ambiguous_ignored"] = True
                changed = True
        entry_ts = position.get("entry_ts")
        if not entry_ts:
            outcome = _no_fill_if_expired(position, current_bar_ts)
        else:
            after_fill = [b for b in forward if str(b.get("ts") or "") > str(entry_ts)]
            if record.get("exit_policy") == "fixed_bracket":
                # Examine the fill bar exactly once, on the call that first
                # discovers it. Later calls start strictly after retained
                # entry_ts, so they neither reclassify a later bar as a fill
                # nor require the original fill bar to remain in history.
                outcome = (
                    resolve_resting_bracket(position, forward)
                    if fill_bar is not None
                    else _resolve_fixed(position, after_fill, current_bar_ts)
                )
            else:
                outcome = _runner_fill_bar_stop(position, fill_bar) if fill_bar is not None else None
                if outcome is None:
                    outcome = resolve_shadow_position(
                        position, after_fill, activation_r=activation_r,
                        trail_r=trail_r, timeout_hours=TIMEOUT_HOURS,
                    )
        if outcome is None:
            continue
        if position.get("fill_bar_target_ambiguous_ignored"):
            outcome["fill_bar_target_ambiguous_ignored"] = True
        row = outcome_record(position, outcome)
        for field in (
            "fill_bar_ambiguous", "fill_bar_target_ambiguous_ignored",
            "intrabar_policy", "excursion_policy", "fill_ts",
        ):
            if field in outcome:
                row[field] = outcome[field]
        append_record(log_dir, row)
        resolved.append(row)
        del state["positions"][candidate_id]
        changed = True
    if changed:
        _save_state(log_dir, state)
    return resolved


def _hours_between(start: str, end: str) -> float:
    try:
        a = datetime.fromisoformat(start.replace("Z", "+00:00"))
        b = datetime.fromisoformat(end.replace("Z", "+00:00"))
        return (b - a).total_seconds() / 3600.0
    except (TypeError, ValueError):
        return 0.0


def _no_fill_if_expired(position: dict, current_bar_ts: str) -> Optional[dict]:
    if _hours_between(str(position.get("signal_ts")), current_bar_ts) <= TIMEOUT_HOURS:
        return None
    return {
        "result": "NO_FILL", "exit_reason": "NO_FILL_TIMEOUT",
        "exit_ts": current_bar_ts, "exit_price": None,
        "max_favorable_excursion": None, "max_adverse_excursion": None,
    }


def _resolve_fixed(position: dict, bars: list[dict], current_bar_ts: str) -> Optional[dict]:
    entry, stop, target = (float(position[k]) for k in ("entry", "stop", "target"))
    is_long = position["direction"] == "LONG"
    mfe = mae = 0.0
    for bar in bars:
        high, low = float(bar["high"]), float(bar["low"])
        mfe = max(mfe, (high - entry) if is_long else (entry - low))
        mae = max(mae, (entry - low) if is_long else (high - entry))
        target_hit = high >= target if is_long else low <= target
        stop_hit = low <= stop if is_long else high >= stop
        if target_hit or stop_hit:
            won = target_hit and not stop_hit
            return {
                "result": "WIN" if won else "LOSS",
                "exit_price": target if won else stop,
                "exit_reason": "TARGET_HIT" if won else "STOP_HIT",
                "exit_ts": bar.get("ts"),
                "max_favorable_excursion": round(max(0.0, mfe), 4),
                "max_adverse_excursion": round(max(0.0, mae), 4),
            }
    if _hours_between(str(position.get("entry_ts")), current_bar_ts) > TIMEOUT_HOURS:
        last_close = float(bars[-1].get("close", entry)) if bars else entry
        return {
            "result": "EXPIRED", "exit_price": last_close,
            "exit_reason": "SHADOW_TIMEOUT", "exit_ts": current_bar_ts,
            "max_favorable_excursion": round(mfe, 4),
            "max_adverse_excursion": round(mae, 4),
        }
    return None
