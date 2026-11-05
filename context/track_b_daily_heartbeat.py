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
SCHEMA_V2 = "track-b-daily-heartbeat-v2"
JOURNAL_NAME = "track_b_daily_heartbeat.jsonl"
STATE_DIR = "track_b_heartbeat_state"
LOCK_NAME = ".track_b_heartbeat.lock"
_LOCAL = threading.Lock()
LAST_BAR_MINIMUM_ET = time(15, 50)
_DETECTION_END_ET = time(11, 0)
_LAST_SLOT_ET = time(15, 55)
# Duplicated here so this module does not import the collector.
NATIVE_STRATEGIES = ("strat_4hr_retrigger", "strat_322_first_live")
_THREE_TWO_TWO = "strat_322_first_live"


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


def _bars_ok(bars) -> bool:
    return isinstance(bars, dict) and all(
        isinstance(key, str) and isinstance(value, dict)
        for key, value in bars.items()
    )


def _read_state(path: Path) -> dict:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"bars": {}}
    lanes = doc.get("lanes", {}) if isinstance(doc, dict) else None
    if (
        not isinstance(doc, dict)
        or not _bars_ok(doc.get("bars"))
        or not isinstance(lanes, dict)
        or any(not isinstance(key, str) or not _bars_ok(value) for key, value in lanes.items())
    ):
        raise ValueError("invalid Track B heartbeat state")
    return doc


def _window_start(strategy: str) -> time:
    if strategy == _THREE_TWO_TWO:
        return time(10, 0)
    return time(9, 30)


def bars_expected(strategy: str) -> int:
    """5-minute slots from the strategy window through 15:55 ET, inclusive."""
    start = _window_start(strategy)
    start_minutes = start.hour * 60 + start.minute
    end_minutes = _LAST_SLOT_ET.hour * 60 + _LAST_SLOT_ET.minute
    return (end_minutes - start_minutes) // 5 + 1


def _row_schema(row: dict) -> str:
    schema = row.get("schema")
    return schema if isinstance(schema, str) else SCHEMA


def _in_detection_window(strategy: str, stamp: str) -> bool:
    local = datetime.fromisoformat(stamp).astimezone(ET)
    clock = local.time().replace(tzinfo=None)
    return _window_start(strategy) <= clock < _DETECTION_END_ET


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
    """Read the single sealed v1 record, rejecting duplicate or corrupt v1 rows.

    v2 per-strategy rows are ignored here so the original reader stays strict
    about the one paper row it was written for.
    """
    matches = [
        row for row in _rows_for_day(Path(log_dir) / JOURNAL_NAME, day)
        if _row_schema(row) == SCHEMA
    ]
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
    strategy: str | None = None,
    lane: str | None = None,
    hint_present: bool | None = None,
) -> None:
    """Checkpoint one completed MNQ 5m bar; idempotent.

    Without ``strategy`` and ``lane`` this is the original v1 paper checkpoint.
    With both, the bar is stored under that strategy and lane for a v2 seal.
    """
    if (strategy is None) ^ (lane is None):
        raise ValueError("Track B heartbeat strategy and lane are set together")
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
        if strategy is None:
            state["bars"].setdefault(
                stamp,
                {
                    "checked": bool(checked),
                    "signal": bool(signal),
                    "reason": str(reason or "NO_4HR_SETUP")[:200],
                    "entry_recorded": bool(entry_recorded),
                },
            )
        else:
            lanes = state.setdefault("lanes", {})
            bucket = lanes.setdefault(f"{strategy}|{lane}", {})
            bucket.setdefault(
                stamp,
                {
                    "checked": bool(checked),
                    "signal": bool(signal),
                    "reason": str(reason or "NO_CANDIDATE")[:200],
                    "entry_recorded": bool(entry_recorded),
                    "hint_present": None if hint_present is None else bool(hint_present),
                },
            )
        _atomic_json(path, state)


def _duplicate_lanes(rows: list[dict]) -> bool:
    keys = [(row.get("strategy"), row.get("lane")) for row in rows]
    return len(keys) != len(set(keys))


def _v1_row(bars: dict, *, day: date, now: datetime, fallback_action: str) -> dict:
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
    return {
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


def _lane_status(strategy: str, bars: dict, *, day: date) -> tuple[str, bool]:
    checked_n = sum(bool(bar.get("checked")) for bar in bars.values())
    timestamps = sorted(bars)
    last_bar = timestamps[-1] if timestamps else None
    last_local = datetime.fromisoformat(last_bar).astimezone(ET) if last_bar else None
    fresh = bool(
        last_local and last_local.date() == day
        and LAST_BAR_MINIMUM_ET <= last_local.time().replace(tzinfo=None) < time(16, 0)
    )
    if checked_n == 0:
        return "STOPPED", fresh
    if checked_n < bars_expected(strategy) or not fresh:
        return "INCOMPLETE", fresh
    if not any(bool(bar.get("signal")) for bar in bars.values()):
        return "NO_SETUP", fresh
    return "OK", fresh


def _v2_row(
    strategy: str, lane: str, bars: dict, *, day: date, now: datetime, fallback_action: str,
) -> dict:
    timestamps = sorted(bars)
    status, fresh = _lane_status(strategy, bars, day=day)
    checked_n = sum(bool(bar.get("checked")) for bar in bars.values())
    signal_n = sum(bool(bar.get("signal")) for bar in bars.values())
    entry_n = sum(bool(bar.get("entry_recorded")) for bar in bars.values())
    checked_stamps = [stamp for stamp in timestamps if bars[stamp].get("checked")]
    last_checked = checked_stamps[-1] if checked_stamps else None
    last_reason = None
    for stamp in timestamps:
        reason = bars[stamp].get("reason")
        if reason:
            last_reason = str(reason)
    hint_missing = sum(
        1 for stamp in timestamps
        if _in_detection_window(strategy, stamp) and bars[stamp].get("hint_present") is not True
    )
    last_bar = timestamps[-1] if timestamps else None
    return {
        "schema": SCHEMA_V2,
        "session_date_et": day.isoformat(),
        "strategy": strategy,
        "lane": lane,
        "sealed_at": now.isoformat(),
        "source": "demo_5m_runtime" if lane == "demo" else "paper_5m_collector",
        "sealed_by": "demo_eod_fallback",
        "fallback_action": fallback_action,
        "bars_expected": bars_expected(strategy),
        "bars_checked": checked_n,
        "bars_evaluated": len(timestamps),
        "signals": signal_n,
        "signal": signal_n > 0,
        "entries": entry_n,
        "entry_recorded": entry_n > 0,
        "last_checked_et": (
            datetime.fromisoformat(last_checked).astimezone(ET).isoformat()
            if last_checked else None
        ),
        "hint_missing_bars": hint_missing,
        "status": status,
        "last_checkpoint": last_reason,
        "last_bar_time": last_bar,
        "no_signal_reason": None if signal_n else last_reason,
        "bars_fresh": fresh,
        "complete": status in {"OK", "NO_SETUP"},
    }


def seal_session(
    log_dir: str | Path, *, now: datetime, fallback_action: str,
) -> dict:
    """Seal the session after 16:00 ET.

    A paper-only day still appends exactly one v1 row. Strategy/lane checkpoints
    append one v2 row each and do not invent an empty v1 row by themselves.
    """
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
        v1_prior = [row for row in prior if _row_schema(row) == SCHEMA]
        v2_prior = [row for row in prior if _row_schema(row) == SCHEMA_V2]
        other = [row for row in prior if _row_schema(row) not in {SCHEMA, SCHEMA_V2}]
        if len(v1_prior) > 1 or other or _duplicate_lanes(v2_prior):
            raise ValueError("duplicate Track B session heartbeat")
        if prior:
            return v1_prior[0] if v1_prior else v2_prior[0]
        state = _read_state(_state_path(root, day))
        bars = state.get("bars") or {}
        lanes = state.get("lanes") or {}
        rows = []
        if bars or not lanes:
            rows.append(_v1_row(bars, day=day, now=now, fallback_action=fallback_action))
        for key in sorted(lanes):
            strategy, lane = key.split("|", 1)
            rows.append(_v2_row(
                strategy, lane, lanes[key], day=day, now=now, fallback_action=fallback_action,
            ))
        with journal.open("a", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return rows[0]
