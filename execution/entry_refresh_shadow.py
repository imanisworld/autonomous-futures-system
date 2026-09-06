"""Entry-refresh shadow execution and outcome tracker (Phase 1, PR #265's build).

Evaluates a hypothetical REFRESHED position (from
context/mnq_entry_refresh.py) against subsequent bars, using the SAME
runner-exit math real positions use (`execution.trailing.compute_trailed_stop`
via `execution.paper_broker.PaperBroker`, `runner_mode=True`) — so a shadow
WIN/LOSS here means exactly what it would mean live. Sends no order, calls no
risk engine, calls no broker, ever.

Persistence is deliberately simple: at most ONE pending shadow position per
(instrument, strategy) at a time (a second REFRESHED candidate while one is
still pending is just not opened — mirrors the campaign-dedupe spirit in
context/mnq_orb_reclaim_proof.py, avoids overlapping hypothetical positions
that would make evidence attribution ambiguous). State is a single
restart-safe JSON file (not date-partitioned — a shadow position can span a
day boundary exactly like a real one can); resolution is recomputed fresh
from bar history on every call, the same "stateless HTTP handler, journal-ish
file is the source of truth" pattern `webhook/runner.py` already uses for
real open positions (see its `_runner_max_fav` reconstruction block).
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional

from execution.trailing import compute_trailed_stop

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None

STATE_FILENAME = "entry_refresh_shadow_state.json"
EVIDENCE_FILENAME = "entry_refresh_shadow_evidence.jsonl"
DEFAULT_TIMEOUT_HOURS = 8.0


def _state_path(log_dir: str | Path) -> Path:
    return Path(log_dir) / STATE_FILENAME


def _position_key(instrument: str, strategy: str) -> str:
    return f"{instrument.upper()}|{strategy}"


def get_pending_shadow_position(
    log_dir: str | Path, instrument: str, strategy: str
) -> Optional[dict]:
    """Fail-soft: an unreadable/missing state file means 'no pending shadow'."""
    path = _state_path(log_dir)
    try:
        raw = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    positions = raw.get("positions") if isinstance(raw, dict) else None
    if not isinstance(positions, dict):
        return None
    return positions.get(_position_key(instrument, strategy))


def open_shadow_position(
    log_dir: str | Path,
    *,
    instrument: str,
    strategy: str,
    direction: str,
    entry: float,
    stop: float,
    target: float,
    entry_ts: str,
    opened_at: Optional[str] = None,
    refresh_policy: str = "translate",
    detachment_ticks: Optional[float] = None,
    detachment_r: Optional[float] = None,
    campaign_record: Optional[dict] = None,
) -> bool:
    """Fail-soft: a persistence hiccup must never affect trading. No-ops if a
    shadow position is already pending for this (instrument, strategy)."""
    if get_pending_shadow_position(log_dir, instrument, strategy) is not None:
        return False
    path = _state_path(log_dir)
    try:
        try:
            raw = json.loads(path.read_text())
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            raw = {}
        positions = raw.get("positions") if isinstance(raw, dict) else None
        if not isinstance(positions, dict):
            positions = {}
        positions[_position_key(instrument, strategy)] = {
            "instrument": instrument.upper(),
            "strategy": strategy,
            "direction": direction,
            "entry": entry,
            "stop": stop,
            "target": target,
            "entry_ts": entry_ts,
            "opened_at": opened_at or datetime.now(timezone.utc).isoformat(),
            "refresh_policy": refresh_policy,
            "detachment_ticks": detachment_ticks,
            "detachment_r": detachment_r,
            "campaign_record": campaign_record,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"positions": positions}, separators=(",", ":")))
        tmp.replace(path)
        return True
    except OSError:
        return False


def close_shadow_position(log_dir: str | Path, instrument: str, strategy: str) -> None:
    """Fail-soft removal. No-ops if nothing was pending."""
    path = _state_path(log_dir)
    try:
        try:
            raw = json.loads(path.read_text())
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return
        positions = raw.get("positions") if isinstance(raw, dict) else None
        if not isinstance(positions, dict):
            return
        key = _position_key(instrument, strategy)
        if key not in positions:
            return
        del positions[key]
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"positions": positions}, separators=(",", ":")))
        tmp.replace(path)
    except OSError:
        pass


def resolve_shadow_position(
    position: dict,
    bars_since_entry: List[dict],
    *,
    activation_r: float = 1.0,
    trail_r: float = 0.5,
    timeout_hours: float = DEFAULT_TIMEOUT_HOURS,
) -> Optional[dict]:
    """Pure: given a hypothetical position and every bar since its entry
    (oldest -> newest, high/low/ts required), determine whether it has
    resolved. Returns None if still open (caller keeps waiting), or a result
    dict once resolved (WIN/LOSS/BREAKEVEN/TIMEOUT).

    Re-walks the full bar history each call (idempotent, stateless) — same
    "stop hit checked BEFORE updating the favourable extreme" ordering as
    `PaperBroker._resolve_runner`, so no intra-bar look-ahead. MFE/MAE are
    tracked independently of the trail (best/worst excursion ever seen, not
    just the trailing-stop's own max_favorable).

    ``bars_since_entry`` MUST be STRICTLY after the entry bar (the caller's
    responsibility) — the hypothetical entry is priced at that bar's own
    close, so including that same bar here would check its low/high for a
    stop/target hit using price action that happened BEFORE the position
    existed (invalid look-ahead).
    """
    direction = (position.get("direction") or "").upper()
    if direction not in ("LONG", "SHORT"):
        return None
    try:
        entry = float(position["entry"])
        original_stop = float(position["stop"])
    except (KeyError, TypeError, ValueError):
        return None
    if not bars_since_entry:
        return None

    is_long = direction == "LONG"
    R = abs(entry - original_stop)
    if R <= 0:
        return None

    max_favorable = entry
    max_adverse = entry
    runner_activated = False

    for bar in bars_since_entry:
        try:
            high, low = float(bar["high"]), float(bar["low"])
        except (KeyError, TypeError, ValueError):
            continue

        active_stop, trailing = compute_trailed_stop(
            is_long=is_long, entry=entry, original_stop=original_stop,
            max_favorable=max_favorable, activation_r=activation_r, trail_r=trail_r,
        )
        runner_activated = runner_activated or trailing
        stop_hit = (low <= active_stop) if is_long else (high >= active_stop)
        if stop_hit:
            exit_price = active_stop
            pnl_ticks_dir = (exit_price - entry) if is_long else (entry - exit_price)
            result = "WIN" if pnl_ticks_dir > 0 else ("BREAKEVEN" if pnl_ticks_dir == 0 else "LOSS")
            return {
                "result": result,
                "exit_price": round(exit_price, 4),
                "exit_reason": "RUNNER_TRAIL" if trailing else "STOP_HIT",
                "exit_ts": bar.get("ts"),
                "runner_activated": runner_activated,
                "max_favorable_excursion": round(abs(max_favorable - entry), 4),
                "max_adverse_excursion": round(abs(entry - max_adverse), 4),
            }

        cur_fav = high if is_long else low
        cur_adv = low if is_long else high
        max_favorable = max(max_favorable, cur_fav) if is_long else min(max_favorable, cur_fav)
        max_adverse = min(max_adverse, cur_adv) if is_long else max(max_adverse, cur_adv)

    try:
        opened_at = datetime.fromisoformat(str(position.get("opened_at", "")).replace("Z", "+00:00"))
        age_hours = (datetime.now(timezone.utc) - opened_at).total_seconds() / 3600
    except (TypeError, ValueError):
        age_hours = 0.0
    if age_hours > timeout_hours:
        last_close = None
        for bar in reversed(bars_since_entry):
            if "close" in bar:
                try:
                    last_close = float(bar["close"])
                except (TypeError, ValueError):
                    last_close = None
                break
        exit_price = last_close if last_close is not None else max_favorable
        return {
            "result": "TIMEOUT",
            "exit_price": round(exit_price, 4) if exit_price is not None else None,
            "exit_reason": "SHADOW_TIMEOUT",
            "exit_ts": bars_since_entry[-1].get("ts"),
            "runner_activated": runner_activated,
            "max_favorable_excursion": round(abs(max_favorable - entry), 4),
            "max_adverse_excursion": round(abs(entry - max_adverse), 4),
        }

    return None


def append_entry_refresh_shadow_evidence(log_dir: str | Path, record: dict[str, Any]) -> None:
    """Append-only, fcntl-locked (mirrors ops/runner_shadow_evidence.py).
    Callers own fail-soft exception handling."""
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
