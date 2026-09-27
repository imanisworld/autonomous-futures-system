"""Narrow read-only surfaces for the weekly system check.

These functions read explicit evidence inputs and return count/timestamp facts.
They do not browse ``/root/afs-shared``, do not fetch market data, do not write,
and do not invoke a single-look or experiment run.

The 2-1-2 target-geometry dataset recovery is not part of this module.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from context.bar_history import _parse_dt

SURFACE_NAMES = (
    "futures-trigger-counts",
    "forward-campaign-counts",
    "options-reclaim-counts",
    "feed-15m-status",
)

_DATE = r"\d{4}-\d{2}-\d{2}"
_INST = r"[A-Z0-9]+"
_FILE_RULES = {
    "4hr_evidence": re.compile(rf"^tf1m/4hr_trigger_evidence_{_DATE}\.jsonl$"),
    "322_state": re.compile(rf"^tf1m/322_first_live/state_{_DATE}\.json$"),
    "322_evidence": re.compile(rf"^tf1m/322_first_live/evidence_{_DATE}\.jsonl$"),
    "bars_15m": re.compile(rf"^bars_{_INST}_{_DATE}\.jsonl$"),
    "bars_5m": re.compile(rf"^tf5m/bars_{_INST}_{_DATE}\.jsonl$"),
    "bars_1m": re.compile(rf"^tf1m/bars_{_INST}_{_DATE}\.jsonl$"),
}
_COUNT_BUCKETS = ("ARMED", "TRIGGER_TOUCH", "BLOCKED", "EXPIRED")
_EVENT_BUCKET = {
    "ARMED": "ARMED",
    "TRIGGER_TOUCH": "TRIGGER_TOUCH",
    "EXPIRED": "EXPIRED",
    "TRIGGER_BLOCKED": "BLOCKED",
    "ARM_BLOCKED": "BLOCKED",
}
_DEDUPE_EVENTS = frozenset({"TRIGGER_DUPLICATE"})
_KNOWN_EVENTS = frozenset(_EVENT_BUCKET) | _DEDUPE_EVENTS | frozenset({"SETUP_NOT_ARMED"})
_CAUSAL_REASONS = frozenset(
    {
        "COMPLETED_1H_STOP_MISSING",
        "ENTRY_BRACKET_INVALID_AT_TOUCH",
        "REFERENCE_DATA_INCOMPLETE",
        "CANONICAL_SETUP_NOT_ARMED",
        "NO_BREAK_BY_11AM",
    }
)
_PRICE_KEYS = frozenset(
    {
        "open",
        "high",
        "low",
        "close",
        "volume",
        "trigger",
        "stop",
        "target",
        "fill_reference",
        "paper_entry_1tick",
        "trigger_reference",
        "pnl",
        "result",
    }
)
_MNQ_KEYS = (
    "prereg",
    "mode",
    "as_of",
    "scoring_start",
    "pipeline_health",
    "cme_observation_days",
    "gap_days",
    "h1_counts",
    "h2_six_family_counts",
    "scored_fillable_events_by_family",
    "scored_signal_attempts_by_family",
    "fillable_events_before_scoring_start_excluded",
    "sample",
    "note",
)
_MGC_KEYS = (
    "prereg",
    "mode",
    "as_of",
    "scoring_start",
    "pipeline_healthy",
    "pipeline_error",
    "terminal_trades",
    "terminal_trades_by_setup",
    "void_gap_day_trades",
    "observation_days",
    "gaps",
    "sample",
    "note",
)


class SurfaceError(RuntimeError):
    """The requested surface cannot be produced without leaving its contract."""


def _empty_counts() -> dict[str, int]:
    return {name: 0 for name in _COUNT_BUCKETS}


def _reject_secret_path(path: Path) -> None:
    name = path.name
    if name == ".env" or name.startswith(".env."):
        raise SurfaceError("refusing to read an env file")


def _resolve_under(root: Path, relative: str) -> Path:
    if relative.startswith("/") or ".." in Path(relative).parts:
        raise SurfaceError(f"path escapes the log root: {relative}")
    root_resolved = root.resolve()
    candidate = (root_resolved / relative).resolve()
    try:
        candidate.relative_to(root_resolved)
    except ValueError as exc:
        raise SurfaceError(f"path escapes the log root: {relative}") from exc
    return candidate


def _matching_files(root: Path, kind: str) -> list[tuple[str, Path]]:
    rule = _FILE_RULES[kind]
    root_resolved = root.resolve()
    if not root_resolved.is_dir():
        raise SurfaceError(f"log root is not a directory: {root}")
    found: list[tuple[str, Path]] = []
    if kind == "4hr_evidence":
        base = root_resolved / "tf1m"
        paths = base.glob("4hr_trigger_evidence_*.jsonl") if base.is_dir() else []
    elif kind in {"322_state", "322_evidence"}:
        base = root_resolved / "tf1m" / "322_first_live"
        pattern = "state_*.json" if kind == "322_state" else "evidence_*.jsonl"
        paths = base.glob(pattern) if base.is_dir() else []
    elif kind == "bars_15m":
        paths = root_resolved.glob("bars_*.jsonl")
    elif kind == "bars_5m":
        base = root_resolved / "tf5m"
        paths = base.glob("bars_*.jsonl") if base.is_dir() else []
    elif kind == "bars_1m":
        base = root_resolved / "tf1m"
        paths = base.glob("bars_*.jsonl") if base.is_dir() else []
    else:
        raise SurfaceError(f"unknown file class {kind}")
    for path in paths:
        if not path.is_file():
            continue
        try:
            relative = path.resolve().relative_to(root_resolved).as_posix()
        except ValueError as exc:
            raise SurfaceError(f"path escapes the log root: {path.name}") from exc
        if not rule.fullmatch(relative):
            continue
        _reject_secret_path(path)
        found.append((relative, _resolve_under(root_resolved, relative)))
    found.sort(key=lambda item: item[0])
    return found


def _read_json_lines(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    text = path.read_text(encoding="utf-8")
    for lineno, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SurfaceError(f"unreadable jsonl {path.name}:{lineno}") from exc
        if not isinstance(row, dict):
            raise SurfaceError(f"jsonl row is not an object: {path.name}:{lineno}")
        rows.append(row)
    return rows


def _timestamp_of(row: Mapping[str, Any]) -> str | None:
    for key in ("bar_ts", "decision_time", "observer_boundary_ts", "trading_date"):
        value = row.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _date_of(value: str | None) -> str | None:
    if not value:
        return None
    parsed = _parse_dt(value)
    if parsed is not None:
        return parsed.date().isoformat()
    if re.fullmatch(_DATE, value):
        return value
    return None


def _consume_event(row: Mapping[str, Any], bucket: Counter, dedupe: Counter, causal: Counter) -> None:
    event = str(row.get("event") or "")
    if event not in _KNOWN_EVENTS:
        raise SurfaceError(f"unrecognized trigger event {event!r}")
    if event in _DEDUPE_EVENTS:
        dedupe["duplicate_events"] += 1
    else:
        mapped = _EVENT_BUCKET.get(event)
        if mapped:
            bucket[mapped] += 1
        elif event == "SETUP_NOT_ARMED":
            causal["setup_not_armed"] += 1
    reason = row.get("reason")
    if isinstance(reason, str) and reason:
        if reason not in _CAUSAL_REASONS:
            raise SurfaceError(f"unrecognized block reason {reason!r}")
        causal[f"reason:{reason}"] += 1
    source = row.get("source_state")
    if event == "TRIGGER_TOUCH" and isinstance(source, dict):
        if source.get("status") != "ARMED":
            causal["touch_source_not_armed"] += 1


def futures_trigger_counts(log_dir: Path) -> dict[str, Any]:
    """Count 4HR and 3-2-2 observer events. Prices and brackets are dropped."""
    root = Path(log_dir)
    lanes = {
        "4hr": _matching_files(root, "4hr_evidence"),
        "322_evidence": _matching_files(root, "322_evidence"),
        "322_state": _matching_files(root, "322_state"),
    }
    counts = _empty_counts()
    by_lane = {
        "4hr": _empty_counts(),
        "322_first_live": _empty_counts(),
    }
    dedupe_events = 0
    arm_counts: Counter[str] = Counter()
    causal: Counter[str] = Counter()
    dates: set[str] = set()
    latest: str | None = None
    files_read: list[str] = []
    state_status: dict[str, str] = {}

    def _note_time(value: str | None) -> None:
        nonlocal latest
        if not value:
            return
        day = _date_of(value)
        if day:
            dates.add(day)
        if latest is None or value > latest:
            latest = value

    for label, files in (("4hr", lanes["4hr"]), ("322_first_live", lanes["322_evidence"])):
        for relative, path in files:
            files_read.append(relative)
            bucket: Counter[str] = Counter()
            dedupe: Counter[str] = Counter()
            for row in _read_json_lines(path):
                _consume_event(row, bucket, dedupe, causal)
                _note_time(_timestamp_of(row))
                arm_key = row.get("arm_key")
                if row.get("event") == "TRIGGER_TOUCH" and isinstance(arm_key, str) and arm_key:
                    arm_counts[arm_key] += 1
            dedupe_events += int(dedupe["duplicate_events"])
            for name in _COUNT_BUCKETS:
                by_lane[label][name] += int(bucket[name])
                counts[name] += int(bucket[name])

    allowed_state = {"ARMED", "EXPIRED", "INVALIDATED", "BLOCKED"}
    for relative, path in lanes["322_state"]:
        files_read.append(relative)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise SurfaceError(f"unreadable state file {path.name}") from exc
        if not isinstance(payload, dict):
            raise SurfaceError(f"state file is not an object: {path.name}")
        status = payload.get("status")
        if not isinstance(status, str) or status not in allowed_state:
            raise SurfaceError(f"unrecognized state status in {path.name}")
        day = _date_of(str(payload.get("trading_date") or ""))
        if day is None:
            raise SurfaceError(f"state file missing trading_date: {path.name}")
        dates.add(day)
        state_status[day] = status
        _note_time(payload.get("observer_boundary_ts") if isinstance(payload.get("observer_boundary_ts"), str) else day)
        invalidation = payload.get("invalidation")
        if isinstance(invalidation, str) and invalidation:
            if invalidation not in _CAUSAL_REASONS:
                raise SurfaceError(f"unrecognized state invalidation {invalidation!r}")
            causal[f"state:{invalidation}"] += 1

    duplicate_arm_keys = sum(1 for count in arm_counts.values() if count > 1)
    report = {
        "surface": "futures-trigger-counts",
        "files_read": files_read,
        "dates": sorted(dates),
        "latest_timestamp": latest,
        "counts": counts,
        "by_lane": by_lane,
        "latest_322_state_by_date": dict(sorted(state_status.items())),
        "dedupe": {
            "duplicate_events": dedupe_events,
            "duplicate_arm_keys": duplicate_arm_keys,
        },
        "causality": {
            "flags": dict(sorted(causal.items())),
        },
    }
    _assert_no_price_keys(report)
    return report


def _assert_no_price_keys(obj: Any, path: str = "") -> None:
    if isinstance(obj, dict):
        for key, value in obj.items():
            if str(key).lower() in _PRICE_KEYS:
                raise SurfaceError(f"price field escaped the surface at {path}/{key}")
            _assert_no_price_keys(value, f"{path}/{key}")
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            _assert_no_price_keys(value, f"{path}[{index}]")


def _project(report: Mapping[str, Any], keys: Iterable[str]) -> dict[str, Any]:
    return {key: report[key] for key in keys if key in report}


def project_mnq_counts(report: Mapping[str, Any]) -> dict[str, Any]:
    from research.prereg929_forward_portfolio import assert_blind

    assert_blind(report)
    if report.get("mode") != "counts":
        raise SurfaceError("MNQ surface accepts counts mode only")
    projected = _project(report, _MNQ_KEYS)
    assert_blind(projected)
    return projected


def project_mgc_counts(report: Mapping[str, Any]) -> dict[str, Any]:
    from research.mgc_4h_wide_forward import assert_blind

    assert_blind(report)
    if report.get("mode") != "counts":
        raise SurfaceError("MGC surface accepts counts mode only")
    projected = _project(report, _MGC_KEYS)
    assert_blind(projected)
    return projected


def load_mgc_counts(path: Path) -> dict[str, Any]:
    """Read an already-produced blind counts file. Does not fetch bars."""
    source = Path(path)
    _reject_secret_path(source)
    if not source.is_file():
        raise SurfaceError(f"MGC counts file missing: {source}")
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SurfaceError("MGC counts file is not JSON") from exc
    if not isinstance(payload, dict):
        raise SurfaceError("MGC counts file is not an object")
    return project_mgc_counts(payload)


def load_mnq_counts(corpus_5m: Path, corpus_15m: Path, *, as_of: datetime | None = None) -> dict[str, Any]:
    """Run the existing local-corpus counts path. Does not call look."""
    import tempfile

    from research import prereg929_forward_portfolio as fp
    from scripts.prereg929_forward_portfolio import _collect

    as_of = as_of or datetime.now(timezone.utc)
    with tempfile.TemporaryDirectory(prefix="afs-weekly-mnq-") as tmp:
        run, cme, _window = _collect(Path(corpus_5m), Path(corpus_15m), as_of, Path(tmp))
        report = fp.counts_report(run, cme, as_of=as_of)
    return project_mnq_counts(report)


def forward_campaign_counts(
    *,
    mnq_corpus_5m: Path | None = None,
    mnq_corpus_15m: Path | None = None,
    mgc_counts: Path | None = None,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    """Blind counts for the two forward campaigns. No fetch and no look."""
    out: dict[str, Any] = {"surface": "forward-campaign-counts"}
    if mnq_corpus_5m and mnq_corpus_15m:
        out["mnq_shared_account"] = load_mnq_counts(mnq_corpus_5m, mnq_corpus_15m, as_of=as_of)
    else:
        out["mnq_shared_account"] = {
            "status": "BLOCKED",
            "reason": "local_corpus_required",
        }
    if mgc_counts:
        out["mgc_4h_forward"] = load_mgc_counts(mgc_counts)
    else:
        out["mgc_4h_forward"] = {
            "status": "BLOCKED",
            "reason": "local_counts_required_refusing_fetch",
        }
    return out


def options_reclaim_counts(db_path: Path, *, as_of: datetime | None = None) -> dict[str, Any]:
    """Existing reclaim counts mode only. Never calls look."""
    from research import options_reclaim_entry as oe

    source_path = Path(db_path)
    _reject_secret_path(source_path)
    if not source_path.is_file():
        raise SurfaceError(f"options snapshot missing: {source_path}")
    now = as_of or datetime.now(timezone.utc)
    conn = oe.connect_readonly(source_path)
    try:
        episodes = oe.load_episodes(conn)
        reproduction = oe.reproduction(episodes, oe.contract_symbols_by_row(conn))
        if reproduction["verdict"] != "PASS":
            return {
                "surface": "options-reclaim-counts",
                "paired_episodes": None,
                "trading_days": None,
                "reclaim_entries": None,
                "no_entry": None,
                "lineage_blocks": {
                    "reproduction_verdict": reproduction["verdict"],
                    "matched": reproduction["matched"],
                    "total": reproduction["total"],
                },
                "gate": None,
                "gate_status": "LINEAGE_BLOCKED",
            }
        pairs = oe.evaluate(episodes)
        report = oe.counts_report(
            pairs,
            as_of=now,
            source={"db": source_path.name},
        )
    finally:
        conn.close()
    projected = {
        "surface": "options-reclaim-counts",
        "paired_episodes": report["eligible_scorable_pairs"],
        "trading_days": report["distinct_trading_days_scorable"],
        "reclaim_entries": report["reclaim_entries_scorable"],
        "no_entry": report["reclaim_no_entry_scorable"],
        "lineage_blocks": {
            "reproduction_verdict": "PASS",
            "ineligible_by_reason": report["ineligible_by_reason"],
            "blocked_by_reason": report["blocked_by_reason"],
        },
        "gate": report["gate"],
        "gate_status": report["status"],
    }
    oe.assert_blind(projected)
    return projected


def _bar_identity(relative: str) -> tuple[str, str]:
    name = Path(relative).name
    match = re.fullmatch(rf"bars_({_INST})_{_DATE}\.jsonl", name)
    if not match:
        raise SurfaceError(f"bar filename is outside the feed contract: {relative}")
    return match.group(1), relative


def _timestamps(path: Path) -> list[tuple[datetime, str | None]]:
    found: list[tuple[datetime, str | None]] = []
    for row in _read_json_lines(path):
        raw = row.get("ts")
        if not isinstance(raw, str):
            raise SurfaceError(f"bar row missing ts in {path.name}")
        parsed = _parse_dt(raw)
        if parsed is None:
            raise SurfaceError(f"bar timestamp unreadable in {path.name}")
        source = row.get("source")
        producer = source if isinstance(source, str) and source else None
        found.append((parsed, producer))
    return found


def _first_missing(stamps: list[datetime], minutes: int) -> str | None:
    ordered = sorted(set(stamps))
    step = timedelta(minutes=minutes)
    tolerance = timedelta(seconds=1)
    for prev, nxt in zip(ordered, ordered[1:]):
        if nxt - prev > step + tolerance:
            return (prev + step).astimezone(timezone.utc).isoformat()
    return None


def feed_15m_status(log_dir: Path) -> dict[str, Any]:
    """Last bar timestamps and the first missing 15m slot. No backfill."""
    root = Path(log_dir)
    classes = (
        ("15m", "bars_15m", 15),
        ("5m", "bars_5m", 5),
        ("1m", "bars_1m", 1),
    )
    instruments: dict[str, dict[str, Any]] = {}
    files_read: list[str] = []
    for label, kind, _minutes in classes:
        for relative, path in _matching_files(root, kind):
            files_read.append(relative)
            instrument, _ = _bar_identity(relative)
            slot = instruments.setdefault(
                instrument,
                {
                    "last_15m": None,
                    "last_5m": None,
                    "last_1m": None,
                    "first_missing_15m": None,
                    "producer": None,
                },
            )
            stamps = _timestamps(path)
            if not stamps:
                continue
            latest_dt = max(dt for dt, _producer in stamps)
            key = f"last_{label}"
            iso = latest_dt.astimezone(timezone.utc).isoformat()
            if slot[key] is None or iso > slot[key]:
                slot[key] = iso
            if label == "15m":
                sourced = [item for item in stamps if item[1]]
                if sourced:
                    slot["producer"] = max(sourced, key=lambda item: item[0])[1]
            if label == "15m":
                slot.setdefault("_15m_stamps", []).extend(dt for dt, _producer in stamps)
    for slot in instruments.values():
        stamps = slot.pop("_15m_stamps", [])
        slot["first_missing_15m"] = _first_missing(stamps, 15)
    return {
        "surface": "feed-15m-status",
        "files_read": files_read,
        "instruments": dict(sorted(instruments.items())),
    }
