"""MNQ vwap_hold early-signal shadow execution and outcome tracker.

Sibling to execution/entry_refresh_shadow.py, kept as a SEPARATE module (own
state file, own evidence file) so this new, less-proven upstream-timing lane
can never share state with or destabilize the already-verified moderate-
detachment lane. Reuses that module's resolve_shadow_position() verbatim —
it is already fully generic (direction/entry/stop/opened_at + a bar list),
so re-deriving the same runner-shadow trail math here would only add risk
without adding behavior. Sends no order, calls no risk engine, calls no
broker, ever.

At most ONE pending shadow position for MNQ+vwap_hold at a time — a second
detected signal while one is pending is just not opened (same dedupe spirit
as every other shadow lane in this codebase).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional

from execution.entry_refresh_shadow import resolve_shadow_position  # noqa: F401 (re-exported)

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None

STATE_FILENAME = "vwap_hold_early_shadow_state.json"
EVIDENCE_FILENAME = "vwap_hold_early_shadow_evidence.jsonl"
DEFAULT_TIMEOUT_HOURS = 8.0

_POSITION_KEY = "MNQ|vwap_hold"


def _state_path(log_dir: str | Path) -> Path:
    return Path(log_dir) / STATE_FILENAME


def get_pending_shadow_position(log_dir: str | Path) -> Optional[dict]:
    """Fail-soft: an unreadable/missing state file means 'no pending shadow'."""
    path = _state_path(log_dir)
    try:
        raw = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    positions = raw.get("positions") if isinstance(raw, dict) else None
    if not isinstance(positions, dict):
        return None
    return positions.get(_POSITION_KEY)


def open_shadow_position(
    log_dir: str | Path,
    *,
    direction: str,
    entry: float,
    stop: float,
    target: float,
    entry_ts: str,
    opened_at: Optional[str] = None,
    rr_ratio: Optional[float] = None,
) -> None:
    """Fail-soft: a persistence hiccup must never affect trading. No-ops if a
    shadow position is already pending."""
    if get_pending_shadow_position(log_dir) is not None:
        return
    path = _state_path(log_dir)
    try:
        try:
            raw = json.loads(path.read_text())
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            raw = {}
        positions = raw.get("positions") if isinstance(raw, dict) else None
        if not isinstance(positions, dict):
            positions = {}
        positions[_POSITION_KEY] = {
            "instrument": "MNQ",
            "strategy": "vwap_hold",
            "direction": direction,
            "entry": entry,
            "stop": stop,
            "target": target,
            "entry_ts": entry_ts,
            "opened_at": opened_at or datetime.now(timezone.utc).isoformat(),
            "rr_ratio": rr_ratio,
            "source": "5m_early_signal",
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"positions": positions}, separators=(",", ":")))
        tmp.replace(path)
    except OSError:
        pass


def close_shadow_position(log_dir: str | Path) -> None:
    """Fail-soft removal. No-ops if nothing was pending."""
    path = _state_path(log_dir)
    try:
        try:
            raw = json.loads(path.read_text())
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return
        positions = raw.get("positions") if isinstance(raw, dict) else None
        if not isinstance(positions, dict) or _POSITION_KEY not in positions:
            return
        del positions[_POSITION_KEY]
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"positions": positions}, separators=(",", ":")))
        tmp.replace(path)
    except OSError:
        pass


def append_vwap_hold_early_shadow_evidence(log_dir: str | Path, record: dict[str, Any]) -> None:
    """Append-only, fcntl-locked (mirrors execution/entry_refresh_shadow.py)."""
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
