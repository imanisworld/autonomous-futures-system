"""Evidence-only Track B session heartbeat; never participates in trading decisions.

The paper 5-minute collector checkpoints evaluated bars. The independent DEMO
EOD fallback seals exactly one JSONL row per session, including empty/failed
collection as INCOMPLETE (never falsely "no signal"). All files live in the
mutable shared logs directory, outside immutable releases.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from contextlib import contextmanager
from datetime import date, datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None

ET = ZoneInfo("America/New_York")
SCHEMA = "track-b-daily-heartbeat-v1"
JOURNAL_NAME = "track_b_daily_heartbeat.jsonl"
STATE_DIR = "track_b_heartbeat_state"
LOCK_NAME = ".track_b_heartbeat.lock"
_LOCAL = threading.Lock()
LAST_BAR_MINIMUM_ET = time(15, 50)


@contextmanager
def _locked(log_dir: Path):
    log_dir.mkdir(parents=True, exist_ok=True)
    with _LOCAL:
        with (log_dir / LOCK_NAME).open("a") as handle:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _state_path(log_dir: Path, day: date) -> Path:
    return log_dir / STATE_DIR / (day.isoformat() + ".json")


def _read_state(path: Path) -> dict:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"bars": {}}
    if (
        not isinstance(doc, dict)
        or not isinstance(doc.get("bars"), dict)
        or any(
            not isinstance(key, str) or not isinstance(value, dict)
            for key, value in doc["bars"].items()
        )
    ):
        raise ValueError("invalid Track B heartbeat state")
    return doc


def _atomic_json(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".trackb.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(doc, handle, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def _rows_for_day(path: Path, day: date) -> list[dict]:
    if not path.exists():
        return []
    matches = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        obj = json.loads(line)
        if not isinstance(obj, dict):
            raise ValueError("invalid Track B heartbeat journal row")
        if obj.get("session_date_et") == day.isoformat():
            matches.append(obj)
    return matches


def read_session(log_dir: str | Path, day: date) -> dict | None:
    """Read the single sealed daily record, rejecting duplicate or corrupt rows."""
    matches = _rows_for_day(Path(log_dir) / JOURNAL_NAME, day)
    if len(matches) > 1:
        raise ValueError("duplicate Track B session heartbeat")
    return matches[0] if matches else None


def record_bar(
    log_dir: str | Path,
    *,
    current_ts: datetime,
    checked: bool,
    signal: bool,
    reason: str,
    entry_recorded: bool,
) -> None:
    """Checkpoint a successfully evaluated completed MNQ 5m bar; idempotent."""
    if current_ts.tzinfo is None:
        raise ValueError("Track B bar timestamp must be timezone-aware")
    local = current_ts.astimezone(ET)
    clock = local.time().replace(tzinfo=None)
    if local.weekday() >= 5 or not (time(9, 30) <= clock < time(16, 0)):
        return
    day = local.date()
    root = Path(log_dir)
    stamp = current_ts.astimezone(timezone.utc).isoformat()
    with _locked(root):
        if _rows_for_day(root / JOURNAL_NAME, day):
            # EOD has already sealed this session; never revise a sealed row.
            return
        path = _state_path(root, day)
        state = _read_state(path)
        state["bars"].setdefault(
            stamp,
            {
                "checked": bool(checked),
                "signal": bool(signal),
                "reason": str(reason or "NO_4HR_SETUP")[:200],
                "entry_recorded": bool(entry_recorded),
            },
        )
        _atomic_json(path, state)


def seal_session(
    log_dir: str | Path, *, now: datetime, fallback_action: str,
) -> dict:
    """Append ONE post-16:00 ET row, including INCOMPLETE when bars are absent."""
    if now.tzinfo is None:
        raise ValueError("Track B EOD timestamp must be timezone-aware")
    local = now.astimezone(ET)
    if local.weekday() >= 5 or local.time().replace(tzinfo=None) < time(16, 0):
        raise ValueError("Track B heartbeat cannot be sealed before session close")
    root = Path(log_dir)
    day = local.date()
    with _locked(root):
        journal = root / JOURNAL_NAME
        prior = _rows_for_day(journal, day)
        if len(prior) > 1:
            raise ValueError("duplicate Track B session heartbeat")
        if prior:
            return prior[0]
        bars = _read_state(_state_path(root, day))["bars"]
        timestamps = sorted(bars)
        last_bar = timestamps[-1] if timestamps else None
        checked = sum(bool(bar.get("checked")) for bar in bars.values())
        signal = any(bool(bar.get("signal")) for bar in bars.values())
        entry = any(bool(bar.get("entry_recorded")) for bar in bars.values())
        if not timestamps:
            no_reason = "NO_BARS_EVALUATED"
        elif not checked:
            no_reason = "NO_4HR_WINDOW_EVALUATION"
        else:
            candidates = [
                str(bar.get("reason") or "") for _, bar in sorted(bars.items())
                if bar.get("checked") and bar.get("reason")
            ]
            no_reason = candidates[-1] if candidates else "NO_4HR_SETUP"
        last_local = datetime.fromisoformat(last_bar).astimezone(ET) if last_bar else None
        fresh = bool(
            last_local and last_local.date() == day
            and LAST_BAR_MINIMUM_ET <= last_local.time().replace(tzinfo=None) < time(16, 0)
        )
        fallback_ok = fallback_action != "FAIL_CLOSED"
        row = {
            "schema": SCHEMA,
            "session_date_et": day.isoformat(),
            "sealed_at": now.isoformat(),
            "source": "paper_5m_collector",
            "sealed_by": "demo_eod_fallback",
            "fallback_action": fallback_action,
            "bars_evaluated": len(timestamps),
            "last_bar_time": last_bar,
            "setups_checked": checked,
            "signal": signal,
            "no_signal_reason": None if signal else no_reason,
            "entry_recorded": entry,
            "bars_fresh": fresh,
            "complete": bool(timestamps and checked and fresh and fallback_ok),
        }
        with journal.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return row
