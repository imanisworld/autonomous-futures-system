"""Equity corpus v2 — resumable batch-runner preflight. READ-ONLY, NO NETWORK.

Gate §8.4 of docs/equity-setup-corpus-preregistration-v2.md: verify the batch
runner's request plan, restart/idempotency behaviour, pagination-completion
evidence, and real bar coverage BEFORE any corpus fetch is authorized.

This module performs **no network I/O whatsoever**. It imports no HTTP client and
requires no API key. Everything it checks is derived from local files:

  - the frozen universe JSON (SHA-256 pinned, unchanged from v1),
  - the frozen source watchlist CSV (SHA-256 pinned),
  - the v1 and v2 preregistration documents,
  - per-symbol 5-minute bar files and fetch-evidence files, if present.

It does not fetch the corpus, does not alter the frozen universe, and imports
nothing from the production trading stack.

Fail-closed means exactly that: there is no "complete with warnings" state. The
process exits non-zero unless every required symbol passes every gate.

What v2 corrects (see the v2 document §0 for the full record)
-------------------------------------------------------------
D-1  Session tagging is now **calendar-aware**. v1's flat 09:30-16:00 RTH rule
     mislabelled 13:00-16:00 half-day extended activity as RTH, contaminating the
     primary dataset. `session_tag_v1_flat` is preserved for the parity record.

D-2  Coverage is now evaluated against the **expected five-minute grid** per
     session per tag. v1's "missing-session count" only proved a session had at
     least one bar, which a single bar per day would satisfy.

D-3  **No one-page pagination assumption exists.** The provider's row limit
     bounds base aggregates queried, not aggregated bars returned: a
     calendar-quarter slice of 5-minute bars spans ~60,480 one-minute base
     aggregates, above the 50,000 ceiling. 1,404 is the count of *logical*
     requests only. Physical completion must be proven by recorded evidence.

D-4  Checkpoints are **bound to the corpus bytes**. A resumed symbol re-reads and
     re-hashes its bar file and evidence file; a deleted, truncated, reordered,
     or edited file fails the run instead of returning a synthetic PASS.

Deliberate duplication
----------------------
Session tagging is reimplemented here rather than imported from
`research/equity_corpus_smoke.py`, which imports `httpx` at module scope; this
module must stay import-clean of any network library. A parity test pins the two
together on ordinary sessions and documents exactly where v2 departs.
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import json
import os
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Sequence
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------------------
# Frozen constants. None of these may be changed here; a change requires a
# further versioned preregistration correction.
# ---------------------------------------------------------------------------

CORPUS_VERSION = "equity_corpus_v2"
UNIVERSE_VERSION = "equity_corpus_v1"      # membership unchanged; file reused
SUPERSEDED_VERSION = "equity_corpus_v1"

REPO_ROOT = Path(__file__).resolve().parent.parent
UNIVERSE_PATH = REPO_ROOT / "research" / "universe" / "equity_corpus_v1_universe.json"
SOURCE_CSV_PATH = REPO_ROOT / "docs" / "options_watchlist_150.csv"
PREREG_V1_PATH = REPO_ROOT / "docs" / "equity-setup-corpus-preregistration-v1.md"
PREREG_V2_PATH = REPO_ROOT / "docs" / "equity-setup-corpus-preregistration-v2.md"

UNIVERSE_SHA256 = "327c7dcd795acc9a11d0b14c6030f0a03e14960245e3ef8740f6bedde9b90a67"
SOURCE_CSV_SHA256 = "2770c80b9d6b745b481245b457957e45275ad3590d6aa5cdfc6f7ca4761f1d4d"
# v1 is frozen history: pinned so tampering with the superseded record is caught.
PREREG_V1_SHA256 = "69bcd953c0df02a3361131640479bc79ce44467c763f22962cc633abfc7eee26"

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

SESSION_TAGS = ("PREMARKET", "RTH", "AFTER_HOURS")
PREMARKET_OPEN_MIN = 4 * 60          # 04:00 ET
RTH_OPEN_MIN = 9 * 60 + 30           # 09:30 ET
RTH_CLOSE_MIN = 16 * 60              # 16:00 ET
EXT_CLOSE_MIN = 20 * 60              # 20:00 ET

EARLY_RTH_CLOSE_MIN = 13 * 60        # 13:00 ET — core close on a half day
EARLY_EXT_CLOSE_MIN = 17 * 60        # 17:00 ET — late session close on a half day

MARKET_HOLIDAYS: frozenset[date] = frozenset(
    date.fromisoformat(d)
    for d in (
        "2024-09-02", "2024-11-28", "2024-12-25",
        "2025-01-01",
        "2025-01-09",  # national day of mourning (President Carter)
        "2025-01-20", "2025-02-17", "2025-04-18", "2025-05-26", "2025-06-19",
        "2025-07-04", "2025-09-01", "2025-11-27", "2025-12-25",
        "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25",
        "2026-06-19",
        "2026-07-03",  # Independence Day observed; July 4 is a Saturday
    )
)

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

# Frozen halt / calendar exceptions permitting a missing RTH interval.
# EMPTY AT v2 FREEZE. Entries are added only by a further versioned correction,
# with a date and a reason — never by loosening a threshold after seeing results.
RTH_EXCEPTIONS: dict[str, dict[str, str]] = {}

CHECKPOINT_SCHEMA = "equity_corpus_v2.preflight.checkpoint/1"
REPORT_SCHEMA = "equity_corpus_v2.preflight.report/1"
MANIFEST_SCHEMA = "equity_corpus_v2.preflight.manifest/1"
EVIDENCE_SCHEMA = "equity_corpus_v2.fetch_evidence/1"

STATUS_COMPLETE = "complete"

# How many individual offending items to name in a finding. The *count* is always
# exact; only the enumeration is bounded, and it says so.
SAMPLE_LIMIT = 8


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


class EvidenceFileError(PreflightError):
    """A fetch-evidence file is unreadable or malformed."""


# ---------------------------------------------------------------------------
# Canonical serialization + hashing
# ---------------------------------------------------------------------------


def canonical_json(obj: Any) -> str:
    """Deterministic serialization: sorted keys, no incidental whitespace."""
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


def _describe(items: Iterable[Any], limit: int = SAMPLE_LIMIT) -> str:
    items = list(items)
    head = ", ".join(str(i) for i in items[:limit])
    if len(items) > limit:
        head += f", ... (+{len(items) - limit} more, not enumerated)"
    return head


# ---------------------------------------------------------------------------
# Calendar-aware session model  (v2 §3 — corrects D-1)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SessionBounds:
    """Minutes-of-day ET bounds for one trading session."""

    premarket_open: int
    rth_open: int
    rth_close: int
    ext_close: int
    is_early_close: bool

    def bounds_for(self, tag: str) -> tuple[int, int]:
        if tag == "PREMARKET":
            return self.premarket_open, self.rth_open
        if tag == "RTH":
            return self.rth_open, self.rth_close
        if tag == "AFTER_HOURS":
            return self.rth_close, self.ext_close
        raise ValueError(f"unknown session tag: {tag!r}")


def _et_datetime(ts_ms: int) -> datetime:
    return datetime.fromtimestamp(ts_ms / 1000, tz=ET)


def _minutes_of_day(dt: datetime) -> int:
    return dt.hour * 60 + dt.minute


def is_trading_session(day: date) -> bool:
    return day.weekday() < 5 and day not in MARKET_HOLIDAYS


def session_bounds(day: date) -> SessionBounds | None:
    """Bounds for `day`, or None if the market was closed.

    On an early-close session core trading ends at 13:00 and the late session
    ends at 17:00 — so 13:00-16:00 is AFTER_HOURS, not RTH. This is the whole
    point of the v2 correction.
    """
    if not is_trading_session(day):
        return None
    if day in EARLY_CLOSES:
        return SessionBounds(
            premarket_open=PREMARKET_OPEN_MIN,
            rth_open=RTH_OPEN_MIN,
            rth_close=EARLY_RTH_CLOSE_MIN,
            ext_close=EARLY_EXT_CLOSE_MIN,
            is_early_close=True,
        )
    return SessionBounds(
        premarket_open=PREMARKET_OPEN_MIN,
        rth_open=RTH_OPEN_MIN,
        rth_close=RTH_CLOSE_MIN,
        ext_close=EXT_CLOSE_MIN,
        is_early_close=False,
    )


def session_tag(ts_ms: int) -> str | None:
    """Calendar-aware tag, or None outside the day's bounds / on a closed day."""
    dt = _et_datetime(ts_ms)
    bounds = session_bounds(dt.date())
    if bounds is None:
        return None
    minutes = _minutes_of_day(dt)
    for tag in SESSION_TAGS:
        lo, hi = bounds.bounds_for(tag)
        if lo <= minutes < hi:
            return tag
    return None


def session_tag_v1_flat(ts_ms: int) -> str | None:
    """The SUPERSEDED v1 rule: 09:30-16:00 is RTH on every day, half days included.

    Preserved only so the v2 correction is auditable — the parity test shows
    exactly which bars v1 mislabelled. Never use this for classification.
    """
    minutes = _minutes_of_day(_et_datetime(ts_ms))
    if PREMARKET_OPEN_MIN <= minutes < RTH_OPEN_MIN:
        return "PREMARKET"
    if RTH_OPEN_MIN <= minutes < RTH_CLOSE_MIN:
        return "RTH"
    if RTH_CLOSE_MIN <= minutes < EXT_CLOSE_MIN:
        return "AFTER_HOURS"
    return None


def expected_sessions(start: date = WINDOW_START, end: date = WINDOW_END) -> list[date]:
    out: list[date] = []
    day = start
    while day <= end:
        if is_trading_session(day):
            out.append(day)
        day += timedelta(days=1)
    return out


@functools.lru_cache(maxsize=None)
def _expected_grid_cached(day: date, tag: str) -> tuple[int, ...]:
    bounds = session_bounds(day)
    if bounds is None:
        return ()
    lo, hi = bounds.bounds_for(tag)
    return tuple(
        int(datetime(day.year, day.month, day.day, m // 60, m % 60,
                     tzinfo=ET).timestamp() * 1000)
        for m in range(lo, hi, BAR_INTERVAL_MINUTES)
    )


def expected_grid(day: date, tag: str) -> list[int]:
    """Every expected 5-minute interval start (epoch ms) for `day` and `tag`.

    Memoized: the frozen window has ~502 sessions x 3 tags, and coverage is
    recomputed for every symbol.
    """
    return list(_expected_grid_cached(day, tag))


# ---------------------------------------------------------------------------
# Coverage policy  (v2 §4 — corrects D-2)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CoveragePolicy:
    """Frozen, per-asset-class rule for what a coverage gap means."""

    name: str
    rth_requires_full_grid: bool
    extended_requires_full_grid: bool

    def requires_full_grid(self, tag: str) -> bool:
        if tag == "RTH":
            return self.rth_requires_full_grid
        return self.extended_requires_full_grid


# Equities/ETFs: RTH grid must be complete; extended sparsity is expected and is
# published, not failed. Indices publish on update, not on trade, so an absent
# interval is not evidence of loss in any segment.
POLICY_EQUITY = CoveragePolicy(
    name="equity_rth_strict",
    rth_requires_full_grid=True,
    extended_requires_full_grid=False,
)
POLICY_INDEX = CoveragePolicy(
    name="index_enumerate_only",
    rth_requires_full_grid=False,
    extended_requires_full_grid=False,
)


def policy_for(cohort: str) -> CoveragePolicy:
    return POLICY_INDEX if cohort == "index" else POLICY_EQUITY


def policies_as_dict() -> dict[str, Any]:
    return {
        p.name: {
            "rth_requires_full_grid": p.rth_requires_full_grid,
            "extended_requires_full_grid": p.extended_requires_full_grid,
        }
        for p in (POLICY_EQUITY, POLICY_INDEX)
    }


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

        VIX is an index, not an equity. The mapping is recorded explicitly so the
        batch runner cannot silently request a non-existent equity named "VIX".
        It is UNVERIFIED — no endpoint or entitlement check has been authorized.
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

    def by_ticker(self, ticker: str) -> UniverseEntry:
        for e in self.entries:
            if e.ticker == ticker:
                return e
        raise KeyError(ticker)


def load_universe(
    path: Path | str = UNIVERSE_PATH,
    *,
    expected_universe_sha256: str = UNIVERSE_SHA256,
    source_csv_path: Path | str | None = SOURCE_CSV_PATH,
    expected_source_sha256: str = SOURCE_CSV_SHA256,
    verify_source: bool = True,
) -> Universe:
    """Read the frozen universe read-only and verify every pinned invariant."""
    path = Path(path)
    if not path.is_file():
        raise ProvenanceError(f"universe file not found: {path}")

    actual = sha256_file(path)
    if actual != expected_universe_sha256:
        raise ProvenanceError(
            "universe SHA-256 mismatch — the frozen universe has been altered: "
            f"expected {expected_universe_sha256}, got {actual}"
        )

    try:
        raw = json.loads(path.read_text())
    except json.JSONDecodeError as exc:  # pragma: no cover - sha guard fires first
        raise ProvenanceError(f"universe JSON is unparseable: {exc}") from exc

    if raw.get("universe_version") != UNIVERSE_VERSION:
        raise ProvenanceError(
            f"universe_version mismatch: expected {UNIVERSE_VERSION}, "
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
        raise ProvenanceError("declared setup_candidates does not match the frozen count")
    if raw.get("cohort_counts") != EXPECTED_COHORT_COUNTS:
        raise ProvenanceError(
            f"cohort_counts mismatch: expected {EXPECTED_COHORT_COUNTS}, "
            f"got {raw.get('cohort_counts')}"
        )

    entries = tuple(
        UniverseEntry(
            ticker=e["ticker"], cohort=e["cohort"], role=e["role"],
            sector=e.get("sector", ""),
        )
        for e in raw_entries
    )

    seen: set[str] = set()
    for entry in entries:
        if entry.ticker in seen:
            raise ProvenanceError(f"duplicate ticker in universe: {entry.ticker}")
        seen.add(entry.ticker)

    observed: dict[str, int] = {}
    for entry in entries:
        observed[entry.cohort] = observed.get(entry.cohort, 0) + 1
    if observed != EXPECTED_COHORT_COUNTS:
        raise ProvenanceError(
            f"observed cohort membership {observed} does not match the frozen "
            f"counts {EXPECTED_COHORT_COUNTS}"
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
        universe_sha256=actual,
        source_sha256=source_sha,
        raw=raw,
    )


def verify_preregistration_docs(
    *, v1_path: Path | str = PREREG_V1_PATH, v2_path: Path | str = PREREG_V2_PATH
) -> dict[str, str]:
    """v1 is frozen history and is pinned; v2 is binding and is recorded.

    v2's hash is recorded rather than pinned (a document cannot pin its own hash),
    but it enters the provenance set, so editing v2 invalidates every checkpoint.
    """
    v1_path, v2_path = Path(v1_path), Path(v2_path)
    if not v1_path.is_file():
        raise ProvenanceError(f"v1 preregistration not found: {v1_path}")
    if not v2_path.is_file():
        raise ProvenanceError(f"v2 preregistration not found: {v2_path}")

    v1_sha = sha256_file(v1_path)
    if v1_sha != PREREG_V1_SHA256:
        raise ProvenanceError(
            "v1 preregistration SHA-256 mismatch — superseded history must stay "
            f"unmodified: expected {PREREG_V1_SHA256}, got {v1_sha}"
        )
    return {"preregistration_v1_sha256": v1_sha,
            "preregistration_v2_sha256": sha256_file(v2_path)}


# ---------------------------------------------------------------------------
# Deterministic request manifest  (logical only — see D-3)
# ---------------------------------------------------------------------------


def quarter_slices(
    start: date = WINDOW_START, end: date = WINDOW_END
) -> list[tuple[date, date]]:
    """Split the frozen window into calendar-quarter slices, clipped to bounds.

    Deterministic and provider-independent. This fixes the count of **logical**
    requests. It says NOTHING about how many physical pages each one takes: the
    provider's row limit bounds base aggregates queried, not aggregated bars
    returned, and a quarter of 5-minute bars spans ~60,480 one-minute base
    aggregates. Physical completion is proven by evidence, never by arithmetic.
    """
    out: list[tuple[date, date]] = []
    cursor = start
    while cursor <= end:
        q_end_month = ((cursor.month - 1) // 3 + 1) * 3
        q_end = (
            date(cursor.year, 12, 31) if q_end_month == 12
            else date(cursor.year, q_end_month + 1, 1) - timedelta(days=1)
        )
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
    """Stable ID over exactly the fields that define a logical request."""
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
    """Full logical-request manifest for all universe entries, deterministically."""
    slices = quarter_slices(window_start, window_end)
    entries = {e.ticker: e for e in universe.entries}

    requests: list[dict[str, Any]] = []
    per_symbol: dict[str, list[str]] = {}
    for ticker in sorted(entries):
        entry = entries[ticker]
        ids: list[str] = []
        for slice_start, slice_end in slices:
            rid = request_id(
                symbol=entry.provider_ticker, endpoint=ENDPOINT,
                adjustment=ADJUSTMENT, timeframe=TIMEFRAME,
                slice_start=slice_start, slice_end=slice_end,
            )
            ids.append(rid)
            requests.append(
                {
                    "request_id": rid,
                    "ticker": entry.ticker,
                    "provider_ticker": entry.provider_ticker,
                    "cohort": entry.cohort,
                    "role": entry.role,
                    "coverage_policy": policy_for(entry.cohort).name,
                    "endpoint": ENDPOINT,
                    "adjustment": ADJUSTMENT,
                    "timeframe": TIMEFRAME,
                    "slice_start": slice_start.isoformat(),
                    "slice_end": slice_end.isoformat(),
                }
            )
        per_symbol[ticker] = ids

    return {
        "schema": MANIFEST_SCHEMA,
        "corpus_version": CORPUS_VERSION,
        "supersedes": SUPERSEDED_VERSION,
        "universe_version": universe.version,
        "universe_sha256": universe.universe_sha256,
        "timezone": TIMEZONE,
        "window": {"start": window_start.isoformat(), "end": window_end.isoformat()},
        "endpoint": ENDPOINT,
        "adjustment": ADJUSTMENT,
        "timeframe": TIMEFRAME,
        "slice_rule": "calendar_quarter_clipped_to_window",
        "slices": [[s.isoformat(), e.isoformat()] for s, e in slices],
        "pagination": (
            "logical requests only; physical page count is provider-determined "
            "and must be proven by recorded fetch evidence"
        ),
        "coverage_policies": policies_as_dict(),
        "symbol_count": len(per_symbol),
        "logical_request_count": len(requests),
        "logical_requests_per_symbol": len(slices),
        "request_ids_by_symbol": per_symbol,
        "requests": requests,
    }


def manifest_sha256(manifest: dict[str, Any]) -> str:
    return sha256_obj(manifest)


def window_sha256(
    window_start: date = WINDOW_START, window_end: date = WINDOW_END
) -> str:
    return sha256_obj(
        {"start": window_start.isoformat(), "end": window_end.isoformat(),
         "timezone": TIMEZONE}
    )


def config_sha256(
    *, extra_holidays: Sequence[date] = (), doc_hashes: dict[str, str] | None = None
) -> str:
    """Hash of the validation configuration. Environment paths are excluded."""
    return sha256_obj(
        {
            "adjustment": ADJUSTMENT,
            "bar_interval_minutes": BAR_INTERVAL_MINUTES,
            "corpus_version": CORPUS_VERSION,
            "coverage_policies": policies_as_dict(),
            "early_closes": sorted(d.isoformat() for d in EARLY_CLOSES),
            "endpoint": ENDPOINT,
            "evidence_schema": EVIDENCE_SCHEMA,
            "extra_holidays": sorted(d.isoformat() for d in extra_holidays),
            "holidays": sorted(d.isoformat() for d in MARKET_HOLIDAYS),
            "preregistration": doc_hashes or {},
            "rth_exceptions": RTH_EXCEPTIONS,
            "session_bounds": {
                "premarket_open": PREMARKET_OPEN_MIN,
                "rth_open": RTH_OPEN_MIN,
                "rth_close": RTH_CLOSE_MIN,
                "extended_close": EXT_CLOSE_MIN,
                "early_rth_close": EARLY_RTH_CLOSE_MIN,
                "early_extended_close": EARLY_EXT_CLOSE_MIN,
            },
            "session_model": "calendar_aware_v2",
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
    doc_hashes: dict[str, str] | None = None,
) -> dict[str, str]:
    """The five run hashes every checkpoint must match to be resumable."""
    return {
        "manifest_sha256": manifest_sha256(manifest),
        "universe_sha256": universe.universe_sha256,
        "code_sha256": code_sha256(),
        "window_sha256": window_sha256(),
        "config_sha256": config_sha256(
            extra_holidays=extra_holidays, doc_hashes=doc_hashes
        ),
    }


# ---------------------------------------------------------------------------
# Bar loading + byte digest  (corrects D-4)
# ---------------------------------------------------------------------------

REQUIRED_BAR_KEYS = ("t", "o", "h", "l", "c")


def load_bars_jsonl(path: Path | str) -> list[dict[str, Any]]:
    """Read a JSONL bar file strictly. A truncated final line is a hard error."""
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


@dataclass(frozen=True)
class BarFileDigest:
    """Everything needed to prove the resumed bytes are the checkpointed bytes.

    sha256 alone would catch every case; size, row count, and endpoints are kept
    because a mismatch report that says "row_count 96384 -> 96383" is far more
    actionable than "sha256 differs".
    """

    sha256: str
    size_bytes: int
    row_count: int
    first_ts: int | None
    last_ts: int | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "row_count": self.row_count,
            "first_ts": self.first_ts,
            "last_ts": self.last_ts,
        }


def digest_bar_file(path: Path | str, bars: Sequence[dict[str, Any]]) -> BarFileDigest:
    path = Path(path)
    timestamps = [b["t"] for b in bars if isinstance(b.get("t"), int)]
    return BarFileDigest(
        sha256=sha256_file(path),
        size_bytes=path.stat().st_size,
        row_count=len(bars),
        first_ts=min(timestamps) if timestamps else None,
        last_ts=max(timestamps) if timestamps else None,
    )


# ---------------------------------------------------------------------------
# Fetch evidence  (v2 §5 — corrects D-3)
# ---------------------------------------------------------------------------


def load_evidence(path: Path | str) -> dict[str, Any]:
    path = Path(path)
    if not path.is_file():
        raise EvidenceFileError(f"fetch-evidence file not found: {path}")
    raw = path.read_text()
    if raw and not raw.rstrip().endswith(("}", "]")):
        raise EvidenceFileError(
            f"fetch-evidence file {path.name} is truncated (partial write)"
        )
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise EvidenceFileError(
            f"fetch-evidence file {path.name} is not valid JSON: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise EvidenceFileError(f"fetch-evidence file {path.name} is not an object")
    return payload


@dataclass(frozen=True)
class Finding:
    code: str
    detail: str
    count: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "detail": self.detail, "count": self.count}


def validate_fetch_evidence(
    ticker: str,
    evidence: dict[str, Any],
    *,
    request_ids: Sequence[str],
    manifest_requests: Sequence[dict[str, Any]],
    bars: Sequence[dict[str, Any]],
) -> list[Finding]:
    """Prove every logical request was paginated to exhaustion.

    Nothing here assumes a page count. It checks that whatever pagination the
    provider imposed was followed to the end and recorded consistently.
    """
    findings: list[Finding] = []

    if evidence.get("schema") != EVIDENCE_SCHEMA:
        return [Finding("EVIDENCE_SCHEMA_MISMATCH",
                        f"schema is {evidence.get('schema')!r}, expected "
                        f"{EVIDENCE_SCHEMA!r}")]
    if evidence.get("corpus_version") != CORPUS_VERSION:
        findings.append(Finding(
            "EVIDENCE_CORPUS_VERSION_MISMATCH",
            f"evidence declares {evidence.get('corpus_version')!r}, "
            f"expected {CORPUS_VERSION!r}",
        ))
    if evidence.get("symbol") != ticker:
        findings.append(Finding(
            "EVIDENCE_SYMBOL_MISMATCH",
            f"evidence declares symbol {evidence.get('symbol')!r}, expected {ticker!r}",
        ))

    requests = evidence.get("requests")
    if not isinstance(requests, list):
        return findings + [Finding("EVIDENCE_MALFORMED", "'requests' is not a list")]

    recorded_ids = [r.get("request_id") for r in requests]
    if recorded_ids != list(request_ids):
        findings.append(Finding(
            "EVIDENCE_REQUEST_SET_MISMATCH",
            f"evidence covers {len(recorded_ids)} request(s) that do not match the "
            f"manifest's {len(request_ids)} in content or order",
        ))
        return findings

    slice_by_id = {r["request_id"]: r for r in manifest_requests}
    bars_by_day: dict[date, int] = {}
    for bar in bars:
        ts = bar.get("t")
        if isinstance(ts, int):
            day = _et_datetime(ts).date()
            bars_by_day[day] = bars_by_day.get(day, 0) + 1

    all_provider_ids: list[str] = []
    for req in requests:
        rid = req["request_id"]
        pages = req.get("pages")
        if not isinstance(pages, list) or not pages:
            findings.append(Finding("EVIDENCE_NO_PAGES",
                                    f"request {rid} records no pages"))
            continue

        if req.get("page_count") != len(pages):
            findings.append(Finding(
                "PAGE_COUNT_MISMATCH",
                f"request {rid} declares page_count={req.get('page_count')} but "
                f"records {len(pages)} page(s)",
            ))

        indices = [p.get("page_index") for p in pages]
        if indices != list(range(len(pages))):
            findings.append(Finding(
                "PAGE_INDEX_GAP",
                f"request {rid} page indices are {indices}, expected "
                f"{list(range(len(pages)))} — a page was dropped or reordered",
            ))

        # next_url must be present on every page but the last, and absent on the
        # last. Anything else means pagination stopped early or ran past the end.
        for i, page in enumerate(pages):
            present = page.get("next_url_present")
            if present is None:
                findings.append(Finding(
                    "MISSING_NEXT_URL_FLAG",
                    f"request {rid} page {i} does not record next_url_present",
                ))
            elif i < len(pages) - 1 and present is not True:
                findings.append(Finding(
                    "NEXT_URL_NOT_EXHAUSTED",
                    f"request {rid} page {i} reports no next_url but is followed "
                    f"by {len(pages) - 1 - i} more page(s)",
                ))
            elif i == len(pages) - 1 and present is not False:
                findings.append(Finding(
                    "NEXT_URL_NOT_EXHAUSTED",
                    f"request {rid} final page {i} still reports a next_url — "
                    "pagination was abandoned before exhaustion",
                ))

        if req.get("next_url_exhausted") is not True:
            findings.append(Finding(
                "NEXT_URL_NOT_EXHAUSTED",
                f"request {rid} does not declare next_url_exhausted=true",
            ))
        if req.get("complete") is not True:
            findings.append(Finding(
                "INCOMPLETE_REQUEST",
                f"request {rid} has no completion marker — the completion marker "
                "may only be written after the final page",
            ))

        for i, page in enumerate(pages):
            pid = page.get("provider_request_id")
            if not pid:
                findings.append(Finding(
                    "MISSING_PROVIDER_REQUEST_ID",
                    f"request {rid} page {i} records no provider request id",
                ))
            else:
                all_provider_ids.append(pid)
            for key in ("query_count", "results_count"):
                value = page.get(key)
                if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                    findings.append(Finding(
                        "MISSING_PROVIDER_COUNTS",
                        f"request {rid} page {i} has invalid {key}={value!r}",
                    ))

        # Page ranges must ascend and never overlap.
        ranges = [(p.get("first_ts"), p.get("last_ts")) for p in pages]
        if all(isinstance(a, int) and isinstance(b, int) for a, b in ranges):
            for i, (lo, hi) in enumerate(ranges):
                if lo > hi:
                    findings.append(Finding(
                        "PAGE_RANGE_INVERTED",
                        f"request {rid} page {i} has first_ts > last_ts",
                    ))
            for i, ((_, hi), (lo2, _)) in enumerate(zip(ranges, ranges[1:])):
                if lo2 <= hi:
                    findings.append(Finding(
                        "OVERLAPPING_PAGES",
                        f"request {rid} pages {i} and {i + 1} overlap or repeat "
                        f"(page {i} ends {hi}, page {i + 1} starts {lo2})",
                    ))
            declared_first, declared_last = req.get("first_ts"), req.get("last_ts")
            if declared_first != ranges[0][0] or declared_last != ranges[-1][1]:
                findings.append(Finding(
                    "EVIDENCE_TIMESTAMP_MISMATCH",
                    f"request {rid} declares span "
                    f"[{declared_first}, {declared_last}] but its pages span "
                    f"[{ranges[0][0]}, {ranges[-1][1]}]",
                ))

        # The evidence must account for exactly the bars on disk in this slice.
        slice_meta = slice_by_id.get(rid)
        if slice_meta:
            lo = date.fromisoformat(slice_meta["slice_start"])
            hi = date.fromisoformat(slice_meta["slice_end"])
            on_disk = sum(n for d, n in bars_by_day.items() if lo <= d <= hi)
            recorded = sum(
                p.get("results_count", 0) for p in pages
                if isinstance(p.get("results_count"), int)
            )
            if recorded != on_disk:
                findings.append(Finding(
                    "EVIDENCE_BAR_COUNT_MISMATCH",
                    f"request {rid} ({lo}..{hi}) records {recorded} result(s) but "
                    f"{on_disk} bar(s) are on disk for that slice",
                ))

    dupes = [p for p in set(all_provider_ids) if all_provider_ids.count(p) > 1]
    if dupes:
        findings.append(Finding(
            "DUPLICATE_PAGE",
            f"{len(dupes)} provider request id(s) appear on more than one page: "
            + _describe(dupes),
            len(dupes),
        ))

    return findings


# ---------------------------------------------------------------------------
# Coverage + bar validation
# ---------------------------------------------------------------------------


@dataclass
class SymbolResult:
    ticker: str
    status: str                      # PASS | FAIL
    cohort: str = ""
    policy: str = ""
    bar_count: int = 0
    session_count: int = 0
    rth_bar_count: int = 0
    distinct_utc_offsets: tuple[str, ...] = ()
    earliest: str | None = None
    latest: str | None = None
    coverage_totals: dict[str, dict[str, int]] = field(default_factory=dict)
    coverage_sha256: str = ""
    findings: list[Finding] = field(default_factory=list)
    resumed: bool = False

    @property
    def passed(self) -> bool:
        return self.status == "PASS"

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "status": self.status,
            "cohort": self.cohort,
            "policy": self.policy,
            "bar_count": self.bar_count,
            "session_count": self.session_count,
            "rth_bar_count": self.rth_bar_count,
            "distinct_utc_offsets": list(self.distinct_utc_offsets),
            "earliest": self.earliest,
            "latest": self.latest,
            "coverage_totals": self.coverage_totals,
            "coverage_sha256": self.coverage_sha256,
            "resumed": self.resumed,
            "findings": [f.to_dict() for f in self.findings],
        }


def compute_coverage(
    bars: Sequence[dict[str, Any]],
    *,
    window_start: date = WINDOW_START,
    window_end: date = WINDOW_END,
    extra_holidays: Sequence[date] = (),
) -> tuple[list[dict[str, Any]], dict[str, dict[str, int]]]:
    """Per-session, per-tag expected / observed / missing / duplicate counts.

    This is the actual bar-coverage computation v1 never had: it compares the
    observed timestamps against the expected five-minute grid, so an isolated
    hole inside a session is visible.
    """
    observed_by_day_tag: dict[tuple[date, str], list[int]] = {}
    for bar in bars:
        ts = bar.get("t")
        if not isinstance(ts, int) or isinstance(ts, bool):
            continue
        dt = _et_datetime(ts)
        tag = session_tag(ts)
        if tag is None:
            continue
        observed_by_day_tag.setdefault((dt.date(), tag), []).append(ts)

    holidays = set(extra_holidays)
    sessions = [
        d for d in expected_sessions(window_start, window_end) if d not in holidays
    ]

    rows: list[dict[str, Any]] = []
    totals: dict[str, dict[str, int]] = {
        tag: {"expected": 0, "observed": 0, "missing": 0, "duplicate": 0}
        for tag in SESSION_TAGS
    }

    for day in sessions:
        for tag in SESSION_TAGS:
            grid = expected_grid(day, tag)
            observed = observed_by_day_tag.get((day, tag), [])
            unique = set(observed)
            missing = [t for t in grid if t not in unique]
            duplicate = len(observed) - len(unique)
            row = {
                "date": day.isoformat(),
                "tag": tag,
                "expected": len(grid),
                "observed": len(unique),
                "missing": len(missing),
                "duplicate": duplicate,
            }
            rows.append(row)
            totals[tag]["expected"] += len(grid)
            totals[tag]["observed"] += len(unique)
            totals[tag]["missing"] += len(missing)
            totals[tag]["duplicate"] += duplicate

    return rows, totals


def coverage_sha256(rows: Sequence[dict[str, Any]]) -> str:
    return sha256_obj(list(rows))


def _missing_rth_details(
    bars: Sequence[dict[str, Any]],
    *,
    window_start: date,
    window_end: date,
    extra_holidays: Sequence[date],
) -> list[str]:
    """Human-readable ET timestamps of every missing RTH interval (exact list)."""
    observed: set[int] = {
        b["t"] for b in bars
        if isinstance(b.get("t"), int) and session_tag(b["t"]) == "RTH"
    }
    holidays = set(extra_holidays)
    out: list[str] = []
    for day in expected_sessions(window_start, window_end):
        if day in holidays:
            continue
        for ts in expected_grid(day, "RTH"):
            if ts not in observed:
                out.append(_et_datetime(ts).isoformat())
    return out


def validate_symbol_bars(
    ticker: str,
    bars: Sequence[dict[str, Any]],
    *,
    policy: CoveragePolicy = POLICY_EQUITY,
    window_start: date = WINDOW_START,
    window_end: date = WINDOW_END,
    extra_holidays: Sequence[date] = (),
    require_dst: bool = True,
) -> SymbolResult:
    """Run every gate over one symbol's 5-minute bars. No warning tier."""
    findings: list[Finding] = []

    if not bars:
        return SymbolResult(ticker=ticker, status="FAIL", policy=policy.name,
                            findings=[Finding("NO_BARS", "symbol has zero bars")])

    timestamps = [b["t"] for b in bars]
    non_int = [t for t in timestamps if not isinstance(t, int) or isinstance(t, bool)]
    if non_int:
        return SymbolResult(
            ticker=ticker, status="FAIL", policy=policy.name, bar_count=len(bars),
            findings=[Finding("NON_INTEGER_TIMESTAMP",
                              f"non-integer epoch-ms timestamps: {_describe(non_int)}",
                              len(non_int))],
        )

    # --- duplicates -------------------------------------------------------
    seen: set[int] = set()
    dupes: list[int] = []
    for t in timestamps:
        if t in seen:
            dupes.append(t)
        seen.add(t)
    if dupes:
        findings.append(Finding(
            "DUPLICATE_TIMESTAMP",
            f"{len(dupes)} duplicate timestamp(s): "
            + _describe(_et_datetime(t).isoformat() for t in dupes),
            len(dupes),
        ))

    # --- ordering ---------------------------------------------------------
    out_of_order = [(a, b) for a, b in zip(timestamps, timestamps[1:]) if b <= a]
    if out_of_order:
        findings.append(Finding(
            "OUT_OF_ORDER",
            f"{len(out_of_order)} non-monotonic transition(s): "
            + _describe(f"{_et_datetime(a).isoformat()} -> {_et_datetime(b).isoformat()}"
                        for a, b in out_of_order),
            len(out_of_order),
        ))

    # --- 5-minute alignment ----------------------------------------------
    misaligned = [t for t in timestamps if t % BAR_INTERVAL_MS != 0]
    if misaligned:
        findings.append(Finding(
            "MISALIGNED_INTERVAL",
            f"{len(misaligned)} bar(s) off the {BAR_INTERVAL_MINUTES}-minute grid: "
            + _describe(_et_datetime(t).isoformat() for t in misaligned),
            len(misaligned),
        ))

    # --- window bounds / session hours ------------------------------------
    out_of_window: list[int] = []
    outside_session: list[int] = []
    for t in timestamps:
        dt = _et_datetime(t)
        if not (window_start <= dt.date() <= window_end):
            out_of_window.append(t)
        elif session_tag(t) is None:
            outside_session.append(t)

    if out_of_window:
        findings.append(Finding(
            "OUT_OF_WINDOW",
            f"{len(out_of_window)} bar(s) outside the frozen window "
            f"{window_start}..{window_end}: "
            + _describe(_et_datetime(t).isoformat() for t in out_of_window),
            len(out_of_window),
        ))
    if outside_session:
        findings.append(Finding(
            "OUTSIDE_SESSION_HOURS",
            f"{len(outside_session)} bar(s) outside the day's calendar-aware "
            "session bounds (half-day close is 17:00, not 20:00): "
            + _describe(_et_datetime(t).isoformat() for t in outside_session),
            len(outside_session),
        ))

    # --- session tags -----------------------------------------------------
    bad_tags: list[str] = []
    for bar in bars:
        expected_tag = session_tag(bar["t"])
        actual = bar.get("session")
        stamp = _et_datetime(bar["t"]).isoformat()
        if actual is None:
            bad_tags.append(f"{stamp}: missing")
        elif actual not in SESSION_TAGS:
            bad_tags.append(f"{stamp}: {actual!r}")
        elif expected_tag is not None and actual != expected_tag:
            bad_tags.append(f"{stamp}: {actual} != recomputed {expected_tag}")
    if bad_tags:
        findings.append(Finding(
            "INVALID_SESSION_TAG",
            f"{len(bad_tags)} bar(s) with a missing, unknown, or disagreeing tag: "
            + _describe(bad_tags),
            len(bad_tags),
        ))

    # --- DST --------------------------------------------------------------
    offsets = sorted({str(_et_datetime(t).utcoffset()) for t in timestamps})
    if require_dst and len(offsets) < 2:
        findings.append(Finding(
            "DST_NOT_OBSERVED",
            f"only one distinct UTC offset across the window ({offsets}) — "
            "timestamps look built from a fixed offset, not the zone",
        ))

    # --- session presence -------------------------------------------------
    covered = {_et_datetime(t).date() for t in timestamps}
    holidays = set(extra_holidays)
    expected_days = [
        d for d in expected_sessions(window_start, window_end) if d not in holidays
    ]
    missing_days = [d for d in expected_days if d not in covered]
    extra_days = sorted(
        d for d in covered
        if window_start <= d <= window_end and not is_trading_session(d)
    )
    if missing_days:
        findings.append(Finding(
            "MISSING_SESSION",
            f"{len(missing_days)} expected trading session(s) with no bars: "
            + _describe(d.isoformat() for d in missing_days),
            len(missing_days),
        ))
    if extra_days:
        findings.append(Finding(
            "EXTRA_SESSION",
            f"{len(extra_days)} bar date(s) on a weekend or frozen holiday: "
            + _describe(d.isoformat() for d in extra_days),
            len(extra_days),
        ))

    # --- bar coverage against the expected grid  (v2 §4) ------------------
    rows, totals = compute_coverage(
        bars, window_start=window_start, window_end=window_end,
        extra_holidays=extra_holidays,
    )

    if policy.requires_full_grid("RTH") and totals["RTH"]["missing"]:
        detail_list = _missing_rth_details(
            bars, window_start=window_start, window_end=window_end,
            extra_holidays=extra_holidays,
        )
        unexcused = [d for d in detail_list if d[:10] not in RTH_EXCEPTIONS]
        if unexcused:
            findings.append(Finding(
                "MISSING_RTH_INTERVAL",
                f"{len(unexcused)} missing RTH 5-minute interval(s) with no frozen "
                f"halt/calendar exception: {_describe(unexcused)}",
                len(unexcused),
            ))

    for tag in ("PREMARKET", "AFTER_HOURS"):
        if policy.requires_full_grid(tag) and totals[tag]["missing"]:
            findings.append(Finding(
                f"MISSING_{tag}_INTERVAL",
                f"{totals[tag]['missing']} missing {tag} interval(s)",
                totals[tag]["missing"],
            ))

    return SymbolResult(
        ticker=ticker,
        status="FAIL" if findings else "PASS",
        policy=policy.name,
        bar_count=len(bars),
        session_count=len(covered),
        rth_bar_count=sum(1 for b in bars if session_tag(b["t"]) == "RTH"),
        distinct_utc_offsets=tuple(offsets),
        earliest=_et_datetime(min(timestamps)).isoformat(),
        latest=_et_datetime(max(timestamps)).isoformat(),
        coverage_totals=totals,
        coverage_sha256=coverage_sha256(rows),
        findings=findings,
    )


# ---------------------------------------------------------------------------
# Checkpoints  (v2 §6 — corrects D-4)
# ---------------------------------------------------------------------------


class CheckpointStore:
    """Per-symbol checkpoints, written atomically, verified against the bytes.

    Atomic write = temp file in the same directory, fsync, then os.replace. A
    process killed mid-write leaves either the previous complete checkpoint or
    none — never a half-written one.
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
        bar_digest: BarFileDigest,
        evidence_sha256: str,
        evidence_page_count: int,
    ) -> Path:
        """Persist a COMPLETE checkpoint. Failed symbols are never checkpointed."""
        if not result.passed:
            raise CheckpointError(
                f"refusing to checkpoint {result.ticker}: status={result.status}"
            )

        body = {
            "schema": CHECKPOINT_SCHEMA,
            "corpus_version": CORPUS_VERSION,
            "symbol": result.ticker,
            "status": STATUS_COMPLETE,
            "provenance": {k: provenance[k] for k in self.PROVENANCE_KEYS},
            "request_ids": list(request_ids),
            "bar_file": bar_digest.to_dict(),
            "coverage_sha256": result.coverage_sha256,
            "fetch_evidence": {
                "sha256": evidence_sha256,
                "page_count": evidence_page_count,
            },
            "summary": {
                "bar_count": result.bar_count,
                "session_count": result.session_count,
                "rth_bar_count": result.rth_bar_count,
                "distinct_utc_offsets": list(result.distinct_utc_offsets),
                "earliest": result.earliest,
                "latest": result.latest,
                "coverage_totals": result.coverage_totals,
                "policy": result.policy,
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
        """Return the payload, or None if absent. Corrupt is never None."""
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

        for key in ("schema", "symbol", "status", "provenance", "request_ids",
                    "bar_file", "coverage_sha256", "fetch_evidence"):
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
            raise CheckpointError(f"checkpoint {path.name} holds {payload['symbol']!r}")
        return payload

    def verify(
        self,
        ticker: str,
        *,
        provenance: dict[str, str],
        request_ids: Sequence[str],
        bar_digest: BarFileDigest,
        coverage_sha: str,
        evidence_sha256: str,
    ) -> dict[str, Any] | None:
        """Return the checkpoint only if complete AND matching the bytes on disk.

        The bar digest and coverage hash are recomputed by the caller from the
        file as it exists NOW. A checkpoint can never stand in for data that has
        been deleted, truncated, reordered, or edited since.
        """
        payload = self.read(ticker)
        if payload is None:
            return None

        if payload["status"] != STATUS_COMPLETE:
            raise CheckpointError(
                f"checkpoint for {ticker} is incomplete: status={payload['status']!r}"
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
                f"checkpoint for {ticker} covers a different request set than the "
                "current manifest"
            )

        recorded_bar = payload.get("bar_file") or {}
        current_bar = bar_digest.to_dict()
        for key in ("sha256", "size_bytes", "row_count", "first_ts", "last_ts"):
            if recorded_bar.get(key) != current_bar[key]:
                raise CheckpointError(
                    f"checkpoint for {ticker} does not match the bar file on disk: "
                    f"{key} {recorded_bar.get(key)!r} != {current_bar[key]!r}"
                )

        if payload.get("coverage_sha256") != coverage_sha:
            raise CheckpointError(
                f"checkpoint for {ticker} has a stale coverage hash — the bar "
                "file's session coverage has changed since it was written"
            )

        recorded_ev = (payload.get("fetch_evidence") or {}).get("sha256")
        if recorded_ev != evidence_sha256:
            raise CheckpointError(
                f"checkpoint for {ticker} does not match the fetch-evidence file "
                f"on disk: {recorded_ev!r} != {evidence_sha256!r}"
            )
        return payload


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def bar_file_for(bars_dir: Path | str, ticker: str) -> Path:
    return Path(bars_dir) / f"{ticker}_5m.jsonl"


def evidence_file_for(evidence_dir: Path | str, ticker: str) -> Path:
    return Path(evidence_dir) / f"{ticker}_fetch_evidence.json"


def _result_from_checkpoint(payload: dict[str, Any]) -> SymbolResult:
    summary = payload.get("summary") or {}
    return SymbolResult(
        ticker=payload["symbol"],
        status="PASS",
        policy=summary.get("policy", ""),
        bar_count=summary.get("bar_count", 0),
        session_count=summary.get("session_count", 0),
        rth_bar_count=summary.get("rth_bar_count", 0),
        distinct_utc_offsets=tuple(summary.get("distinct_utc_offsets") or ()),
        earliest=summary.get("earliest"),
        latest=summary.get("latest"),
        coverage_totals=summary.get("coverage_totals") or {},
        coverage_sha256=payload.get("coverage_sha256", ""),
        resumed=True,
    )


def run_preflight(
    *,
    bars_dir: Path | str,
    evidence_dir: Path | str,
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

    No network I/O. The report embeds no wall-clock time, so two identical runs
    produce byte-identical output.
    """
    universe = load_universe(
        universe_path, source_csv_path=source_csv_path, verify_source=verify_source
    )
    doc_hashes = verify_preregistration_docs()
    manifest = build_manifest(universe)
    provenance = build_provenance(
        manifest, universe, extra_holidays=extra_holidays, doc_hashes=doc_hashes
    )
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

    uncovered = [t for t in required if not manifest["request_ids_by_symbol"].get(t)]
    if uncovered:
        raise ProvenanceError(
            f"manifest has no requests planned for: {_describe(uncovered)}"
        )

    requests_by_symbol: dict[str, list[dict[str, Any]]] = {}
    for req in manifest["requests"]:
        requests_by_symbol.setdefault(req["ticker"], []).append(req)

    results: list[SymbolResult] = []
    for ticker in required:
        entry = universe.by_ticker(ticker)
        policy = policy_for(entry.cohort)
        request_ids = manifest["request_ids_by_symbol"][ticker]

        def fail(code: str, detail: str) -> None:
            results.append(SymbolResult(
                ticker=ticker, status="FAIL", cohort=entry.cohort,
                policy=policy.name, findings=[Finding(code, detail)],
            ))

        # Bars and evidence are ALWAYS read, resume or not — that is the whole
        # point of binding checkpoints to bytes.
        bar_path = bar_file_for(bars_dir, ticker)
        try:
            bars = load_bars_jsonl(bar_path)
        except BarFileError as exc:
            fail("BAR_FILE_UNUSABLE", str(exc))
            continue

        evidence_path = evidence_file_for(evidence_dir, ticker)
        try:
            evidence = load_evidence(evidence_path)
        except EvidenceFileError as exc:
            fail("EVIDENCE_FILE_UNUSABLE", str(exc))
            continue

        bar_digest = digest_bar_file(bar_path, bars)
        evidence_sha = sha256_file(evidence_path)
        rows, _ = compute_coverage(
            bars, extra_holidays=extra_holidays
        )
        coverage_sha = coverage_sha256(rows)

        if resume:
            try:
                payload = store.verify(
                    ticker, provenance=provenance, request_ids=request_ids,
                    bar_digest=bar_digest, coverage_sha=coverage_sha,
                    evidence_sha256=evidence_sha,
                )
            except CheckpointError as exc:
                fail("CHECKPOINT_REJECTED", str(exc))
                continue
            if payload is not None:
                resumed = _result_from_checkpoint(payload)
                resumed.cohort = entry.cohort
                results.append(resumed)
                continue

        evidence_findings = validate_fetch_evidence(
            ticker, evidence, request_ids=request_ids,
            manifest_requests=requests_by_symbol[ticker], bars=bars,
        )
        result = validate_symbol_bars(
            ticker, bars, policy=policy, extra_holidays=extra_holidays,
            require_dst=require_dst,
        )
        result.cohort = entry.cohort
        if evidence_findings:
            result.findings = [*evidence_findings, *result.findings]
            result.status = "FAIL"

        if result.passed:
            page_count = sum(
                len(r.get("pages") or []) for r in evidence.get("requests", [])
            )
            store.write(
                result, provenance=provenance, request_ids=request_ids,
                bar_digest=bar_digest, evidence_sha256=evidence_sha,
                evidence_page_count=page_count,
            )
        results.append(result)

    return build_report(
        results, manifest=manifest, universe=universe, provenance=provenance,
        required=required, doc_hashes=doc_hashes,
    )


def build_report(
    results: Sequence[SymbolResult],
    *,
    manifest: dict[str, Any],
    universe: Universe,
    provenance: dict[str, str],
    required: Sequence[str],
    doc_hashes: dict[str, str],
) -> dict[str, Any]:
    """Assemble the fail-closed completeness report. No partial-credit state."""
    by_ticker = {r.ticker: r for r in results}
    missing_results = [t for t in required if t not in by_ticker]
    failed = sorted(t for t, r in by_ticker.items() if not r.passed)
    passed = sorted(t for t, r in by_ticker.items() if r.passed)

    findings_by_code: dict[str, int] = {}
    for r in results:
        for f in r.findings:
            findings_by_code[f.code] = findings_by_code.get(f.code, 0) + f.count

    complete = not missing_results and not failed and len(passed) == len(required)

    return {
        "schema": REPORT_SCHEMA,
        "corpus_version": CORPUS_VERSION,
        "supersedes": SUPERSEDED_VERSION,
        "universe_version": universe.version,
        "status": "PASS" if complete else "FAIL",
        "exit_code": 0 if complete else 1,
        "authorizes_corpus_batch": False,
        "provenance": {**provenance, **doc_hashes},
        "manifest": {
            "sha256": provenance["manifest_sha256"],
            "symbol_count": manifest["symbol_count"],
            "logical_request_count": manifest["logical_request_count"],
            "logical_requests_per_symbol": manifest["logical_requests_per_symbol"],
            "pagination": manifest["pagination"],
            "slice_rule": manifest["slice_rule"],
            "window": manifest["window"],
            "endpoint": manifest["endpoint"],
            "adjustment": manifest["adjustment"],
            "timeframe": manifest["timeframe"],
        },
        "coverage_policies": policies_as_dict(),
        "rth_exceptions": RTH_EXCEPTIONS,
        "counts": {
            "universe_entries": len(universe.entries),
            "required": len(required),
            "passed": len(passed),
            "failed": len(failed),
            "no_result": len(missing_results),
            "resumed": sum(1 for r in results if r.resumed),
            "expected_sessions": len(expected_sessions()),
            "early_close_sessions": len(EARLY_CLOSES),
        },
        "failed_symbols": failed,
        "symbols_without_result": missing_results,
        "findings_by_code": dict(sorted(findings_by_code.items())),
        "symbols": {t: by_ticker[t].to_dict() for t in sorted(by_ticker)},
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Equity corpus v2 batch-runner preflight. No network, no fetch, no "
            "production code. Exits non-zero unless every required symbol passes."
        )
    )
    p.add_argument("--bars-dir", default="data/equity_corpus_v2/bars")
    p.add_argument("--evidence-dir", default="data/equity_corpus_v2/evidence")
    p.add_argument("--checkpoint-dir", default="data/equity_corpus_v2/checkpoints")
    p.add_argument("--universe", default=str(UNIVERSE_PATH))
    p.add_argument("--report", default=None, help="write the JSON report here")
    p.add_argument("--manifest-out", default=None, help="write the manifest here")
    p.add_argument("--symbols", default=None,
                   help="comma-separated subset of the frozen universe")
    p.add_argument("--no-resume", action="store_true")
    p.add_argument("--extra-holidays", default="",
                   help="comma-separated extra ISO closure dates; enters config_sha256")
    p.add_argument("--allow-single-utc-offset", action="store_true",
                   help="skip the DST gate (only valid for sub-DST-cycle fixtures)")
    return p.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)

    extra_holidays = tuple(
        date.fromisoformat(d.strip())
        for d in args.extra_holidays.split(",") if d.strip()
    )
    symbols = (
        [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        if args.symbols else None
    )

    try:
        report = run_preflight(
            bars_dir=args.bars_dir,
            evidence_dir=args.evidence_dir,
            checkpoint_dir=args.checkpoint_dir,
            universe_path=args.universe,
            symbols=symbols,
            resume=not args.no_resume,
            extra_holidays=extra_holidays,
            require_dst=not args.allow_single_utc_offset,
        )
    except PreflightError as exc:
        print(f"PREFLIGHT ABORTED — {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    if args.manifest_out:
        manifest = build_manifest(load_universe(args.universe))
        out = Path(args.manifest_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(canonical_json(manifest) + "\n")

    if args.report:
        out = Path(args.report)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")

    counts = report["counts"]
    print(f"PREFLIGHT {report['status']}  ({CORPUS_VERSION}, supersedes "
          f"{SUPERSEDED_VERSION})")
    print(f"  manifest sha256      {report['provenance']['manifest_sha256']}")
    print(f"  logical requests     {report['manifest']['logical_request_count']} "
          f"({report['manifest']['logical_requests_per_symbol']}/symbol) — "
          "physical page count is NOT implied")
    print(f"  required symbols     {counts['required']}")
    print(f"  passed / failed      {counts['passed']} / {counts['failed']}")
    print(f"  resumed              {counts['resumed']}")
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
