"""Equity corpus v1 — resumable batch-runner preflight. READ-ONLY, NO NETWORK.

Gate #13.3 / #13.4 of docs/equity-setup-corpus-preregistration-v1.md: verify the
batch runner's request plan, restart/idempotency behaviour, and coverage
validation BEFORE any full corpus fetch is authorized.

This module performs **no network I/O whatsoever**. It deliberately imports no
HTTP client and requires no API key. Everything it checks is derived from:

  - the frozen universe JSON (SHA-256 pinned),
  - the frozen source watchlist CSV (SHA-256 pinned),
  - locally present 5-minute bar files, if any.

It does not fetch the corpus, does not alter the frozen universe/window/rules,
and imports nothing from the production trading stack.

What it produces
----------------
1. A deterministic, canonically serialized logical-request manifest covering all
   156 universe entries.
2. Stable request IDs derived from (symbol, endpoint, adjustment, timeframe,
   window slice, corpus version).
3. Atomically written per-symbol checkpoints carrying provenance hashes.
4. Resume that accepts ONLY checkpoints whose manifest / universe / code /
   window / config hashes all match the current run.
5. Session and bar-coverage validation: duplicates, ordering, window bounds,
   session tags, DST, 5-minute alignment, and missing-session coverage.
6. A machine-readable, fail-closed completeness report.

Fail-closed means exactly that: there is no "complete with warnings" state. The
process exits non-zero unless every required symbol passes every gate.

Deliberate duplication
----------------------
`session_tag` / bar derivation logic is reimplemented here rather than imported
from `research/equity_corpus_smoke.py`, because that module imports `httpx` at
module scope and this one must stay import-clean of any network library. The
duplication is held honest by a parity test
(`tests/test_equity_corpus_batch_preflight.py`) that asserts the two session
taggers agree across the whole frozen window.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------------------
# Frozen constants — mirrors of the preregistration. NONE of these may be
# changed here; a change requires a versioned universe revision (equity_corpus_v2).
# ---------------------------------------------------------------------------

CORPUS_VERSION = "equity_corpus_v1"

REPO_ROOT = Path(__file__).resolve().parent.parent
UNIVERSE_PATH = REPO_ROOT / "research" / "universe" / "equity_corpus_v1_universe.json"
SOURCE_CSV_PATH = REPO_ROOT / "docs" / "options_watchlist_150.csv"

UNIVERSE_SHA256 = "327c7dcd795acc9a11d0b14c6030f0a03e14960245e3ef8740f6bedde9b90a67"
SOURCE_CSV_SHA256 = "2770c80b9d6b745b481245b457957e45275ad3590d6aa5cdfc6f7ca4761f1d4d"

EXPECTED_TOTAL_ENTRIES = 156
EXPECTED_SETUP_CANDIDATES = 155
EXPECTED_COHORT_COUNTS = {
    "single_name": 132,
    "etf": 17,
    "index": 1,
    "leveraged_inverse": 6,
}

TIMEZONE = "America/New_York"
ET = ZoneInfo(TIMEZONE)
WINDOW_START = date(2024, 7, 31)
WINDOW_END = date(2026, 7, 30)

ENDPOINT = "/v2/aggs/ticker/{ticker}/range/5/minute/{from}/{to}"
ADJUSTMENT = "adjusted=true"
TIMEFRAME = "5m"
BAR_INTERVAL_MINUTES = 5
BAR_INTERVAL_MS = BAR_INTERVAL_MINUTES * 60 * 1000

# Provider page ceiling. Slices are chosen so one logical request is expected to
# map to exactly one physical page — no pagination ambiguity in the plan.
PROVIDER_PAGE_LIMIT = 50_000

SESSION_TAGS = ("PREMARKET", "RTH", "AFTER_HOURS")
PREMARKET_OPEN_MIN = 4 * 60          # 04:00 ET
RTH_OPEN_MIN = 9 * 60 + 30           # 09:30 ET
RTH_CLOSE_MIN = 16 * 60              # 16:00 ET
EXT_CLOSE_MIN = 20 * 60              # 20:00 ET

EARLY_RTH_CLOSE_MIN = 13 * 60        # 13:00 ET on half days
EARLY_EXT_CLOSE_MIN = 17 * 60        # 17:00 ET on half days

# Frozen NYSE full-closure calendar for the window. Any real closure absent from
# this list surfaces as an EXTRA_SESSION finding rather than passing silently.
MARKET_HOLIDAYS: frozenset[date] = frozenset(
    date.fromisoformat(d)
    for d in (
        "2024-09-02",  # Labor Day
        "2024-11-28",  # Thanksgiving
        "2024-12-25",  # Christmas
        "2025-01-01",  # New Year's Day
        "2025-01-09",  # National day of mourning (President Carter)
        "2025-01-20",  # MLK Jr. Day
        "2025-02-17",  # Presidents' Day
        "2025-04-18",  # Good Friday
        "2025-05-26",  # Memorial Day
        "2025-06-19",  # Juneteenth
        "2025-07-04",  # Independence Day
        "2025-09-01",  # Labor Day
        "2025-11-27",  # Thanksgiving
        "2025-12-25",  # Christmas
        "2026-01-01",  # New Year's Day
        "2026-01-19",  # MLK Jr. Day
        "2026-02-16",  # Presidents' Day
        "2026-04-03",  # Good Friday
        "2026-05-25",  # Memorial Day
        "2026-06-19",  # Juneteenth
        "2026-07-03",  # Independence Day (observed; Jul 4 falls on Saturday)
    )
)

# Half sessions: RTH closes 13:00 ET, extended trading closes 17:00 ET.
EARLY_CLOSES: frozenset[date] = frozenset(
    date.fromisoformat(d)
    for d in (
        "2024-11-29",  # day after Thanksgiving
        "2024-12-24",  # Christmas Eve
        "2025-07-03",  # day before Independence Day
        "2025-11-28",  # day after Thanksgiving
        "2025-12-24",  # Christmas Eve
    )
)

CHECKPOINT_SCHEMA = "equity_corpus_v1.preflight.checkpoint/1"
REPORT_SCHEMA = "equity_corpus_v1.preflight.report/1"
MANIFEST_SCHEMA = "equity_corpus_v1.preflight.manifest/1"

STATUS_COMPLETE = "complete"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class PreflightError(RuntimeError):
    """Base class for every fail-closed preflight condition."""


class ProvenanceError(PreflightError):
    """A pinned hash, count, or frozen constant did not match."""


class CheckpointError(PreflightError):
    """A checkpoint is missing, malformed, corrupted, or provenance-mismatched."""


class BarFileError(PreflightError):
    """A bar file is unreadable, truncated, or malformed."""


# ---------------------------------------------------------------------------
# Canonical serialization + hashing
# ---------------------------------------------------------------------------


def canonical_json(obj: Any) -> str:
    """Deterministic serialization: sorted keys, no incidental whitespace.

    Every hash in this module is taken over this form, so a manifest hashed on
    one machine matches the same manifest hashed on another.
    """
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_obj(obj: Any) -> str:
    return sha256_text(canonical_json(obj))


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def code_sha256() -> str:
    """Hash of this module's own source — pins the preflight logic itself."""
    return sha256_file(Path(__file__).resolve())


# ---------------------------------------------------------------------------
# Session model
# ---------------------------------------------------------------------------


def _et_datetime(ts_ms: int) -> datetime:
    return datetime.fromtimestamp(ts_ms / 1000, tz=ET)


def _minutes_of_day(dt: datetime) -> int:
    return dt.hour * 60 + dt.minute


def session_tag(ts_ms: int) -> str | None:
    """PREMARKET / RTH / AFTER_HOURS, or None outside 04:00-20:00 ET.

    Uses zoneinfo, never a fixed UTC offset, so DST transitions stay correct.
    """
    minutes = _minutes_of_day(_et_datetime(ts_ms))
    if PREMARKET_OPEN_MIN <= minutes < RTH_OPEN_MIN:
        return "PREMARKET"
    if RTH_OPEN_MIN <= minutes < RTH_CLOSE_MIN:
        return "RTH"
    if RTH_CLOSE_MIN <= minutes < EXT_CLOSE_MIN:
        return "AFTER_HOURS"
    return None


def is_trading_session(day: date) -> bool:
    return day.weekday() < 5 and day not in MARKET_HOLIDAYS


def session_close_minutes(day: date) -> tuple[int, int]:
    """(rth_close, extended_close) in minutes-of-day ET for a given session."""
    if day in EARLY_CLOSES:
        return EARLY_RTH_CLOSE_MIN, EARLY_EXT_CLOSE_MIN
    return RTH_CLOSE_MIN, EXT_CLOSE_MIN


def expected_sessions(start: date = WINDOW_START, end: date = WINDOW_END) -> list[date]:
    """Every NYSE session in the frozen window, inclusive."""
    out: list[date] = []
    day = start
    while day <= end:
        if is_trading_session(day):
            out.append(day)
        day += timedelta(days=1)
    return out


# ---------------------------------------------------------------------------
# Universe loading + provenance
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UniverseEntry:
    ticker: str
    cohort: str
    role: str
    sector: str

    @property
    def is_setup_candidate(self) -> bool:
        return self.role.startswith("setup_candidate")

    @property
    def provider_ticker(self) -> str:
        """Polygon symbol. Index members use the `I:` namespace.

        VIX is an index, not an equity; its aggregate endpoint namespace differs.
        The manifest records the mapping explicitly so the batch runner can never
        silently request a non-existent equity ticker named "VIX".
        """
        if self.cohort == "index":
            return f"I:{self.ticker}"
        return self.ticker


@dataclass(frozen=True)
class Universe:
    version: str
    entries: tuple[UniverseEntry, ...]
    universe_sha256: str
    source_sha256: str
    raw: dict[str, Any] = field(repr=False, default_factory=dict)

    @property
    def tickers(self) -> tuple[str, ...]:
        return tuple(e.ticker for e in self.entries)


def load_universe(
    path: Path | str = UNIVERSE_PATH,
    *,
    expected_universe_sha256: str = UNIVERSE_SHA256,
    source_csv_path: Path | str | None = SOURCE_CSV_PATH,
    expected_source_sha256: str = SOURCE_CSV_SHA256,
    verify_source: bool = True,
) -> Universe:
    """Read the frozen universe read-only and verify every pinned invariant.

    Raises ProvenanceError on any mismatch. This function never writes.
    """
    path = Path(path)
    if not path.is_file():
        raise ProvenanceError(f"universe file not found: {path}")

    actual_universe_sha = sha256_file(path)
    if actual_universe_sha != expected_universe_sha256:
        raise ProvenanceError(
            "universe SHA-256 mismatch — the frozen universe has been altered: "
            f"expected {expected_universe_sha256}, got {actual_universe_sha}"
        )

    try:
        raw = json.loads(path.read_text())
    except json.JSONDecodeError as exc:  # pragma: no cover - sha guard fires first
        raise ProvenanceError(f"universe JSON is unparseable: {exc}") from exc

    if raw.get("universe_version") != CORPUS_VERSION:
        raise ProvenanceError(
            f"universe_version mismatch: expected {CORPUS_VERSION}, "
            f"got {raw.get('universe_version')!r}"
        )
    if raw.get("frozen") is not True:
        raise ProvenanceError("universe is not marked frozen=true")

    raw_entries = raw.get("entries") or []
    if len(raw_entries) != EXPECTED_TOTAL_ENTRIES:
        raise ProvenanceError(
            f"expected {EXPECTED_TOTAL_ENTRIES} entries, got {len(raw_entries)}"
        )
    if raw.get("total_entries") != EXPECTED_TOTAL_ENTRIES:
        raise ProvenanceError("declared total_entries does not match the frozen count")
    if raw.get("setup_candidates") != EXPECTED_SETUP_CANDIDATES:
        raise ProvenanceError(
            "declared setup_candidates does not match the frozen count"
        )
    if raw.get("cohort_counts") != EXPECTED_COHORT_COUNTS:
        raise ProvenanceError(
            f"cohort_counts mismatch: expected {EXPECTED_COHORT_COUNTS}, "
            f"got {raw.get('cohort_counts')}"
        )

    entries = tuple(
        UniverseEntry(
            ticker=e["ticker"],
            cohort=e["cohort"],
            role=e["role"],
            sector=e.get("sector", ""),
        )
        for e in raw_entries
    )

    seen: set[str] = set()
    for entry in entries:
        if entry.ticker in seen:
            raise ProvenanceError(f"duplicate ticker in universe: {entry.ticker}")
        seen.add(entry.ticker)

    observed_cohorts: dict[str, int] = {}
    for entry in entries:
        observed_cohorts[entry.cohort] = observed_cohorts.get(entry.cohort, 0) + 1
    if observed_cohorts != EXPECTED_COHORT_COUNTS:
        raise ProvenanceError(
            f"observed cohort membership {observed_cohorts} does not match the "
            f"frozen counts {EXPECTED_COHORT_COUNTS}"
        )

    source_sha = ""
    if verify_source and source_csv_path is not None:
        source_csv_path = Path(source_csv_path)
        if not source_csv_path.is_file():
            raise ProvenanceError(f"source watchlist not found: {source_csv_path}")
        source_sha = sha256_file(source_csv_path)
        if source_sha != expected_source_sha256:
            raise ProvenanceError(
                "source watchlist SHA-256 mismatch — the frozen source has been "
                f"altered: expected {expected_source_sha256}, got {source_sha}"
            )
        if raw.get("source_file_sha256") != expected_source_sha256:
            raise ProvenanceError(
                "universe's recorded source_file_sha256 does not match the pin"
            )

    return Universe(
        version=raw["universe_version"],
        entries=entries,
        universe_sha256=actual_universe_sha,
        source_sha256=source_sha,
        raw=raw,
    )


# ---------------------------------------------------------------------------
# Deterministic request manifest
# ---------------------------------------------------------------------------


def quarter_slices(
    start: date = WINDOW_START, end: date = WINDOW_END
) -> list[tuple[date, date]]:
    """Split the frozen window into calendar-quarter slices, clipped to bounds.

    Deterministic and provider-independent. Each slice spans at most one quarter
    (~63 sessions x 192 five-minute bars ~= 12,100 bars), comfortably under the
    50,000-row provider page ceiling, so one logical request is expected to be
    exactly one physical page.
    """
    out: list[tuple[date, date]] = []
    cursor = start
    while cursor <= end:
        q_end_month = ((cursor.month - 1) // 3 + 1) * 3
        if q_end_month == 12:
            q_end = date(cursor.year, 12, 31)
        else:
            q_end = date(cursor.year, q_end_month + 1, 1) - timedelta(days=1)
        slice_end = min(q_end, end)
        out.append((cursor, slice_end))
        cursor = slice_end + timedelta(days=1)
    return out


def request_id(
    *,
    symbol: str,
    endpoint: str,
    adjustment: str,
    timeframe: str,
    slice_start: date,
    slice_end: date,
    corpus_version: str = CORPUS_VERSION,
) -> str:
    """Stable ID over exactly the fields that define a logical request.

    Changing any of them changes the ID; changing anything else (ordering, run
    time, output path) does not.
    """
    return sha256_obj(
        {
            "adjustment": adjustment,
            "corpus_version": corpus_version,
            "endpoint": endpoint,
            "slice_end": slice_end.isoformat(),
            "slice_start": slice_start.isoformat(),
            "symbol": symbol,
            "timeframe": timeframe,
        }
    )[:16]


def build_manifest(
    universe: Universe,
    *,
    window_start: date = WINDOW_START,
    window_end: date = WINDOW_END,
) -> dict[str, Any]:
    """Build the full logical-request manifest for all universe entries.

    Deterministic: symbols are emitted in sorted ticker order and slices in
    chronological order, so the serialized manifest — and therefore its hash —
    is byte-identical across runs and machines.
    """
    slices = quarter_slices(window_start, window_end)
    entries_by_ticker = {e.ticker: e for e in universe.entries}

    requests: list[dict[str, Any]] = []
    per_symbol: dict[str, list[str]] = {}
    for ticker in sorted(entries_by_ticker):
        entry = entries_by_ticker[ticker]
        ids: list[str] = []
        for slice_start, slice_end in slices:
            rid = request_id(
                symbol=entry.provider_ticker,
                endpoint=ENDPOINT,
                adjustment=ADJUSTMENT,
                timeframe=TIMEFRAME,
                slice_start=slice_start,
                slice_end=slice_end,
            )
            ids.append(rid)
            requests.append(
                {
                    "request_id": rid,
                    "ticker": entry.ticker,
                    "provider_ticker": entry.provider_ticker,
                    "cohort": entry.cohort,
                    "role": entry.role,
                    "endpoint": ENDPOINT,
                    "adjustment": ADJUSTMENT,
                    "timeframe": TIMEFRAME,
                    "slice_start": slice_start.isoformat(),
                    "slice_end": slice_end.isoformat(),
                }
            )
        per_symbol[ticker] = ids

    manifest = {
        "schema": MANIFEST_SCHEMA,
        "corpus_version": CORPUS_VERSION,
        "universe_version": universe.version,
        "universe_sha256": universe.universe_sha256,
        "timezone": TIMEZONE,
        "window": {"start": window_start.isoformat(), "end": window_end.isoformat()},
        "endpoint": ENDPOINT,
        "adjustment": ADJUSTMENT,
        "timeframe": TIMEFRAME,
        "slice_rule": "calendar_quarter_clipped_to_window",
        "slices": [[s.isoformat(), e.isoformat()] for s, e in slices],
        "provider_page_limit": PROVIDER_PAGE_LIMIT,
        "symbol_count": len(per_symbol),
        "request_count": len(requests),
        "requests_per_symbol": len(slices),
        "request_ids_by_symbol": per_symbol,
        "requests": requests,
    }
    return manifest


def manifest_sha256(manifest: dict[str, Any]) -> str:
    return sha256_obj(manifest)


def window_sha256(
    window_start: date = WINDOW_START, window_end: date = WINDOW_END
) -> str:
    return sha256_obj(
        {
            "start": window_start.isoformat(),
            "end": window_end.isoformat(),
            "timezone": TIMEZONE,
        }
    )


def config_sha256(*, extra_holidays: Sequence[date] = ()) -> str:
    """Hash of the validation configuration. Environment paths are excluded."""
    return sha256_obj(
        {
            "adjustment": ADJUSTMENT,
            "bar_interval_minutes": BAR_INTERVAL_MINUTES,
            "early_closes": sorted(d.isoformat() for d in EARLY_CLOSES),
            "endpoint": ENDPOINT,
            "extra_holidays": sorted(d.isoformat() for d in extra_holidays),
            "holidays": sorted(d.isoformat() for d in MARKET_HOLIDAYS),
            "session_bounds": {
                "premarket_open": PREMARKET_OPEN_MIN,
                "rth_open": RTH_OPEN_MIN,
                "rth_close": RTH_CLOSE_MIN,
                "extended_close": EXT_CLOSE_MIN,
                "early_rth_close": EARLY_RTH_CLOSE_MIN,
                "early_extended_close": EARLY_EXT_CLOSE_MIN,
            },
            "session_tags": list(SESSION_TAGS),
            "slice_rule": "calendar_quarter_clipped_to_window",
            "timeframe": TIMEFRAME,
        }
    )


def build_provenance(
    manifest: dict[str, Any],
    universe: Universe,
    *,
    extra_holidays: Sequence[date] = (),
) -> dict[str, str]:
    """The five hashes every checkpoint must match to be resumable."""
    return {
        "manifest_sha256": manifest_sha256(manifest),
        "universe_sha256": universe.universe_sha256,
        "code_sha256": code_sha256(),
        "window_sha256": window_sha256(),
        "config_sha256": config_sha256(extra_holidays=extra_holidays),
    }


# ---------------------------------------------------------------------------
# Bar loading
# ---------------------------------------------------------------------------

REQUIRED_BAR_KEYS = ("t", "o", "h", "l", "c")


def load_bars_jsonl(path: Path | str) -> list[dict[str, Any]]:
    """Read a JSONL bar file strictly.

    A truncated final line — the signature of a run killed mid-write — raises
    BarFileError rather than yielding a short, plausible-looking bar list.
    """
    path = Path(path)
    if not path.is_file():
        raise BarFileError(f"bar file not found: {path}")

    raw = path.read_text()
    if raw and not raw.endswith("\n"):
        raise BarFileError(
            f"bar file {path.name} does not end with a newline — it is truncated "
            "(partial output from an interrupted write)"
        )

    bars: list[dict[str, Any]] = []
    for lineno, line in enumerate(raw.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            bar = json.loads(line)
        except json.JSONDecodeError as exc:
            raise BarFileError(
                f"bar file {path.name} line {lineno} is not valid JSON: {exc}"
            ) from exc
        if not isinstance(bar, dict):
            raise BarFileError(f"bar file {path.name} line {lineno} is not an object")
        missing = [k for k in REQUIRED_BAR_KEYS if k not in bar]
        if missing:
            raise BarFileError(
                f"bar file {path.name} line {lineno} is missing keys: {missing}"
            )
        bars.append(bar)
    return bars


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Finding:
    code: str
    detail: str
    count: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "detail": self.detail, "count": self.count}


@dataclass
class SymbolResult:
    ticker: str
    status: str                      # PASS | FAIL
    bar_count: int = 0
    session_count: int = 0
    rth_bar_count: int = 0
    distinct_utc_offsets: tuple[str, ...] = ()
    earliest: str | None = None
    latest: str | None = None
    findings: list[Finding] = field(default_factory=list)
    resumed: bool = False

    @property
    def passed(self) -> bool:
        return self.status == "PASS"

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "status": self.status,
            "bar_count": self.bar_count,
            "session_count": self.session_count,
            "rth_bar_count": self.rth_bar_count,
            "distinct_utc_offsets": list(self.distinct_utc_offsets),
            "earliest": self.earliest,
            "latest": self.latest,
            "resumed": self.resumed,
            "findings": [f.to_dict() for f in self.findings],
        }


def _describe(items: Iterable[Any], limit: int = 5) -> str:
    items = list(items)
    head = ", ".join(str(i) for i in items[:limit])
    if len(items) > limit:
        head += f", ... (+{len(items) - limit} more)"
    return head


def validate_symbol_bars(
    ticker: str,
    bars: Sequence[dict[str, Any]],
    *,
    window_start: date = WINDOW_START,
    window_end: date = WINDOW_END,
    extra_holidays: Sequence[date] = (),
    require_dst: bool = True,
) -> SymbolResult:
    """Run every coverage gate over one symbol's 5-minute bars.

    Every gate is a hard failure. There is no warning tier.
    """
    findings: list[Finding] = []

    if not bars:
        return SymbolResult(
            ticker=ticker,
            status="FAIL",
            findings=[Finding("NO_BARS", "symbol has zero bars")],
        )

    timestamps = [b["t"] for b in bars]

    non_int = [t for t in timestamps if not isinstance(t, int) or isinstance(t, bool)]
    if non_int:
        findings.append(
            Finding(
                "NON_INTEGER_TIMESTAMP",
                f"non-integer epoch-ms timestamps: {_describe(non_int)}",
                len(non_int),
            )
        )
        # Everything downstream assumes int ms; stop here rather than guess.
        return SymbolResult(ticker=ticker, status="FAIL", bar_count=len(bars),
                            findings=findings)

    # --- duplicates -------------------------------------------------------
    seen: set[int] = set()
    dupes: list[int] = []
    for t in timestamps:
        if t in seen:
            dupes.append(t)
        seen.add(t)
    if dupes:
        findings.append(
            Finding(
                "DUPLICATE_TIMESTAMP",
                f"{len(dupes)} duplicate timestamp(s): "
                f"{_describe(_et_datetime(t).isoformat() for t in dupes)}",
                len(dupes),
            )
        )

    # --- ordering ---------------------------------------------------------
    out_of_order = [
        (a, b) for a, b in zip(timestamps, timestamps[1:]) if b <= a
    ]
    if out_of_order:
        findings.append(
            Finding(
                "OUT_OF_ORDER",
                f"{len(out_of_order)} non-monotonic timestamp transition(s): "
                + _describe(
                    f"{_et_datetime(a).isoformat()} -> {_et_datetime(b).isoformat()}"
                    for a, b in out_of_order
                ),
                len(out_of_order),
            )
        )

    # --- 5-minute alignment ----------------------------------------------
    misaligned = [t for t in timestamps if t % BAR_INTERVAL_MS != 0]
    if misaligned:
        findings.append(
            Finding(
                "MISALIGNED_INTERVAL",
                f"{len(misaligned)} bar(s) not on a {BAR_INTERVAL_MINUTES}-minute "
                "boundary: "
                + _describe(_et_datetime(t).isoformat() for t in misaligned),
                len(misaligned),
            )
        )

    # --- window bounds ----------------------------------------------------
    out_of_window: list[int] = []
    after_close: list[int] = []
    outside_session: list[int] = []
    for t in timestamps:
        dt = _et_datetime(t)
        if not (window_start <= dt.date() <= window_end):
            out_of_window.append(t)
            continue
        tag = session_tag(t)
        if tag is None:
            outside_session.append(t)
            continue
        _, ext_close = session_close_minutes(dt.date())
        if _minutes_of_day(dt) >= ext_close:
            after_close.append(t)

    if out_of_window:
        findings.append(
            Finding(
                "OUT_OF_WINDOW",
                f"{len(out_of_window)} bar(s) outside the frozen window "
                f"{window_start}..{window_end}: "
                + _describe(_et_datetime(t).isoformat() for t in out_of_window),
                len(out_of_window),
            )
        )
    if outside_session:
        findings.append(
            Finding(
                "OUTSIDE_SESSION_HOURS",
                f"{len(outside_session)} bar(s) outside 04:00-20:00 ET: "
                + _describe(_et_datetime(t).isoformat() for t in outside_session),
                len(outside_session),
            )
        )
    if after_close:
        findings.append(
            Finding(
                "BAR_AFTER_SESSION_CLOSE",
                f"{len(after_close)} bar(s) after that session's extended close "
                "(half-day aware): "
                + _describe(_et_datetime(t).isoformat() for t in after_close),
                len(after_close),
            )
        )

    # --- session tags -----------------------------------------------------
    bad_tags: list[str] = []
    for bar in bars:
        expected_tag = session_tag(bar["t"])
        actual_tag = bar.get("session")
        if actual_tag is None:
            bad_tags.append(f"{_et_datetime(bar['t']).isoformat()}: missing")
        elif actual_tag not in SESSION_TAGS:
            bad_tags.append(f"{_et_datetime(bar['t']).isoformat()}: {actual_tag!r}")
        elif expected_tag is not None and actual_tag != expected_tag:
            bad_tags.append(
                f"{_et_datetime(bar['t']).isoformat()}: {actual_tag} "
                f"!= recomputed {expected_tag}"
            )
    if bad_tags:
        findings.append(
            Finding(
                "INVALID_SESSION_TAG",
                f"{len(bad_tags)} bar(s) with a missing, unknown, or disagreeing "
                f"session tag: {_describe(bad_tags)}",
                len(bad_tags),
            )
        )

    # --- DST --------------------------------------------------------------
    offsets = sorted({str(_et_datetime(t).utcoffset()) for t in timestamps})
    if require_dst and len(offsets) < 2:
        findings.append(
            Finding(
                "DST_NOT_OBSERVED",
                "only one distinct UTC offset across a 24-month window "
                f"({offsets}) — timestamps look built from a fixed offset "
                "rather than the America/New_York zone",
            )
        )

    # --- session coverage -------------------------------------------------
    covered = {_et_datetime(t).date() for t in timestamps}
    rth_covered = {
        _et_datetime(b["t"]).date() for b in bars if session_tag(b["t"]) == "RTH"
    }
    holidays = set(extra_holidays)
    expected = [
        d for d in expected_sessions(window_start, window_end) if d not in holidays
    ]
    missing = [d for d in expected if d not in covered]
    missing_rth = [d for d in expected if d not in rth_covered]
    extra = sorted(
        d for d in covered
        if window_start <= d <= window_end and not is_trading_session(d)
    )

    if missing:
        findings.append(
            Finding(
                "MISSING_SESSION",
                f"{len(missing)} expected trading session(s) with no bars: "
                + _describe(d.isoformat() for d in missing),
                len(missing),
            )
        )
    if missing_rth and missing_rth != missing:
        only_rth = [d for d in missing_rth if d not in set(missing)]
        if only_rth:
            findings.append(
                Finding(
                    "MISSING_RTH_SESSION",
                    f"{len(only_rth)} session(s) present but with zero RTH bars: "
                    + _describe(d.isoformat() for d in only_rth),
                    len(only_rth),
                )
            )
    if extra:
        findings.append(
            Finding(
                "EXTRA_SESSION",
                f"{len(extra)} bar date(s) on a weekend or frozen holiday: "
                + _describe(d.isoformat() for d in extra),
                len(extra),
            )
        )

    return SymbolResult(
        ticker=ticker,
        status="FAIL" if findings else "PASS",
        bar_count=len(bars),
        session_count=len(covered),
        rth_bar_count=sum(1 for b in bars if session_tag(b["t"]) == "RTH"),
        distinct_utc_offsets=tuple(offsets),
        earliest=_et_datetime(min(timestamps)).isoformat(),
        latest=_et_datetime(max(timestamps)).isoformat(),
        findings=findings,
    )


# ---------------------------------------------------------------------------
# Checkpoints
# ---------------------------------------------------------------------------


class CheckpointStore:
    """Per-symbol checkpoints, written atomically, verified on read.

    Atomic write = write a temp file in the same directory, fsync, then
    os.replace. A process killed mid-write leaves either the previous complete
    checkpoint or no checkpoint — never a half-written one.
    """

    PROVENANCE_KEYS = (
        "manifest_sha256",
        "universe_sha256",
        "code_sha256",
        "window_sha256",
        "config_sha256",
    )

    def __init__(self, root: Path | str):
        self.root = Path(root)

    def path_for(self, ticker: str) -> Path:
        return self.root / f"{ticker}.json"

    # -- write ------------------------------------------------------------

    def write(
        self,
        result: SymbolResult,
        *,
        provenance: dict[str, str],
        request_ids: Sequence[str],
    ) -> Path:
        """Persist a COMPLETE checkpoint. Failed symbols are never checkpointed.

        Only complete work is resumable; a failed symbol must be re-validated on
        the next run, never skipped.
        """
        if not result.passed:
            raise CheckpointError(
                f"refusing to checkpoint {result.ticker}: status={result.status}"
            )

        body = {
            "schema": CHECKPOINT_SCHEMA,
            "symbol": result.ticker,
            "status": STATUS_COMPLETE,
            "provenance": {k: provenance[k] for k in self.PROVENANCE_KEYS},
            "request_ids": list(request_ids),
            "summary": {
                "bar_count": result.bar_count,
                "session_count": result.session_count,
                "rth_bar_count": result.rth_bar_count,
                "distinct_utc_offsets": list(result.distinct_utc_offsets),
                "earliest": result.earliest,
                "latest": result.latest,
            },
        }
        payload = {**body, "payload_sha256": sha256_obj(body)}

        self.root.mkdir(parents=True, exist_ok=True)
        target = self.path_for(result.ticker)
        fd, tmp_name = tempfile.mkstemp(
            dir=str(self.root), prefix=f".{result.ticker}.", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w") as fh:
                fh.write(canonical_json(payload) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_name, target)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise
        return target

    # -- read -------------------------------------------------------------

    def read(self, ticker: str) -> dict[str, Any] | None:
        """Return the checkpoint payload, or None if absent.

        Raises CheckpointError if present but unreadable, malformed, or
        integrity-broken. Absent is a normal state; corrupt never is.
        """
        path = self.path_for(ticker)
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            raise CheckpointError(
                f"checkpoint {path.name} is corrupted (unparseable JSON): {exc}"
            ) from exc
        if not isinstance(payload, dict):
            raise CheckpointError(f"checkpoint {path.name} is not an object")

        recorded = payload.get("payload_sha256")
        if not recorded:
            raise CheckpointError(
                f"checkpoint {path.name} has no payload_sha256 — incomplete write"
            )
        body = {k: v for k, v in payload.items() if k != "payload_sha256"}
        if sha256_obj(body) != recorded:
            raise CheckpointError(
                f"checkpoint {path.name} is corrupted: payload_sha256 does not "
                "match its contents"
            )

        for key in ("schema", "symbol", "status", "provenance", "request_ids"):
            if key not in payload:
                raise CheckpointError(
                    f"checkpoint {path.name} is incomplete: missing {key!r}"
                )
        if payload["schema"] != CHECKPOINT_SCHEMA:
            raise CheckpointError(
                f"checkpoint {path.name} schema {payload['schema']!r} is not "
                f"{CHECKPOINT_SCHEMA!r}"
            )
        if payload["symbol"] != ticker:
            raise CheckpointError(
                f"checkpoint {path.name} holds symbol {payload['symbol']!r}"
            )
        return payload

    def verify(
        self,
        ticker: str,
        *,
        provenance: dict[str, str],
        request_ids: Sequence[str],
    ) -> dict[str, Any] | None:
        """Return the checkpoint only if it is complete AND fully matching.

        Any provenance drift — manifest, universe, code, window, or config —
        raises. Stale work is never silently reused.
        """
        payload = self.read(ticker)
        if payload is None:
            return None

        if payload["status"] != STATUS_COMPLETE:
            raise CheckpointError(
                f"checkpoint for {ticker} is incomplete: status="
                f"{payload['status']!r}"
            )

        recorded_prov = payload.get("provenance") or {}
        for key in self.PROVENANCE_KEYS:
            if key not in recorded_prov:
                raise CheckpointError(
                    f"checkpoint for {ticker} is missing provenance key {key!r}"
                )
            if recorded_prov[key] != provenance[key]:
                raise CheckpointError(
                    f"checkpoint for {ticker} has a stale {key}: "
                    f"{recorded_prov[key]} != {provenance[key]}"
                )

        if list(payload["request_ids"]) != list(request_ids):
            raise CheckpointError(
                f"checkpoint for {ticker} covers a different request set than "
                "the current manifest"
            )
        return payload


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def _result_from_checkpoint(payload: dict[str, Any]) -> SymbolResult:
    summary = payload.get("summary") or {}
    return SymbolResult(
        ticker=payload["symbol"],
        status="PASS",
        bar_count=summary.get("bar_count", 0),
        session_count=summary.get("session_count", 0),
        rth_bar_count=summary.get("rth_bar_count", 0),
        distinct_utc_offsets=tuple(summary.get("distinct_utc_offsets") or ()),
        earliest=summary.get("earliest"),
        latest=summary.get("latest"),
        resumed=True,
    )


def bar_file_for(bars_dir: Path, ticker: str) -> Path:
    return Path(bars_dir) / f"{ticker}_5m.jsonl"


def run_preflight(
    *,
    bars_dir: Path | str,
    checkpoint_dir: Path | str,
    universe_path: Path | str = UNIVERSE_PATH,
    source_csv_path: Path | str | None = SOURCE_CSV_PATH,
    symbols: Sequence[str] | None = None,
    resume: bool = True,
    extra_holidays: Sequence[date] = (),
    require_dst: bool = True,
    verify_source: bool = True,
) -> dict[str, Any]:
    """Execute the preflight and return a machine-readable completeness report.

    Performs no network I/O. The returned report is deterministic: it embeds no
    wall-clock time, so two identical runs produce byte-identical reports.
    """
    universe = load_universe(
        universe_path,
        source_csv_path=source_csv_path,
        verify_source=verify_source,
    )
    manifest = build_manifest(universe)
    provenance = build_provenance(manifest, universe, extra_holidays=extra_holidays)
    store = CheckpointStore(checkpoint_dir)

    all_tickers = sorted(universe.tickers)
    if symbols is None:
        required = all_tickers
    else:
        unknown = sorted(set(symbols) - set(all_tickers))
        if unknown:
            raise ProvenanceError(
                f"requested symbols are not in the frozen universe: {unknown}"
            )
        required = sorted(set(symbols))

    # Manifest coverage is itself a gate: a symbol with no planned requests
    # would otherwise be "complete" by vacuous truth.
    uncovered = [t for t in required if not manifest["request_ids_by_symbol"].get(t)]
    if uncovered:
        raise ProvenanceError(
            f"manifest has no requests planned for: {_describe(uncovered)}"
        )

    results: list[SymbolResult] = []
    for ticker in required:
        request_ids = manifest["request_ids_by_symbol"][ticker]

        if resume:
            try:
                payload = store.verify(
                    ticker, provenance=provenance, request_ids=request_ids
                )
            except CheckpointError as exc:
                results.append(
                    SymbolResult(
                        ticker=ticker,
                        status="FAIL",
                        findings=[Finding("CHECKPOINT_REJECTED", str(exc))],
                    )
                )
                continue
            if payload is not None:
                results.append(_result_from_checkpoint(payload))
                continue

        path = bar_file_for(Path(bars_dir), ticker)
        try:
            bars = load_bars_jsonl(path)
        except BarFileError as exc:
            results.append(
                SymbolResult(
                    ticker=ticker,
                    status="FAIL",
                    findings=[Finding("BAR_FILE_UNUSABLE", str(exc))],
                )
            )
            continue

        result = validate_symbol_bars(
            ticker,
            bars,
            extra_holidays=extra_holidays,
            require_dst=require_dst,
        )
        if result.passed:
            store.write(result, provenance=provenance, request_ids=request_ids)
        results.append(result)

    return build_report(
        results,
        manifest=manifest,
        universe=universe,
        provenance=provenance,
        required=required,
    )


def build_report(
    results: Sequence[SymbolResult],
    *,
    manifest: dict[str, Any],
    universe: Universe,
    provenance: dict[str, str],
    required: Sequence[str],
) -> dict[str, Any]:
    """Assemble the fail-closed completeness report.

    PASS requires: every required symbol present in the results, every one of
    them PASS, and zero findings. There is no partial-credit state.
    """
    by_ticker = {r.ticker: r for r in results}
    missing_results = [t for t in required if t not in by_ticker]
    failed = sorted(t for t, r in by_ticker.items() if not r.passed)
    passed = sorted(t for t, r in by_ticker.items() if r.passed)

    findings_by_code: dict[str, int] = {}
    for r in results:
        for f in r.findings:
            findings_by_code[f.code] = findings_by_code.get(f.code, 0) + f.count

    complete = not missing_results and not failed and len(passed) == len(required)

    report = {
        "schema": REPORT_SCHEMA,
        "corpus_version": CORPUS_VERSION,
        "universe_version": universe.version,
        "status": "PASS" if complete else "FAIL",
        "exit_code": 0 if complete else 1,
        "authorizes_corpus_batch": False,
        "provenance": dict(provenance),
        "manifest": {
            "sha256": provenance["manifest_sha256"],
            "symbol_count": manifest["symbol_count"],
            "request_count": manifest["request_count"],
            "requests_per_symbol": manifest["requests_per_symbol"],
            "slice_rule": manifest["slice_rule"],
            "window": manifest["window"],
            "endpoint": manifest["endpoint"],
            "adjustment": manifest["adjustment"],
            "timeframe": manifest["timeframe"],
        },
        "counts": {
            "universe_entries": len(universe.entries),
            "required": len(required),
            "passed": len(passed),
            "failed": len(failed),
            "no_result": len(missing_results),
            "resumed": sum(1 for r in results if r.resumed),
            "expected_sessions": len(expected_sessions()),
        },
        "failed_symbols": failed,
        "symbols_without_result": missing_results,
        "findings_by_code": dict(sorted(findings_by_code.items())),
        "symbols": {t: by_ticker[t].to_dict() for t in sorted(by_ticker)},
    }
    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Equity corpus v1 batch-runner preflight. No network, no fetch, no "
            "production code. Exits non-zero unless every required symbol passes."
        )
    )
    p.add_argument("--bars-dir", default="data/equity_corpus_v1/bars")
    p.add_argument("--checkpoint-dir", default="data/equity_corpus_v1/checkpoints")
    p.add_argument("--universe", default=str(UNIVERSE_PATH))
    p.add_argument("--report", default=None, help="write the JSON report here")
    p.add_argument("--manifest-out", default=None, help="write the manifest here")
    p.add_argument(
        "--symbols",
        default=None,
        help="comma-separated subset of the frozen universe (testing only)",
    )
    p.add_argument("--no-resume", action="store_true")
    p.add_argument(
        "--extra-holidays",
        default="",
        help="comma-separated extra ISO closure dates; folded into config_sha256",
    )
    p.add_argument(
        "--allow-single-utc-offset",
        action="store_true",
        help="skip the DST gate (only valid for sub-DST-cycle fixtures)",
    )
    return p.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)

    extra_holidays = tuple(
        date.fromisoformat(d.strip())
        for d in args.extra_holidays.split(",")
        if d.strip()
    )
    symbols = (
        [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        if args.symbols
        else None
    )

    try:
        report = run_preflight(
            bars_dir=args.bars_dir,
            checkpoint_dir=args.checkpoint_dir,
            universe_path=args.universe,
            symbols=symbols,
            resume=not args.no_resume,
            extra_holidays=extra_holidays,
            require_dst=not args.allow_single_utc_offset,
        )
    except PreflightError as exc:
        # A provenance failure is fatal and un-resumable: report it as such
        # rather than emitting a partial report that reads like progress.
        print(f"PREFLIGHT ABORTED — {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    if args.manifest_out:
        universe = load_universe(args.universe)
        manifest = build_manifest(universe)
        out = Path(args.manifest_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(canonical_json(manifest) + "\n")

    if args.report:
        out = Path(args.report)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")

    counts = report["counts"]
    print(f"PREFLIGHT {report['status']}")
    print(f"  manifest sha256    {report['provenance']['manifest_sha256']}")
    print(f"  requests planned   {report['manifest']['request_count']} "
          f"({report['manifest']['requests_per_symbol']}/symbol)")
    print(f"  required symbols   {counts['required']}")
    print(f"  passed / failed    {counts['passed']} / {counts['failed']}")
    print(f"  resumed            {counts['resumed']}")
    if report["findings_by_code"]:
        print("  findings:")
        for code, n in report["findings_by_code"].items():
            print(f"    {code}: {n}")
    if report["failed_symbols"]:
        print(f"  failed symbols: {_describe(report['failed_symbols'], 20)}")
    print("  corpus batch authorization: NOT GRANTED by this report")

    return int(report["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
