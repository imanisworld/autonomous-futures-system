"""Read-only evidence-quality gate for cross_instrument_observation_v1.

Raw observation rows remain immutable and visible. This module only determines
whether a row is clean enough to count toward validation/review thresholds.
No result from this module grants strategy, risk, broker, or execution
eligibility.

Quality rules are intentionally conservative:
- missing expected 15m bars contaminate the sample;
- unknown detector/code provenance blocks eligibility;
- a contract change inside the detector/outcome window is roll-contaminated;
- continuous MGC/MCL/MBT provenance remains unknown until a proven roll schedule
  exists;
- MBT structural populations remain population-level HOLD until a 24/7 outcome
  horizon is explicitly proven.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional

from config.futures_contracts import contract_root
from context.bar_history import BarHistory, _parse_dt
from context.futures_session import product_session_active
from sources.polygon_client import PolygonError, QUARTERLY_SYMBOLS, front_contract

QUALITY_VERSION = "cross_instrument_evidence_quality_v1"
VALID = "VALID"
DATA_GAP_CONTAMINATED = "DATA_GAP_CONTAMINATED"
ROLL_CONTAMINATED = "ROLL_CONTAMINATED"
ROLL_PROVENANCE_UNKNOWN = "ROLL_PROVENANCE_UNKNOWN"
CODE_PROVENANCE_UNKNOWN = "CODE_PROVENANCE_UNKNOWN"
DETECTOR_PROVENANCE_UNKNOWN = "DETECTOR_PROVENANCE_UNKNOWN"
TIMEFRAME_PROVENANCE_UNKNOWN = "TIMEFRAME_PROVENANCE_UNKNOWN"
CALENDAR_PROVENANCE_UNKNOWN = "CALENDAR_PROVENANCE_UNKNOWN"
MBT_OUTCOME_HORIZON_UNPROVEN = "MBT_OUTCOME_HORIZON_UNPROVEN"

_MONTH_YEAR = re.compile(r"^[FGHJKMNQUVXZ]\d{1,4}$")
_SHA = re.compile(r"^[0-9a-fA-F]{7,40}$")
_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "cross_instrument_observation.json"
_RECONSTRUCTED_DEPENDENCY_BARS = 8


def _dt(value: object) -> Optional[datetime]:
    if isinstance(value, datetime):
        out = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return out.astimezone(timezone.utc)
    return _parse_dt(str(value or ""))


def _iso_minute(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(second=0, microsecond=0).isoformat()


def _timeframe_minutes(value: object) -> Optional[int]:
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


def _bars_between(
    log_dir: str | Path,
    instrument: str,
    start: datetime,
    end: datetime,
) -> list[dict]:
    if end < start:
        return []
    days = max(2, (end.date() - start.date()).days + 2)
    history = BarHistory(log_dir=str(log_dir)).recent(
        instrument,
        10000,
        for_date=end.date(),
        lookback_days=days,
    )
    out: list[dict] = []
    for bar in history:
        if _timeframe_minutes(bar.get("timeframe")) != 15:
            continue
        ts = _dt(bar.get("ts"))
        if ts is not None and start <= ts <= end:
            out.append(bar)
    out.sort(key=lambda row: _dt(row.get("ts")) or datetime.min.replace(tzinfo=timezone.utc))
    return out


def _detector_id(strategy: object) -> Optional[str]:
    try:
        config = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    for population in config.get("populations") or []:
        if population.get("strategy") == strategy:
            value = str(population.get("detector") or "").strip()
            return value or None
    return None


def _detector_dependencies(row: dict, log_dir: str | Path) -> list[datetime]:
    explicit = [
        dt for value in list(row.get("detector_window_timestamps") or [])
        if (dt := _dt(value)) is not None
    ]
    if explicit:
        return explicit
    signal = _dt(row.get("signal_timestamp"))
    instrument = str(row.get("instrument") or "")
    if signal is None or not instrument:
        return []
    # The live observation transport gives generic structural detectors at most
    # the latest eight 15m bars. Reconstruct that same conservative dependency
    # window from BarHistory. Samples observed before a full eight-bar window is
    # available remain detector-provenance blocked rather than receiving credit
    # from an unknowably truncated startup window.
    history = BarHistory(log_dir=str(log_dir)).recent(
        instrument,
        64,
        for_date=signal.date(),
        lookback_days=2,
    )
    bars = []
    for bar in history:
        ts = _dt(bar.get("ts"))
        if (
            ts is not None
            and ts <= signal
            and _timeframe_minutes(bar.get("timeframe")) == 15
        ):
            bars.append(ts)
    return bars[-_RECONSTRUCTED_DEPENDENCY_BARS:]


def _expected_15m(
    instrument: str,
    start: datetime,
    end: datetime,
) -> tuple[list[str], bool]:
    """Expected 15m bar opens, excluding known product maintenance/weekly halts.

    Holiday/early-close exceptions are deliberately not guessed. If the product
    calendar is unknown, calendar_known=False and the sample cannot qualify.
    Extra false-positive gap contamination is acceptable; false clean evidence is
    not.
    """
    cursor = start.astimezone(timezone.utc).replace(second=0, microsecond=0)
    finish = end.astimezone(timezone.utc).replace(second=0, microsecond=0)
    expected: list[str] = []
    calendar_known = True
    while cursor <= finish:
        active = product_session_active(instrument, cursor)
        if active is None:
            calendar_known = False
        elif active:
            expected.append(_iso_minute(cursor))
        cursor += timedelta(minutes=15)
    return expected, calendar_known


def continuity_assessment(row: dict, log_dir: str | Path) -> dict:
    dependency_dts = _detector_dependencies(row, log_dir)
    signal = _dt(row.get("signal_timestamp"))
    if signal is not None:
        dependency_dts.append(signal)
    end = _dt(row.get("exit_timestamp")) or signal
    if not dependency_dts or end is None:
        return {
            "known": False,
            "missing_expected_bars": [],
            "expected_bar_count": 0,
            "observed_bar_count": 0,
            "window_start": None,
            "window_end": end.isoformat() if end else None,
        }
    start = min(dependency_dts)
    instrument = str(row.get("instrument") or "")
    bars = _bars_between(log_dir, instrument, start, end)
    expected, calendar_known = _expected_15m(instrument, start, end)
    observed = {
        _iso_minute(ts)
        for bar in bars
        if (ts := _dt(bar.get("ts"))) is not None
    }
    missing = sorted(set(expected) - observed)
    return {
        "known": calendar_known,
        "missing_expected_bars": missing,
        "expected_bar_count": len(expected),
        "observed_bar_count": len(observed),
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
    }


def _clean_symbol(value: object) -> str:
    return str(value or "").split(":")[-1].upper().strip()


def _symbol_kind(symbol: str, root: str) -> str:
    if not symbol:
        return "unknown"
    if symbol.endswith("1!") or symbol.endswith("!"):
        return "continuous"
    if symbol == root:
        return "root_only"
    if symbol.startswith(root) and _MONTH_YEAR.match(symbol[len(root):]):
        return "dated_contract"
    return "unknown"


def _date_range(start: date, end: date) -> Iterable[date]:
    cursor = start
    while cursor <= end:
        yield cursor
        cursor += timedelta(days=1)


def _quarterly_roll_window(root: str, start: date, end: date) -> bool:
    """Conservatively mark any date adjacent to the repo's proven roll switch."""
    if root not in QUARTERLY_SYMBOLS:
        return False
    try:
        for d in _date_range(start, end):
            before = front_contract(root, d - timedelta(days=1))
            current = front_contract(root, d)
            after = front_contract(root, d + timedelta(days=1))
            if before != current or current != after:
                return True
    except PolygonError:
        return True
    return False


def roll_assessment(row: dict, log_dir: str | Path) -> dict:
    instrument = str(row.get("instrument") or "")
    signal = _dt(row.get("signal_timestamp"))
    end = _dt(row.get("exit_timestamp")) or signal
    deps = _detector_dependencies(row, log_dir)
    if signal is not None:
        deps.append(signal)
    if not deps or end is None:
        return {"status": ROLL_PROVENANCE_UNKNOWN, "source_tickers": [], "symbol_kinds": []}
    start = min(deps)
    bars = _bars_between(log_dir, instrument, start, end)
    symbols: list[str] = []
    source_from_row = _clean_symbol(row.get("source_ticker"))
    if source_from_row:
        symbols.append(source_from_row)
    for bar in bars:
        source = _clean_symbol(bar.get("source_ticker"))
        if source:
            symbols.append(source)
        else:
            return {"status": ROLL_PROVENANCE_UNKNOWN, "source_tickers": sorted(set(symbols)), "symbol_kinds": []}
    unique = sorted(set(symbols))
    if not unique:
        return {"status": ROLL_PROVENANCE_UNKNOWN, "source_tickers": [], "symbol_kinds": []}
    roots = {contract_root(symbol) for symbol in unique}
    if roots != {instrument}:
        return {"status": ROLL_PROVENANCE_UNKNOWN, "source_tickers": unique, "symbol_kinds": []}
    kinds = sorted({_symbol_kind(symbol, instrument) for symbol in unique})
    if "unknown" in kinds or "root_only" in kinds:
        return {"status": ROLL_PROVENANCE_UNKNOWN, "source_tickers": unique, "symbol_kinds": kinds}
    if len(unique) > 1:
        return {"status": ROLL_CONTAMINATED, "source_tickers": unique, "symbol_kinds": kinds}
    kind = kinds[0]
    if kind == "dated_contract":
        return {"status": VALID, "source_tickers": unique, "symbol_kinds": kinds}
    if kind == "continuous":
        if instrument not in QUARTERLY_SYMBOLS:
            return {"status": ROLL_PROVENANCE_UNKNOWN, "source_tickers": unique, "symbol_kinds": kinds}
        contaminated = _quarterly_roll_window(instrument, start.date(), end.date())
        return {
            "status": ROLL_CONTAMINATED if contaminated else VALID,
            "source_tickers": unique,
            "symbol_kinds": kinds,
            "quarterly_schedule_checked": True,
        }
    return {"status": ROLL_PROVENANCE_UNKNOWN, "source_tickers": unique, "symbol_kinds": kinds}


def assess_evidence_row(row: dict, log_dir: str | Path) -> dict:
    """Return immutable/read-only quality classification for one evidence row."""
    issues: list[str] = []
    if _timeframe_minutes(row.get("source_timeframe")) != 15:
        issues.append(TIMEFRAME_PROVENANCE_UNKNOWN)
    sha = str(row.get("generating_git_sha") or "").strip()
    if not _SHA.match(sha) or str(row.get("provenance_status") or "") == "unknown":
        issues.append(CODE_PROVENANCE_UNKNOWN)
    detector = str(row.get("detector_id") or _detector_id(row.get("strategy")) or "").strip()
    explicit_dependency_values = list(row.get("detector_window_timestamps") or [])
    detector_dependencies = _detector_dependencies(row, log_dir)
    if (
        not detector
        or not detector_dependencies
        or (
            not explicit_dependency_values
            and len(detector_dependencies) < _RECONSTRUCTED_DEPENDENCY_BARS
        )
    ):
        issues.append(DETECTOR_PROVENANCE_UNKNOWN)

    continuity = continuity_assessment(row, log_dir)
    if not continuity["known"]:
        issues.append(CALENDAR_PROVENANCE_UNKNOWN)
    if continuity["missing_expected_bars"]:
        issues.append(DATA_GAP_CONTAMINATED)

    roll = roll_assessment(row, log_dir)
    if roll["status"] in {ROLL_CONTAMINATED, ROLL_PROVENANCE_UNKNOWN}:
        issues.append(roll["status"])

    issues = list(dict.fromkeys(issues))
    return {
        "quality_version": QUALITY_VERSION,
        "status": VALID if not issues else issues[0],
        "eligible": not issues,
        "issues": issues,
        "continuity": continuity,
        "roll": roll,
        "code_provenance": {
            "generating_git_sha": sha or None,
            "provenance_status": row.get("provenance_status"),
            "detector_id": detector or None,
            "detector_dependency_count": len(detector_dependencies),
            "detector_dependencies_explicit": bool(explicit_dependency_values),
        },
    }


def population_quality_blockers(population: dict) -> list[str]:
    """Population-level issues that cannot be solved by one otherwise-clean row."""
    if (
        population.get("instrument") == "MBT"
        and population.get("collection_mode") == "structural_outcome"
    ):
        return [MBT_OUTCOME_HORIZON_UNPROVEN]
    return []


def summarize_assessments(assessments: Iterable[dict]) -> dict:
    rows = list(assessments)
    issues = Counter(issue for row in rows for issue in row.get("issues") or [])
    return {
        "assessed": len(rows),
        "eligible": sum(1 for row in rows if row.get("eligible")),
        "blocked": sum(1 for row in rows if not row.get("eligible")),
        "issue_counts": dict(sorted(issues.items())),
    }


def build_quality_report(log_dir: str | Path, *, epoch: Optional[str] = None) -> dict:
    """Wrap the raw campaign report with the authoritative quality readiness gate.

    Raw counts remain visible. READY FOR REVIEW is based only on quality-eligible
    WIN/LOSS rows and is impossible while a population-level blocker exists.
    """
    from execution import cross_instrument_observation as cio

    report = cio.build_report(log_dir, epoch=epoch)
    rows, _ = cio._dedupe_rows(cio.read_evidence(log_dir))
    gate = report["review_gate"]
    for population in report["populations"]:
        key = (
            population["strategy"],
            population["instrument"],
            population["variant"],
            population["evidence_epoch"],
        )
        terminal = []
        for row in rows:
            try:
                row_key = cio.population_key(row)
            except cio.ObservationError:
                continue
            if row_key == key and row.get("record_type") == "OUTCOME" and row.get("result") in cio.TERMINAL_RESULTS:
                terminal.append(row)
        assessments = [(row, assess_evidence_row(row, log_dir)) for row in terminal]
        eligible_rows = [row for row, quality in assessments if quality["eligible"]]
        eligible_days = {cio._day(row) for row in eligible_rows if cio._day(row)}
        issue_counts = Counter(
            issue
            for _, quality in assessments
            for issue in quality.get("issues") or []
        )
        blockers = population_quality_blockers(population)
        raw_status = population["status"]
        ready = (
            not blockers
            and len(eligible_rows) >= gate["per_population_minimum_terminal_outcomes"]
            and len(eligible_days) >= gate["per_population_minimum_distinct_days"]
        )
        if population["evidence_epoch"] is None:
            quality_status = "NOT ARMED"
        elif ready:
            quality_status = "READY FOR REVIEW"
        elif (
            blockers
            or (
                len(terminal) >= gate["per_population_minimum_terminal_outcomes"]
                and len(eligible_rows) < gate["per_population_minimum_terminal_outcomes"]
            )
        ):
            quality_status = "QUALITY BLOCKED"
        elif terminal:
            quality_status = "INSUFFICIENT SAMPLE"
        elif population["candidates"] or population["signals"]:
            quality_status = "COLLECTING"
        else:
            quality_status = "NOT COLLECTING"
        quality_r = [row.get("pnl_r") for row in eligible_rows if isinstance(row.get("pnl_r"), (int, float))]
        population.update({
            "raw_status": raw_status,
            "status": quality_status,
            "quality_gate_authoritative": True,
            "quality_version": QUALITY_VERSION,
            "quality_eligible_terminal_outcomes": len(eligible_rows),
            "quality_blocked_terminal_outcomes": len(terminal) - len(eligible_rows),
            "quality_distinct_terminal_days": len(eligible_days),
            "quality_issue_counts": dict(sorted(issue_counts.items())),
            "quality_population_blockers": blockers,
            "quality_net_r": round(sum(float(v) for v in quality_r), 4) if quality_r else None,
        })
    report.update({
        "quality_gate_authoritative": True,
        "quality_version": QUALITY_VERSION,
        "raw_campaign_status_is_informational": True,
        "quality_rule": "only quality-eligible terminal outcomes may satisfy the review gate",
    })
    return report
