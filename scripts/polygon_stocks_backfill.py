"""scripts/polygon_stocks_backfill.py

One-time, read-only Polygon historical backfill for the `stocks_advisory`
QQQ -> TQQQ/SQQQ backtest. Pulls 5-minute aggregate bars for QQQ, TQQQ, and
SQQQ and writes CSVs in the exact shape `stocks_advisory/csv_loader.py`
already consumes (`timestamp,open,high,low,close,volume`, ISO-8601
timestamps with an America/New_York offset, ascending, deduplicated).

Not a production adapter and not imported by any live/backtest code path.
Run once, inspect the printed validation report + manifest, then feed the
output CSVs into `stocks_advisory/tqqq_sqqq_backtest.py` by hand.

Data-source decision (operator-specified): Polygon's consolidated-tape
bars are the authoritative dataset. The BATS/TradingView export files
already in the repo root are used ONLY as an independent overlap sanity
check -- never merged or spliced into the Polygon series.

Scope: this script and its own imports only. Does not import
`sources.polygon_client` (futures, nanosecond timestamps, different
endpoint) or `options_manager.adapters.polygon_historical` (different
lane, different guarantees) -- reuses their retry/pagination *design*,
not the modules themselves. No futures, options, broker, execution,
strategy, risk, config, or webhook imports.

Fail-closed: a missing API key raises before any request is made. A
malformed bar row is skipped, never fabricated or forward-filled. Two
bars sharing a timestamp with *different* OHLCV values is a conflict and
fails the run rather than silently picking one. The API key is read only
from the POLYGON_API_KEY environment variable and never appears in a
log line, exception message, repr, filename, CSV, or the manifest.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import statistics
import subprocess
import sys
import time as _time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

import httpx

DEFAULT_BASE_URL = "https://api.polygon.io"
ET = ZoneInfo("America/New_York")
RTH_START = (9, 30)
RTH_END = (16, 0)

DEFAULT_SYMBOLS = ("QQQ", "TQQQ", "SQQQ")
DEFAULT_MULTIPLIER = 5
DEFAULT_TIMESPAN = "minute"
DEFAULT_MONTHS_BACK = 18

CSV_HEADER = ("timestamp", "open", "high", "low", "close", "volume")

# Overlap sanity-check thresholds. Polygon is consolidated-tape (all
# exchanges); the uploaded BATS_* exports are BATS-only. A few basis
# points of drift between the two is expected and NOT a bug -- these
# thresholds exist to catch a real defect class (timestamp misalignment,
# a timezone-conversion error, an adjustment-setting mismatch), not to
# demand tick-for-tick equality between two different venues.
MAX_ACCEPTABLE_MEDIAN_BPS = 20.0
MAX_ACCEPTABLE_MAX_BPS = 500.0
MIN_ACCEPTABLE_CLOSE_CORRELATION = 0.999


class PolygonBackfillError(RuntimeError):
    """Any failure fetching or validating the backfill. Never includes
    the API key in its message."""


class ConflictingDuplicateTimestamp(PolygonBackfillError):
    """Two bars share a timestamp but disagree on OHLCV values."""


@dataclass(frozen=True)
class Bar5m:
    timestamp: str  # ISO-8601, America/New_York offset
    open: float
    high: float
    low: float
    close: float
    volume: int

    def as_row(self) -> tuple:
        return (self.timestamp, self.open, self.high, self.low, self.close, self.volume)


# ── HTTP client (design mirrors sources/polygon_client.py and
#    options_manager/adapters/polygon_historical.py -- not imported from
#    either, since neither fits this endpoint/timeframe/lane) ──────────────
class PolygonAggsClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: float = 30.0,
        client: Optional[httpx.Client] = None,
        max_retries: int = 5,
        retry_sleep_seconds: float = 15.0,
    ) -> None:
        self._api_key = (
            api_key if api_key is not None else os.getenv("POLYGON_API_KEY", "")
        ).strip()
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_sleep_seconds = retry_sleep_seconds
        self._client = client

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    def __repr__(self) -> str:
        return f"PolygonAggsClient(configured={self.configured})"

    def _get(self, client: httpx.Client, url: str, params: Optional[dict]) -> dict:
        headers = {"Authorization": f"Bearer {self._api_key}"}
        last_err: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            try:
                resp = client.get(url, params=params, headers=headers, timeout=self.timeout)
            except httpx.HTTPError as exc:
                last_err = exc
                if attempt < self.max_retries:
                    _time.sleep(self.retry_sleep_seconds)
                continue
            if resp.status_code == 429 and attempt < self.max_retries:
                try:
                    wait = float(resp.headers["Retry-After"])
                except (KeyError, TypeError, ValueError):
                    wait = self.retry_sleep_seconds * (attempt + 1)
                _time.sleep(max(1.0, wait))
                continue
            if resp.status_code != 200:
                raise PolygonBackfillError(
                    f"HTTP {resp.status_code} from Polygon aggregates request for {url.split('?')[0]}"
                )
            try:
                return resp.json()
            except ValueError as exc:
                raise PolygonBackfillError("non-JSON response from Polygon") from exc
        raise PolygonBackfillError(
            f"request failed after {self.max_retries + 1} attempts: {last_err}"
        )

    def fetch_all_bars(
        self,
        ticker: str,
        from_date: str,
        to_date: str,
        multiplier: int = DEFAULT_MULTIPLIER,
        timespan: str = DEFAULT_TIMESPAN,
        adjusted: bool = True,
        limit: int = 50_000,
    ) -> list[dict]:
        """Every raw result row for [from_date, to_date], following
        `next_url` until exhausted. Raw dicts -- caller maps/validates."""
        if not self.configured:
            raise PolygonBackfillError("POLYGON_API_KEY not configured")

        url = (
            f"{self.base_url}/v2/aggs/ticker/{ticker.upper()}/range/"
            f"{multiplier}/{timespan}/{from_date}/{to_date}"
        )
        params: Optional[dict] = {
            "adjusted": "true" if adjusted else "false",
            "sort": "asc",
            "limit": limit,
        }

        rows: list[dict] = []
        close_client = self._client is None
        client = self._client or httpx.Client()
        try:
            while url:
                payload = self._get(client, url, params=params)
                params = None  # next_url carries the full query string
                rows.extend(payload.get("results") or [])
                url = payload.get("next_url") or ""
        finally:
            if close_client:
                client.close()
        return rows


def _row_to_bar(row: dict[str, Any]) -> Optional[Bar5m]:
    """Maps one raw Polygon row to a Bar5m. Returns None (skip, never
    fabricate) on any missing/unparseable OHLC field."""
    try:
        t_ms = float(row["t"])
        open_ = float(row["o"])
        high = float(row["h"])
        low = float(row["l"])
        close = float(row["c"])
    except (KeyError, TypeError, ValueError):
        return None
    volume_raw = row.get("v")
    try:
        volume = int(volume_raw) if volume_raw is not None else 0
    except (TypeError, ValueError):
        volume = 0

    dt_et = datetime.fromtimestamp(t_ms / 1000.0, tz=timezone.utc).astimezone(ET)
    return Bar5m(
        timestamp=dt_et.isoformat(),
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )


def dedupe_bars(bars: list[Bar5m]) -> tuple[list[Bar5m], int, list[str]]:
    """Dedupes by timestamp. Two bars at the same timestamp with
    identical OHLCV are a plain duplicate (dropped, counted). Two bars
    at the same timestamp with DIFFERENT OHLCV are a conflict -- these
    are collected and raise `ConflictingDuplicateTimestamp` rather than
    silently keeping either one."""
    by_ts: dict[str, Bar5m] = {}
    duplicate_count = 0
    conflicts: list[str] = []
    for bar in bars:
        existing = by_ts.get(bar.timestamp)
        if existing is None:
            by_ts[bar.timestamp] = bar
            continue
        if existing.as_row() == bar.as_row():
            duplicate_count += 1
            continue
        conflicts.append(bar.timestamp)

    if conflicts:
        raise ConflictingDuplicateTimestamp(
            f"{len(conflicts)} timestamp(s) had conflicting OHLCV values "
            f"across duplicate rows: {conflicts[:10]}"
            + (" ... (truncated)" if len(conflicts) > 10 else "")
        )

    ordered = sorted(by_ts.values(), key=lambda b: datetime.fromisoformat(b.timestamp))
    return ordered, duplicate_count, conflicts


def write_csv(path: Path, bars: list[Bar5m]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(CSV_HEADER)
        for bar in bars:
            writer.writerow(bar.as_row())


def read_csv_bars(path: Path) -> list[Bar5m]:
    """Reads an already-written output CSV back into Bar5m objects, for
    --no-fetch manifest rebuilds -- never touches the file, never
    refetches, so the on-disk bytes (and their hash) are untouched."""
    bars: list[Bar5m] = []
    with path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            bars.append(
                Bar5m(
                    timestamp=row["timestamp"],
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=int(row["volume"]),
                )
            )
    return bars


def sha256_of_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def script_commit_status(repo_root: Path, script_path: Path) -> dict:
    """Reports whether HEAD's tree actually contains this exact script,
    rather than just returning `git rev-parse HEAD` unconditionally --
    a bare HEAD SHA is misleading when the script itself is uncommitted
    or has local edits, since that commit's tree doesn't contain it.

    Returns one of:
      {"status": "committed", "commit_sha": "<sha>"} -- the file is
        tracked and matches HEAD's blob for that path exactly.
      {"status": "uncommitted", "commit_sha": None, "head_sha_at_manifest_time": "<sha>"}
        -- the file is new or has local changes relative to HEAD.
      {"status": "unknown", "commit_sha": None} -- not a git repo, or
        the git calls failed for some other reason.
    """
    try:
        rel_path = str(script_path.resolve().relative_to(repo_root.resolve()))
    except ValueError:
        return {"status": "unknown", "commit_sha": None}
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root, capture_output=True, text=True, timeout=10,
        )
        if head.returncode != 0:
            return {"status": "unknown", "commit_sha": None}
        head_sha = head.stdout.strip()

        tracked = subprocess.run(
            ["git", "ls-files", "--error-unmatch", rel_path],
            cwd=repo_root, capture_output=True, text=True, timeout=10,
        )
        if tracked.returncode != 0:
            return {"status": "uncommitted", "commit_sha": None, "head_sha_at_manifest_time": head_sha}

        no_diff = subprocess.run(
            ["git", "diff", "--quiet", "HEAD", "--", rel_path],
            cwd=repo_root, timeout=10,
        )
        if no_diff.returncode == 0:
            return {"status": "committed", "commit_sha": head_sha}
        return {"status": "uncommitted", "commit_sha": None, "head_sha_at_manifest_time": head_sha}
    except Exception:
        return {"status": "unknown", "commit_sha": None}


def _is_rth(bar: Bar5m) -> bool:
    dt = datetime.fromisoformat(bar.timestamp)
    t = (dt.hour, dt.minute)
    return RTH_START <= t < RTH_END


def rth_dates(bars: list[Bar5m]) -> set[str]:
    return {datetime.fromisoformat(b.timestamp).date().isoformat() for b in bars if _is_rth(b)}


def coverage_report(bars_by_symbol: dict[str, list[Bar5m]]) -> dict:
    per_symbol = {}
    date_sets: dict[str, set[str]] = {}
    for symbol, bars in bars_by_symbol.items():
        dates = rth_dates(bars)
        date_sets[symbol] = dates
        per_symbol[symbol] = {
            "first_timestamp": bars[0].timestamp if bars else None,
            "last_timestamp": bars[-1].timestamp if bars else None,
            "total_bars": len(bars),
            "rth_session_dates": len(dates),
        }
    common = set.intersection(*date_sets.values()) if date_sets else set()
    union = set.union(*date_sets.values()) if date_sets else set()
    missing = {
        symbol: sorted(union - dates)
        for symbol, dates in date_sets.items()
        if union - dates
    }
    return {
        "per_symbol": per_symbol,
        "common_rth_session_dates": len(common),
        "common_date_range": (min(common), max(common)) if common else None,
        "missing_sessions_by_symbol": missing,
    }


# ── BATS overlap sanity check (independent, never merged into output) ──────
def load_bats_csvs(symbol: str, repo_root: Path) -> list[Bar5m]:
    pattern = f"BATS_{symbol.upper()}, 5*.csv"
    paths = sorted(repo_root.glob(pattern))
    bars: list[Bar5m] = []
    for path in paths:
        with path.open("r", newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            header = reader.fieldnames or []
            lowered = {c.lower(): c for c in header}
            ts_col = lowered.get("time") or lowered.get("timestamp")
            o_col, h_col, l_col, c_col, v_col = (
                lowered.get("open"), lowered.get("high"), lowered.get("low"),
                lowered.get("close"), lowered.get("volume"),
            )
            if not all((ts_col, o_col, h_col, l_col, c_col, v_col)):
                continue
            for row in reader:
                try:
                    ts = datetime.fromisoformat(row[ts_col]).isoformat()
                    bars.append(
                        Bar5m(
                            timestamp=ts,
                            open=float(row[o_col]),
                            high=float(row[h_col]),
                            low=float(row[l_col]),
                            close=float(row[c_col]),
                            volume=int(float(row[v_col])),
                        )
                    )
                except (KeyError, TypeError, ValueError):
                    continue
    deduped, _dup_count, conflicts = dedupe_bars_lenient(bars)
    return deduped


def dedupe_bars_lenient(bars: list[Bar5m]) -> tuple[list[Bar5m], int, list[str]]:
    """Same-timestamp dedupe for the BATS *sanity-check* input only --
    lenient (keeps the first row seen) since the numbered TradingView
    export files are expected to overlap at their chunk boundaries and
    this data never reaches the authoritative output CSVs."""
    by_ts: dict[str, Bar5m] = {}
    duplicate_count = 0
    for bar in bars:
        if bar.timestamp in by_ts:
            duplicate_count += 1
            continue
        by_ts[bar.timestamp] = bar
    ordered = sorted(by_ts.values(), key=lambda b: datetime.fromisoformat(b.timestamp))
    return ordered, duplicate_count, []


def _pearson(xs: list[float], ys: list[float]) -> Optional[float]:
    n = len(xs)
    if n < 2:
        return None
    mean_x, mean_y = sum(xs) / n, sum(ys) / n
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    var_x = sum((x - mean_x) ** 2 for x in xs)
    var_y = sum((y - mean_y) ** 2 for y in ys)
    denom = (var_x * var_y) ** 0.5
    if denom == 0:
        return None
    return cov / denom


def compare_overlap(polygon_bars: list[Bar5m], bats_bars: list[Bar5m]) -> dict:
    bats_by_ts = {b.timestamp: b for b in bats_bars}
    matched_polygon: list[Bar5m] = []
    matched_bats: list[Bar5m] = []
    for bar in polygon_bars:
        other = bats_by_ts.get(bar.timestamp)
        if other is not None:
            matched_polygon.append(bar)
            matched_bats.append(other)

    if not matched_polygon:
        return {
            "matched_bars": 0,
            "close_correlation": None,
            "median_abs_ohlc_diff": None,
            "max_abs_ohlc_diff": None,
            "median_abs_bps_diff": None,
            "max_abs_bps_diff": None,
            "volume_median_pct_diff": None,
            "volume_max_pct_diff": None,
        }

    abs_diffs: list[float] = []
    bps_diffs: list[float] = []
    volume_pct_diffs: list[float] = []
    for p, b in zip(matched_polygon, matched_bats):
        for p_val, b_val in ((p.open, b.open), (p.high, b.high), (p.low, b.low), (p.close, b.close)):
            diff = abs(p_val - b_val)
            abs_diffs.append(diff)
            if b_val != 0:
                bps_diffs.append((diff / abs(b_val)) * 10_000)
        if b.volume:
            volume_pct_diffs.append(abs(p.volume - b.volume) / b.volume * 100)

    close_corr = _pearson([p.close for p in matched_polygon], [b.close for b in matched_bats])

    return {
        "matched_bars": len(matched_polygon),
        "close_correlation": close_corr,
        "median_abs_ohlc_diff": statistics.median(abs_diffs) if abs_diffs else None,
        "max_abs_ohlc_diff": max(abs_diffs) if abs_diffs else None,
        "median_abs_bps_diff": statistics.median(bps_diffs) if bps_diffs else None,
        "max_abs_bps_diff": max(bps_diffs) if bps_diffs else None,
        "volume_median_pct_diff": statistics.median(volume_pct_diffs) if volume_pct_diffs else None,
        "volume_max_pct_diff": max(volume_pct_diffs) if volume_pct_diffs else None,
        "note": (
            "Polygon is consolidated-tape (all exchanges); the BATS_* "
            "export is BATS-only single-venue data. Small OHLC/bps "
            "differences are expected from the different reporting "
            "venues, not a defect."
        ),
    }


def overlap_verdict(overlap: dict) -> tuple[bool, list[str]]:
    """Returns (passed, reasons_if_failed). Flags exactly the failure
    classes the operator asked to stop for: timestamp misalignment,
    timezone-conversion error, adjustment mismatch, or materially
    inconsistent pricing -- not routine venue-to-venue noise."""
    if overlap["matched_bars"] == 0:
        return False, ["zero overlapping timestamps matched -- likely timestamp/timezone misalignment"]
    reasons = []
    corr = overlap["close_correlation"]
    # Correlation is only meaningful with enough matched bars AND some
    # price variance across them; a too-small or constant-price sample
    # legitimately can't produce one (statistics, not a defect) -- fall
    # through to the bps checks instead of forcing a false failure.
    if corr is not None and corr < MIN_ACCEPTABLE_CLOSE_CORRELATION:
        reasons.append(f"close correlation {corr} below {MIN_ACCEPTABLE_CLOSE_CORRELATION}")
    median_bps = overlap["median_abs_bps_diff"]
    if median_bps is not None and median_bps > MAX_ACCEPTABLE_MEDIAN_BPS:
        reasons.append(f"median bps diff {median_bps:.2f} above {MAX_ACCEPTABLE_MEDIAN_BPS}")
    max_bps = overlap["max_abs_bps_diff"]
    if max_bps is not None and max_bps > MAX_ACCEPTABLE_MAX_BPS:
        reasons.append(f"max bps diff {max_bps:.2f} above {MAX_ACCEPTABLE_MAX_BPS}")
    return (len(reasons) == 0), reasons


def build_manifest(
    *,
    symbols: tuple[str, ...],
    requested_start: str,
    requested_end: str,
    coverage: dict,
    output_paths: dict[str, Path],
    row_counts: dict[str, int],
    repo_root: Path,
    retrieved_at: str,
) -> dict:
    script_path = Path(__file__).resolve()
    commit_info = script_commit_status(repo_root, script_path)
    return {
        "source": "Polygon.io",
        "endpoint": "/v2/aggs/ticker/{ticker}/range/5/minute/{from}/{to}",
        "adjusted": True,
        "symbols": list(symbols),
        "requested_date_range": {"start": requested_start, "end": requested_end},
        "actual_coverage": coverage["per_symbol"],
        "retrieval_timestamp_utc": retrieved_at,
        "row_counts": row_counts,
        "sha256": {symbol: sha256_of_file(path) for symbol, path in output_paths.items()},
        # Only "committed" when HEAD's tree actually contains this exact
        # script byte-for-byte -- never attaches a commit SHA to a script
        # that was uncommitted/edited at manifest-build time (see
        # script_commit_status()). script_sha256 is always populated
        # regardless of commit status, so the manifest is independently
        # verifiable even if git history is ever rewritten.
        "script_commit_status": commit_info["status"],
        "script_commit_sha": commit_info.get("commit_sha"),
        "script_sha256": sha256_of_file(script_path),
    }


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    parser.add_argument("--months-back", type=int, default=DEFAULT_MONTHS_BACK)
    parser.add_argument("--start-date", default=None, help="YYYY-MM-DD, overrides --months-back")
    parser.add_argument("--end-date", default=None, help="YYYY-MM-DD, defaults to today")
    parser.add_argument("--out-dir", default="data/stocks_advisory_polygon_5m")
    parser.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--skip-bats-check", action="store_true")
    parser.add_argument("--as-of", default=None, help="YYYY-MM-DD to anchor --months-back against; defaults to real today")
    parser.add_argument(
        "--no-fetch", action="store_true",
        help="Rebuild the manifest (and re-run the BATS overlap check) from the "
             "CSVs already written in --out-dir, without contacting Polygon again. "
             "Used to refresh script_commit_sha/script_sha256 after committing the "
             "script, without refetching or altering the dataset.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    repo_root = Path(args.repo_root)
    symbols = tuple(s.strip().upper() for s in args.symbols.split(",") if s.strip())

    anchor = date.fromisoformat(args.as_of) if args.as_of else date.today()
    end_date = args.end_date or anchor.isoformat()
    if args.start_date:
        start_date = args.start_date
    else:
        start_date = (anchor - timedelta(days=30 * args.months_back)).isoformat()

    out_dir = repo_root / args.out_dir
    bars_by_symbol: dict[str, list[Bar5m]] = {}
    output_paths: dict[str, Path] = {}
    row_counts: dict[str, int] = {}

    if args.no_fetch:
        print(f"--no-fetch: rebuilding manifest from existing CSVs in {out_dir}, no network calls...")
        for symbol in symbols:
            path = out_dir / f"{symbol}_5min.csv"
            if not path.exists():
                print(f"BLOCKED: --no-fetch requested but {path} does not exist. Stopping.", file=sys.stderr)
                return 2
            pre_hash = sha256_of_file(path)
            bars = read_csv_bars(path)
            bars_by_symbol[symbol] = bars
            output_paths[symbol] = path
            row_counts[symbol] = len(bars)
            post_hash = sha256_of_file(path)
            assert pre_hash == post_hash, f"{path} was modified during a --no-fetch run (should be impossible: read-only)"
            print(f"  {symbol}: {len(bars)} bars read from {path} (sha256 unchanged: {post_hash[:12]}...)")
    else:
        client = PolygonAggsClient()
        if not client.configured:
            print("BLOCKED: POLYGON_API_KEY not configured. Stopping -- no workaround attempted.", file=sys.stderr)
            return 2

        print(f"Fetching {symbols} from {start_date} to {end_date} (adjusted=true, 5min)...")
        for symbol in symbols:
            raw_rows = client.fetch_all_bars(symbol, start_date, end_date, adjusted=True)
            mapped = [b for b in (_row_to_bar(r) for r in raw_rows) if b is not None]
            skipped = len(raw_rows) - len(mapped)
            deduped, dup_count, _conflicts = dedupe_bars(mapped)
            bars_by_symbol[symbol] = deduped
            path = out_dir / f"{symbol}_5min.csv"
            write_csv(path, deduped)
            output_paths[symbol] = path
            row_counts[symbol] = len(deduped)
            print(
                f"  {symbol}: {len(raw_rows)} raw rows, {skipped} skipped (malformed), "
                f"{dup_count} exact duplicates dropped, {len(deduped)} bars written -> {path}"
            )

    coverage = coverage_report(bars_by_symbol)
    print("\n=== Coverage report ===")
    print(json.dumps(coverage, indent=2, default=str))

    overlap_by_symbol: dict[str, dict] = {}
    overall_overlap_passed = True
    if not args.skip_bats_check:
        print("\n=== BATS overlap sanity check (independent, not merged) ===")
        print(
            "    Gate is scored on RTH-matched bars only, since csv_loader.py "
            "only ever feeds rth_bars into the backtest -- pre-market/after-"
            "hours bars are thin, single-venue-vs-consolidated noise there is "
            "expected and reported separately, not gated on."
        )
        for symbol in symbols:
            bats_bars = load_bats_csvs(symbol, repo_root)
            if not bats_bars:
                print(f"  {symbol}: no BATS_* files found for this symbol, skipping overlap check")
                continue
            polygon_rth = [b for b in bars_by_symbol[symbol] if _is_rth(b)]
            bats_rth = [b for b in bats_bars if _is_rth(b)]
            overlap_all_sessions = compare_overlap(bars_by_symbol[symbol], bats_bars)
            overlap_rth = compare_overlap(polygon_rth, bats_rth)
            passed, reasons = overlap_verdict(overlap_rth)
            overlap_by_symbol[symbol] = {
                "all_sessions": overlap_all_sessions,
                "rth_only_gated": {**overlap_rth, "passed": passed, "fail_reasons": reasons},
            }
            overall_overlap_passed = overall_overlap_passed and passed
            print(f"  {symbol}: {json.dumps(overlap_by_symbol[symbol], indent=2, default=str)}")

    manifest_path = out_dir / "manifest.json"
    now = datetime.now(timezone.utc).isoformat()
    manifest_rebuilt_at = None
    if args.no_fetch:
        # Preserve the ORIGINAL fetch's retrieval timestamp -- a --no-fetch
        # manifest rebuild is metadata-only and must not overwrite when the
        # data itself was actually pulled from Polygon.
        try:
            previous = json.loads(manifest_path.read_text(encoding="utf-8"))
            retrieved_at = previous["retrieval_timestamp_utc"]
        except Exception:
            retrieved_at = now
            print(
                "  WARNING: could not read prior manifest's retrieval_timestamp_utc; "
                "using current time instead (this run did not actually fetch data).",
                file=sys.stderr,
            )
        manifest_rebuilt_at = now
    else:
        retrieved_at = now

    manifest = build_manifest(
        symbols=symbols,
        requested_start=start_date,
        requested_end=end_date,
        coverage=coverage,
        output_paths=output_paths,
        row_counts=row_counts,
        repo_root=repo_root,
        retrieved_at=retrieved_at,
    )
    if manifest_rebuilt_at is not None:
        manifest["manifest_rebuilt_at_utc"] = manifest_rebuilt_at
    manifest["bats_overlap_check"] = overlap_by_symbol
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    print(f"\nManifest written -> {manifest_path}")

    if not overall_overlap_passed:
        print(
            "\nSTOP: BATS overlap sanity check failed its tolerance thresholds -- "
            "this suggests timestamp misalignment, a timezone-conversion error, an "
            "adjustment mismatch, or materially inconsistent pricing, not routine "
            "venue-to-venue noise. Do not use this dataset until investigated.",
            file=sys.stderr,
        )
        return 1

    print("\nPASS: coverage complete, no conflicting duplicates, overlap sanity check within tolerance.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
