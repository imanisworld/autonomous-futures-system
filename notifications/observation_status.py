"""Persistent per-ticker Discord observer status.

Presentation-only state for the observation route. The evidence journal remains
authoritative; this file only remembers the latest card contents and Discord
message ids so repeated observation events update one message per market.
"""
from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

from notifications import plain_english as pe

_ET = ZoneInfo("America/New_York")
_LOCK = threading.Lock()
_RECENT_IDS_MAX = 200


@dataclass(frozen=True)
class StatusUpdate:
    root: str
    message_id: Optional[str]
    text: str
    represented_events: int


def _state_path() -> Path:
    explicit = str(os.getenv("DISCORD_OBSERVATION_STATUS_STATE", "")).strip()
    if explicit:
        return Path(explicit)
    shared = str(os.getenv("AFS_SHARED_DIR", "")).strip()
    base = Path(shared) if shared else Path("logs")
    return base / "discord_observation_status.json"


def _load(path: Path) -> dict:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception:
        return {}


def _save(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _event_time(event: dict) -> Optional[str]:
    for key in ("exit_timestamp", "resolved_at_bar_ts", "signal_timestamp", "observed_at"):
        value = event.get(key)
        if value:
            return str(value)
    return None


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError):
        return None


def _trading_date(event: dict) -> str:
    explicit = str(event.get("trading_date") or event.get("observation_date") or "").strip()
    if explicit:
        return explicit
    dt = _parse_dt(_event_time(event))
    return (dt.astimezone(_ET).date().isoformat() if dt else datetime.now(_ET).date().isoformat())


def _event_key(event: dict) -> str:
    parts = (
        str(event.get("candidate_id") or ""),
        str(event.get("record_type") or ""),
        str(event.get("result") or ""),
        str(_event_time(event) or ""),
        str(event.get("strategy") or ""),
    )
    return "|".join(parts)


def _fresh_root(root: str, day: str, *, message_id: Optional[str] = None) -> dict:
    return {
        "root": root,
        "trading_date": day,
        "message_id": message_id,
        "setups": 0,
        "resolved": 0,
        "wins": 0,
        "losses": 0,
        "breakeven": 0,
        "recent_event_ids": [],
        "last_event": {},
    }


def _apply_one(row: dict, event: dict) -> bool:
    key = _event_key(event)
    recent = list(row.get("recent_event_ids") or [])
    if key in recent:
        return False

    kind = str(event.get("record_type") or "").upper()
    if kind in ("CANDIDATE", "SIGNAL"):
        row["setups"] = int(row.get("setups") or 0) + 1
    elif kind == "OUTCOME":
        row["resolved"] = int(row.get("resolved") or 0) + 1
        result = str(event.get("result") or "").upper()
        if result == "WIN":
            row["wins"] = int(row.get("wins") or 0) + 1
        elif result == "LOSS":
            row["losses"] = int(row.get("losses") or 0) + 1
        else:
            row["breakeven"] = int(row.get("breakeven") or 0) + 1

    row["last_event"] = {
        "record_type": kind,
        "strategy": event.get("strategy"),
        "direction": event.get("direction"),
        "result": event.get("result"),
        "exit_reason": event.get("exit_reason"),
        "timestamp": _event_time(event),
        "entry": event.get("entry"),
        "stop": event.get("stop"),
        "target": event.get("target"),
        "gross_pnl_dollars_1_contract": event.get("gross_pnl_dollars_1_contract"),
    }
    recent.append(key)
    row["recent_event_ids"] = recent[-_RECENT_IDS_MAX:]
    return True


def _current_line(last: dict) -> str:
    kind = str(last.get("record_type") or "").upper()
    setup = pe.setup(last.get("strategy"))
    side = pe.side(last.get("direction")).lower()
    if kind == "OUTCOME":
        result = str(last.get("result") or "").upper()
        words = {"WIN": "won", "LOSS": "lost", "BREAKEVEN": "finished flat"}.get(
            result, result.lower() or "resolved"
        )
        return f"Last observation resolved — {setup} {side} {words}"
    if kind in ("CANDIDATE", "SIGNAL"):
        return f"Watching — {setup} {side} setup spotted"
    return "Watching for the next qualifying setup"


def _last_result_line(last: dict) -> Optional[str]:
    if str(last.get("record_type") or "").upper() != "OUTCOME":
        return None
    dollars = last.get("gross_pnl_dollars_1_contract")
    if isinstance(dollars, (int, float)):
        return f"{pe.money(float(dollars))} on 1 observed contract"
    reason = str(last.get("exit_reason") or "").strip()
    return pe.exit_reason(reason) if reason else None


def render_status(row: dict) -> str:
    root = str(row.get("root") or "?")
    last = dict(row.get("last_event") or {})
    lines = [
        f"👀 {root} observer · active",
        f"Market: {pe.market(root)}",
        f"Date: {row.get('trading_date') or '?'}",
        f"Today: {int(row.get('setups') or 0)} setups · {int(row.get('resolved') or 0)} resolved",
        (
            f"Outcomes: {int(row.get('wins') or 0)} won · "
            f"{int(row.get('losses') or 0)} lost · "
            f"{int(row.get('breakeven') or 0)} other"
        ),
        f"Current: {_current_line(last)}",
    ]
    result = _last_result_line(last)
    if result:
        lines.append(f"Last result: {result}")
    if last.get("timestamp"):
        lines.append(f"Updated: {pe.et_time(last.get('timestamp'))}")
    lines.extend(
        [
            "Action: No action — evidence collection continues",
            "OBSERVATION ONLY · no order, risk, deploy, or promotion authority",
        ]
    )
    return "\n".join(lines)


def build_status_updates(events: Iterable[dict]) -> tuple[list[StatusUpdate], int]:
    """Apply events and return one final update per touched ticker.

    Duplicate events are ignored, daily counters reset on the first event of a
    new trading date, and the previous Discord message id is retained across
    daily resets so the same ticker message can keep being edited.
    """
    clean = [e for e in events if isinstance(e, dict)]
    if not clean:
        return [], 0

    path = _state_path()
    with _LOCK:
        state = _load(path)
        touched: dict[str, int] = {}

        for event in clean:
            root = str(event.get("instrument") or "").strip()
            kind = str(event.get("record_type") or "").strip().upper()
            if not root or kind not in ("CANDIDATE", "SIGNAL", "OUTCOME"):
                continue
            day = _trading_date(event)
            row = state.get(root)
            if not isinstance(row, dict):
                row = _fresh_root(root, day)
            elif str(row.get("trading_date") or "") != day:
                row = _fresh_root(root, day, message_id=row.get("message_id"))
            if _apply_one(row, event):
                touched[root] = touched.get(root, 0) + 1
            state[root] = row

        if touched:
            _save(path, state)

        updates = [
            StatusUpdate(
                root=root,
                message_id=state[root].get("message_id"),
                text=render_status(state[root]),
                represented_events=count,
            )
            for root, count in sorted(touched.items())
        ]
        return updates, sum(touched.values())


def record_message_id(root: str, message_id: Optional[str]) -> None:
    if not root or not message_id:
        return
    path = _state_path()
    with _LOCK:
        state = _load(path)
        row = state.get(root)
        if not isinstance(row, dict):
            row = _fresh_root(root, datetime.now(_ET).date().isoformat())
        row["message_id"] = str(message_id)
        state[root] = row
        _save(path, state)


def reset_state_for_tests(path: Optional[Path] = None) -> None:
    target = path or _state_path()
    try:
        target.unlink()
    except FileNotFoundError:
        pass
