"""Read-only master evidence registry for the EOW paper-collection report.

The registry does not collect, repair, promote, or execute anything.  It only
summarizes evidence already written by existing lanes.  Missing metadata stays
UNKNOWN instead of being inferred.

The purpose is operational memory: make active evidence populations, their
sample growth, review gates, and unresolved tracking gaps visible in one place.
"""

from __future__ import annotations

import csv
import json
import os
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

try:  # display-only setup names; a standalone copy keeps its raw lane ids
    from notifications.plain_english import SETUPS as SETUP_WORDS
except ImportError:  # pragma: no cover - standalone copy without the package
    SETUP_WORDS = {}


FORWARD_REVIEW_GATE = (
    "20 trading days + 30 resolved filled outcomes/variant; no automatic promotion"
)
OPTIONS_PROSPECTIVE_GATE = (
    ">=30 prospective independent episodes; >=3 sessions; both directions; "
    "opening-excluded first-sight excess >=5pp"
)

# These are the only unsupported options families currently pre-registered for
# prospective persistence testing.  Inside-bar break is passive accumulation,
# not a promotion test.
OPTIONS_FAMILIES: tuple[tuple[str, str, str], ...] = (
    ("2-1-2 reversal", "OBSERVING", OPTIONS_PROSPECTIVE_GATE),
    ("1-2-2", "OBSERVING", OPTIONS_PROSPECTIVE_GATE),
    ("inside-bar break", "PASSIVE", "passive accumulation only; no promotion gate"),
)

_TS_FIELDS = (
    "recorded_at", "timestamp", "ts", "signal_timestamp", "observed_at",
    "decision_ts", "entry_ts", "exit_ts", "resolved_at", "ran_at",
)
_DAY_FIELDS = ("session_date", "day", "observation_date", "trading_date", "date")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if isinstance(value, dict):
                rows.append(value)
    except OSError:
        return []
    return rows


def _parse_ts(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def _row_ts(row: dict[str, Any]) -> datetime | None:
    for field in _TS_FIELDS:
        parsed = _parse_ts(row.get(field))
        if parsed is not None:
            return parsed
    return None


def _row_day(row: dict[str, Any]) -> date | None:
    for field in _DAY_FIELDS:
        raw = row.get(field)
        if raw:
            try:
                return date.fromisoformat(str(raw)[:10])
            except ValueError:
                pass
    stamp = _row_ts(row)
    return stamp.date() if stamp else None


def _in_window(row: dict[str, Any], start: date, end: date) -> bool:
    day = _row_day(row)
    return bool(day and start <= day <= end)


def _last_timestamp(rows: Iterable[dict[str, Any]]) -> str | None:
    stamps = [stamp for row in rows if (stamp := _row_ts(row)) is not None]
    return max(stamps).isoformat() if stamps else None


def _event_name(row: dict[str, Any]) -> str:
    for field in ("event", "event_type", "record_type", "type", "status"):
        value = row.get(field)
        if value:
            return str(value)
    return "UNKNOWN"


def _num(value: Any) -> int | None:
    if value in (None, "", "unknown", "UNKNOWN"):
        return None
    try:
        return int(float(str(value).replace("%", "")))
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> float | None:
    if value in (None, "", "unknown", "UNKNOWN"):
        return None
    try:
        return float(str(value).replace("%", "").replace("pp", "").strip())
    except (TypeError, ValueError):
        return None


def _normal_family(value: Any) -> str:
    return str(value or "").lower().replace("strat_", "").replace("_", "-").strip()


def _load_family_summary(path: Path | None) -> dict[str, dict[str, Any]]:
    """Best-effort reader for the prospective-family summary artifact.

    Supports either JSON (``families`` list/dict or a top-level list) or the CSV
    emitted by the current scratchpad analyzer.  This function never invents a
    family result when the artifact is absent or has unfamiliar columns.
    """
    if path is None or not path.exists():
        return {}
    rows: list[dict[str, Any]] = []
    try:
        if path.suffix.lower() == ".json":
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, list):
                rows = [r for r in payload if isinstance(r, dict)]
            elif isinstance(payload, dict):
                families = payload.get("families", payload)
                if isinstance(families, list):
                    rows = [r for r in families if isinstance(r, dict)]
                elif isinstance(families, dict):
                    for name, value in families.items():
                        if isinstance(value, dict):
                            rows.append({"family": name, **value})
        else:
            with path.open(encoding="utf-8", newline="") as handle:
                rows = [dict(r) for r in csv.DictReader(handle)]
    except (OSError, ValueError, csv.Error):
        return {}

    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        family = row.get("family") or row.get("Family") or row.get("setup_family")
        if not family:
            continue
        out[_normal_family(family)] = row
    return out


def _summary_value(row: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in row and row[name] not in (None, ""):
            return row[name]
    return None


def _forward_entries(
    log_dir: Path,
    census: dict[str, Any],
    start: date,
    end: date,
) -> list[dict[str, Any]]:
    rows = _read_jsonl(log_dir / "forward_ab_2026_08_v1.jsonl")
    current_candidates: Counter[str] = Counter()
    sessions: defaultdict[str, set[str]] = defaultdict(set)
    for row in rows:
        if row.get("record_type") != "CANDIDATE":
            continue
        strategy = str(row.get("strategy") or "unknown")
        variant = str(row.get("variant") or "unknown")
        key = f"{strategy}/{variant}"
        if _in_window(row, start, end):
            current_candidates[key] += 1
            day = _row_day(row)
            if day:
                sessions[key].add(day.isoformat())

    configured = ((census.get("campaign_arms") or {}).get("configured") or {})
    entries: list[dict[str, Any]] = []
    for key, meta in sorted(configured.items()):
        total = _num(meta.get("count")) or 0
        window_n = current_candidates.get(key, 0)
        if total == 0:
            status = "NO_EVIDENCE_YET"
        elif window_n == 0:
            status = "QUIET_THIS_WEEK"
        else:
            status = "COLLECTING"
        entries.append({
            "system": "futures",
            "lane": f"forward_ab:{key}",
            "status": status,
            "evidence_n": total,
            "window_n": window_n,
            "sessions_window": len(sessions.get(key, set())),
            "epoch": "forward_ab_2026_08_v1",
            "last_success": meta.get("last"),
            "review_threshold": FORWARD_REVIEW_GATE,
            "threshold_status": "DEFINED",
            "note": "candidate-driven; a quiet week is not itself a collector failure",
        })
    return entries


def _hypothetical_entries(census: dict[str, Any], start: date, end: date) -> list[dict[str, Any]]:
    lanes = ((census.get("hypothetical_lanes") or {}).get("lanes") or {})
    entries: list[dict[str, Any]] = []
    for name, meta in sorted(lanes.items()):
        if not isinstance(meta, dict):
            continue
        last = _parse_ts(meta.get("state_last"))
        active_this_week = bool(last and start <= last.date() <= end)
        if not meta.get("exists"):
            status = "NOT_STARTED"
        elif meta.get("open_position"):
            status = "OPEN_PAPER_POSITION"
        elif active_this_week:
            status = "TRACKING"
        else:
            status = "QUIET_EVENT_DRIVEN"
        entries.append({
            "system": "futures",
            "lane": name,
            "status": status,
            "evidence_n": _num(meta.get("filled_count")),
            "window_n": None,
            "sessions_window": None,
            "epoch": meta.get("epoch"),
            "last_success": meta.get("state_last"),
            "review_threshold": "lane-specific review gate not encoded in registry metadata",
            "threshold_status": "MISSING_METADATA",
            "note": str(meta.get("heartbeat") or "event-driven lane"),
        })
    return entries


def _asia_entry(log_dir: Path, start: date, end: date) -> dict[str, Any]:
    path = log_dir / "asia_d_ema_cohort" / "evidence.jsonl"
    rows = _read_jsonl(path)
    window = [row for row in rows if _in_window(row, start, end)]
    days = {day.isoformat() for row in window if (day := _row_day(row))}
    events = Counter(_event_name(row) for row in window)
    campaign = next((row.get("campaign_id") for row in rows if row.get("campaign_id")), None)
    epoch = os.getenv("ASIA_D_EMA_PAPER_EPOCH_START") or next(
        (row.get("epoch") or row.get("epoch_start") for row in rows if row.get("epoch") or row.get("epoch_start")),
        None,
    )
    if not path.exists():
        status = "NOT_STARTED"
    elif window:
        status = "COLLECTING"
    else:
        status = "QUIET_THIS_WEEK"
    return {
        "system": "futures",
        "lane": "asia_d_ema",
        "status": status,
        "evidence_n": len(rows),
        "window_n": len(window),
        "sessions_window": len(days),
        "epoch": epoch or campaign,
        "last_success": _last_timestamp(rows),
        "review_threshold": "prospective review gate not encoded in repository metadata",
        "threshold_status": "MISSING_METADATA",
        "note": "event rows this week: " + (", ".join(f"{k}={v}" for k, v in sorted(events.items())) or "none"),
    }


def _session_22c_entry(log_dir: Path, start: date, end: date) -> dict[str, Any]:
    """Session-scoped 2-2 continuation paper lane (prereg H6/H7, 2026-09-21)."""
    path = log_dir / "session_22c_lane" / "evidence.jsonl"
    rows = _read_jsonl(path)
    window = [row for row in rows if _in_window(row, start, end)]
    days = {day.isoformat() for row in window if (day := _row_day(row))}
    events = Counter(f"{row.get('lane')}:{_event_name(row)}" for row in window)
    campaign = next((row.get("campaign_id") for row in rows if row.get("campaign_id")), None)
    epoch = os.getenv("SESSION_22C_PAPER_EPOCH_START")
    if not path.exists():
        status = "NOT_STARTED"
    elif window:
        status = "COLLECTING"
    else:
        status = "QUIET_THIS_WEEK"
    return {
        "system": "futures",
        "lane": "session_22c",
        "status": status,
        "evidence_n": len(rows),
        "window_n": len(window),
        "sessions_window": len(days),
        "epoch": epoch or campaign,
        "last_success": _last_timestamp(rows),
        "review_threshold": "H6: >=80 asia rows, PF>=1.10, win>=40%; H7: >=6 Sundays & >=30 rows, PF>=1.20, win>=55% (docs/prereg-mnq-volume-label-and-sunday-reopen-2026-09-21.md)",
        "threshold_status": "PREREGISTERED",
        "note": "event rows this week: " + (", ".join(f"{k}={v}" for k, v in sorted(events.items())) or "none"),
    }


def _coverage_entry(coverage_dir: Path, start: date, end: date) -> dict[str, Any]:
    rows = _read_jsonl(coverage_dir / "ledger.jsonl")
    done = [row for row in rows if row.get("status") == "DONE" and row.get("session_date")]
    window_done = [row for row in done if _in_window(row, start, end)]
    done_sessions = sorted({str(row.get("session_date")) for row in done})
    window_sessions = sorted({str(row.get("session_date")) for row in window_done})
    failed_window = [row for row in rows if row.get("status") == "FAILED" and _in_window(row, start, end)]
    repairs_window = [row for row in window_done if row.get("observer_repair")]

    latest_session_rows: dict[str, dict[str, Any]] = {}
    for row in rows:
        session = row.get("session_date")
        if session:
            latest_session_rows[str(session)] = row
    latest_session = max(latest_session_rows) if latest_session_rows else None
    latest_status = latest_session_rows.get(latest_session, {}).get("status") if latest_session else None

    if not rows:
        status = "NO_LEDGER"
    elif latest_status == "FAILED":
        status = "ATTENTION"
    elif repairs_window:
        status = "COLLECTING_WITH_REPAIR_PROVENANCE"
    else:
        status = "COLLECTING"
    return {
        "system": "options",
        "lane": "coverage_collector",
        "status": status,
        "evidence_n": len(done_sessions),
        "window_n": len(window_sessions),
        "sessions_window": len(window_sessions),
        "epoch": next((row.get("collector_version") for row in reversed(rows) if row.get("collector_version")), None),
        "last_success": _last_timestamp(done),
        "review_threshold": "automation proof requires a clean unattended DONE+AGGREGATED session with no repair/manual start",
        "threshold_status": "DEFINED",
        "note": f"latest session={latest_session or 'none'}; failed attempts this week={len(failed_window)}; repaired DONE this week={len(repairs_window)}",
    }


def _family_entries(summary_path: Path | None) -> list[dict[str, Any]]:
    summary = _load_family_summary(summary_path)
    entries: list[dict[str, Any]] = []
    for family, mode, gate in OPTIONS_FAMILIES:
        row = summary.get(_normal_family(family), {})
        n = _num(_summary_value(row, "episodes", "prospective_episodes", "Prospective episodes", "n"))
        sessions = _num(_summary_value(row, "sessions", "Sessions", "prospective_sessions"))
        longs = _num(_summary_value(row, "long", "LONG", "longs"))
        shorts = _num(_summary_value(row, "short", "SHORT", "shorts"))
        ex_open = _float(_summary_value(
            row,
            "ex_opening_fs_diff_pp", "ex_opening_first_sight_diff_pp",
            "Ex-opening FS diff", "Ex-opening first-sight difference",
        ))
        reported = _summary_value(row, "status", "Status")
        if not row:
            status = "SUMMARY_NOT_CENTRALIZED"
            note = "prospective family summary artifact not available to the EOW reporter"
        else:
            status = str(reported or mode)
            note = f"LONG={longs if longs is not None else '?'} SHORT={shorts if shorts is not None else '?'} ex-open FS diff={ex_open if ex_open is not None else '?'}pp"
        entries.append({
            "system": "options",
            "lane": family,
            "status": status,
            "evidence_n": n,
            "window_n": None,
            "sessions_window": sessions,
            "epoch": "prospective-only after 2026-09-15",
            "last_success": None,
            "review_threshold": gate,
            "threshold_status": "DEFINED" if family != "inside-bar break" else "PASSIVE",
            "note": note,
        })
    return entries


def build_registry(
    *,
    log_dir: Path,
    coverage_dir: Path,
    census: dict[str, Any],
    start: date,
    end: date,
    family_summary_path: Path | None = None,
) -> dict[str, Any]:
    """Build one read-only evidence registry for the report window."""
    entries: list[dict[str, Any]] = []
    entries.extend(_forward_entries(log_dir, census, start, end))
    entries.extend(_hypothetical_entries(census, start, end))
    entries.append(_asia_entry(log_dir, start, end))
    entries.append(_session_22c_entry(log_dir, start, end))
    entries.append(_coverage_entry(coverage_dir, start, end))
    entries.extend(_family_entries(family_summary_path))

    uncertainty = [
        {
            "system": item["system"],
            "lane": item["lane"],
            "reason": (
                "review threshold metadata missing"
                if item.get("threshold_status") == "MISSING_METADATA"
                else "prospective summary not centralized"
                if item.get("status") == "SUMMARY_NOT_CENTRALIZED"
                else "collector attention"
            ),
        }
        for item in entries
        if item.get("threshold_status") == "MISSING_METADATA"
        or item.get("status") in {"SUMMARY_NOT_CENTRALIZED", "ATTENTION"}
    ]
    return {
        "schema": "evidence_registry_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "entries": entries,
        "uncertainty": uncertainty,
        "read_only": True,
    }


# Display words only: the registry JSON keeps its codes and lane ids.
REGISTRY_HEADER = "What we're tracking:"
_STATUS_WORDS = {
    "COLLECTING": "collecting",
    "COLLECTING_WITH_REPAIR_PROVENANCE": "collecting (some data was repaired)",
    "QUIET_THIS_WEEK": "quiet this week",
    "NO_EVIDENCE_YET": "no data yet",
    "NOT_STARTED": "not started",
    "OPEN_PAPER_POSITION": "practice position open",
    "TRACKING": "tracking",
    "QUIET_EVENT_DRIVEN": "quiet (waits for its setup)",
    "NO_LEDGER": "no records file",
    "ATTENTION": "⚠ needs a look",
    "SUMMARY_NOT_CENTRALIZED": "summary not available",
}
_LANE_WORDS = {
    "asia_d_ema": "Asia session daily-trend lane",
    "session_22c": "session 2-2 continuation lane",
    "coverage_collector": "options coverage collector",
}


def _lane_words(lane: object) -> str:
    text = str(lane or "?")
    if text in _LANE_WORDS:
        return _LANE_WORDS[text]
    if text.startswith("forward_ab:"):
        setup, _, arm = text[len("forward_ab:"):].partition("/")
        name = SETUP_WORDS.get(setup, setup.replace("_", " "))
        return f"A/B test: {name}" + (f" ({arm.replace('_', ' ')})" if arm else "")
    return SETUP_WORDS.get(text, text.replace("_", " "))


def _status_words(status: object) -> str:
    text = str(status or "?")
    return _STATUS_WORDS.get(text) or text.replace("_", " ").lower()


def format_registry_lines(registry: dict[str, Any], *, system: str, max_entries: int = 12) -> list[str]:
    """Compact Discord-safe plain-English lines for one system (display only)."""
    entries = [row for row in registry.get("entries", []) if row.get("system") == system]
    if not entries:
        return ["Nothing being tracked yet"]
    lines = [REGISTRY_HEADER]
    for row in entries[:max_entries]:
        bits = [_status_words(row.get("status"))]
        if row.get("evidence_n") is not None:
            bits.append(f"{row['evidence_n']} so far")
        if row.get("window_n") is not None:
            bits.append(f"{row['window_n']} this week")
        if row.get("sessions_window") is not None:
            n = row["sessions_window"]
            bits.append(f"{n} trading day{'' if n == 1 else 's'}")
        lines.append(f"• {_lane_words(row.get('lane'))}: " + " · ".join(bits))
    if len(entries) > max_entries:
        lines.append(f"• +{len(entries) - max_entries} more (full list in the saved report file)")
    uncertain = [u for u in registry.get("uncertainty", []) if u.get("system") == system]
    if uncertain:
        lines.append(
            "Least sure about: " + "; ".join(
                f"{_lane_words(u['lane'])} ({u['reason']})" for u in uncertain[:5]
            )
        )
    return lines
