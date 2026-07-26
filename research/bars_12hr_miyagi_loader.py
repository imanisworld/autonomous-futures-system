"""Bar loaders + resamplers for the 12HR Miyagi evidence lane.

Scoped exclusively to the Miyagi lane (file name intentionally prefixed
`bars_12hr_miyagi_` to avoid any collision with the 60M 3-2-2 lane's
`research/bars_322_polygon_loader.py` or the in-progress 4HR Re-Trigger lane).

Research-only: reads the git-ignored local JSONL bar caches under
`data/replay_polygon*` and produces plain in-memory dict lists shaped exactly
the way `detect_12hr_miyagi()` and `replay_12hr_miyagi_honest_fill.py`'s
functions expect (`ts`/`open`/`high`/`low`/`close` keys, tz-aware `ts`). It
never imports runtime/execution/broker/strategy code.

DATA-AVAILABILITY FINDING (verified 2026-07-26, see full report doc under
docs/strategy-rules/): the task brief that motivated this module described
`data/replay_polygon_5m/{MNQ,MES}/` as strictly RTH-only (9:30 AM ET file
start, no pre-market bars at true 5-minute granularity anywhere). Direct
inspection of every file in both instruments' 5-minute caches disproves this
for all but the very first day of coverage:

  - `MNQ_2024-07-02.jsonl` / `MES_2024-07-02.jsonl` (the first day in the
    5-minute cache) is genuinely RTH-only, starting at 13:30 UTC (9:30 AM ET).
  - From 2024-07-03 onward, both instruments' 5-minute caches contain
    near-continuous ~23-24h coverage (matching the CME Globex session, with a
    routine ~1h daily maintenance gap around 5-6 PM ET), INCLUDING the
    4:00-9:30 AM ET pre-market window this detector's Step 5 needs.
  - The only weekday dates with a fully missing 4:00-9:30 AM ET 5-minute
    window across the whole 2024-07-02..2026-06-26 range are the two
    Christmas/New Year's holidays each covered year (2024-12-25, 2025-01-01,
    2025-12-25, 2026-01-01) plus one anomalous data gap on 2025-09-09 (that
    day's 5-minute file does not start until 12:55 PM ET) -- none of which
    can produce a signal anyway (Step 6 already fails closed to `None` when
    the 9:30 AM bar itself is absent, which is also true on all of these
    dates). A handful of additional dates have a single missing 5-minute bar
    within the window (see `PARTIAL_PREMARKET_DATES` below) -- immaterial to
    the single-bar engulf test except in the specific 15-minute slot missing.

Because of this, `load_5m_premarket_window()` below uses TRUE 5-minute bars
as the primary evidence path for every date where the window has full (or
materially full) coverage, and falls back to the 15-minute-cache proxy
described in the task brief (mathematically proven conservative: a 15-minute
bar's own high/low not breaching Bar C's range implies no 5-minute sub-bar
within it could have either) only for the rare dates where true 5-minute
coverage for that specific window is absent. Each date's provenance
(`"5m"` or `"15m_proxy"`) is returned alongside the bars so the study driver
can report the Step-5 granularity-ambiguity finding transparently instead of
silently resolving it.

Resampling rule (15m -> 60m/12h, ET-anchored), mirroring
`research/bars_322_polygon_loader.py::resample_60m_et`'s convention:
  open  = first available 15m sub-bar's open in that ET bucket
  high  = max high across available 15m sub-bars in that ET bucket
  low   = min low across available 15m sub-bars in that ET bucket
  close = last available 15m sub-bar's close in that ET bucket
If an ET bucket has zero 15m sub-bars present in the source files, no bar is
produced for that bucket (fail-closed -- the detector's own `_find()`
already treats a missing bar as "no signal for this date").

12-hour buckets are anchored at 4:00 AM / 4:00 PM ET and, unlike 60-minute
buckets, routinely span two adjacent daily source files (a 4:00 PM ET bucket
runs into the following UTC calendar day's file). `load_12h_bars_for_date()`
therefore loads a small multi-day window of 15-minute files, merges +
dedupes by absolute timestamp, and resamples across the merged series --
never a single day file in isolation.

Both resamplers were manually verified against raw source rows for
2024-09-05 and for one DST-transition date (2024-11-03, fall-back) before
being trusted at scale -- see
docs/strategy-rules/12HR_MIYAGI_CANONICAL_EVIDENCE_2026-07-26.md,
"Resampler verification".
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")

# Weekday dates (across the full 2024-07-02..2026-06-26 cache range, both
# MNQ and MES) where the true 5-minute cache's 4:00-9:30 AM ET window is
# missing one or more bars but is not fully empty. See module docstring.
PARTIAL_PREMARKET_DATES = {
    date(2025, 1, 10),
    date(2025, 11, 28),
    date(2025, 12, 5),
    date(2026, 4, 3),
}

# Expected 5-minute bar count in the [4:00, 9:30) ET window (5.5 hours).
_EXPECTED_PREMARKET_BARS = 66
# Treat the window as "true 5-minute, primary path" if at least this many of
# the expected bars are present; otherwise fall back to the 15m proxy for
# the whole window rather than mixing granularities within one date.
_PREMARKET_COMPLETENESS_THRESHOLD = 60  # allow a handful of routine gaps


def _read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open() as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def _row_to_bar(row: dict) -> dict:
    ts = datetime.fromisoformat(row["timestamp"])
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return {
        "ts": ts,
        "open": float(row["open"]),
        "high": float(row["high"]),
        "low": float(row["low"]),
        "close": float(row["close"]),
    }


def load_15m_day(cache_dir: str | Path, instrument: str, day: date) -> list[dict]:
    """Load one day's raw 15-minute bars (full-day, incl. pre-market), tz-aware."""
    path = Path(cache_dir) / instrument / f"{instrument}_{day.isoformat()}.jsonl"
    if not path.exists():
        return []
    return sorted((_row_to_bar(row) for row in _read_jsonl(path)), key=lambda b: b["ts"])


def load_15m_days(cache_dir: str | Path, instrument: str, days: list) -> list[dict]:
    """Load + merge + dedupe (by absolute ts) 15-minute bars across several days."""
    merged: dict[datetime, dict] = {}
    for day in days:
        for bar in load_15m_day(cache_dir, instrument, day):
            merged[bar["ts"]] = bar
    return sorted(merged.values(), key=lambda b: b["ts"])


def load_5m_day(cache_dir: str | Path, instrument: str, day: date) -> list[dict]:
    """Load one day's raw 5-minute bars (as-available granularity), normalized
    to ET tzinfo (mirroring `bars_322_polygon_loader.load_5m_day`'s
    convention) so downstream wall-clock-time comparisons (e.g. the replay
    engine's exact-15:55-ET-bar lookup) are correct regardless of the
    source file's on-disk UTC offset."""
    path = Path(cache_dir) / instrument / f"{instrument}_{day.isoformat()}.jsonl"
    if not path.exists():
        return []
    bars = []
    for row in _read_jsonl(path):
        raw = _row_to_bar(row)
        bars.append({**raw, "ts": raw["ts"].astimezone(ET)})
    return sorted(bars, key=lambda b: b["ts"])


def _bucket_start_12h(local_dt: datetime) -> datetime:
    """Return the 4AM/4PM-ET-anchored 12-hour bucket start for a local ET dt."""
    hour = local_dt.hour
    if 4 <= hour < 16:
        return local_dt.replace(hour=4, minute=0, second=0, microsecond=0)
    if hour >= 16:
        return local_dt.replace(hour=16, minute=0, second=0, microsecond=0)
    # hour < 4: belongs to the PRIOR calendar date's 4PM bucket.
    prior = local_dt - timedelta(days=1)
    return prior.replace(hour=16, minute=0, second=0, microsecond=0)


def resample_12h_et(bars_15m: list) -> list:
    """Resample a (possibly multi-day, merged) 15m series into 4AM/4PM ET 12h bars."""
    buckets: dict[datetime, list] = {}
    for bar in bars_15m:
        local = bar["ts"].astimezone(ET)
        key = _bucket_start_12h(local)
        buckets.setdefault(key, []).append({**bar, "ts": local})

    out = []
    for key in sorted(buckets):
        subs = sorted(buckets[key], key=lambda b: b["ts"])
        out.append(
            {
                "ts": key,
                "open": subs[0]["open"],
                "high": max(s["high"] for s in subs),
                "low": min(s["low"] for s in subs),
                "close": subs[-1]["close"],
                "n_sub_bars": len(subs),
            }
        )
    return out


def resample_60m_et(bars_15m: list) -> list:
    """Resample a 15m series into ET-hour-anchored 60m OHLC bars."""
    buckets: dict[datetime, list] = {}
    for bar in bars_15m:
        local = bar["ts"].astimezone(ET)
        key = local.replace(minute=0, second=0, microsecond=0)
        buckets.setdefault(key, []).append({**bar, "ts": local})

    out = []
    for key in sorted(buckets):
        subs = sorted(buckets[key], key=lambda b: b["ts"])
        out.append(
            {
                "ts": key,
                "open": subs[0]["open"],
                "high": max(s["high"] for s in subs),
                "low": min(s["low"] for s in subs),
                "close": subs[-1]["close"],
                "n_sub_bars": len(subs),
            }
        )
    return out


def load_12h_bars_for_date(
    cache_15m: str | Path, instrument: str, eval_date: date, lookback_days: int = 6
) -> list:
    """Load enough 15m history to resample the 4 (or 5, incl. Bar Z) 12h bars
    Miyagi needs for `eval_date`, merged across the necessary daily files."""
    days = [eval_date - timedelta(days=offset) for offset in range(lookback_days, -1, -1)]
    bars_15m = load_15m_days(cache_15m, instrument, days)
    return resample_12h_et(bars_15m)


def load_60m_bars_for_date(cache_15m: str | Path, instrument: str, eval_date: date) -> list:
    """Load just `eval_date`'s own 15m file and resample to 60m ET bars.

    The stop-reference bar (8-9 AM ET) always falls within `eval_date`'s own
    UTC-dated source file (8-9 AM ET is always within [00:00, 24:00) UTC of
    the same calendar date, for both EDT and EST offsets), so a single-day
    load suffices here (unlike the 12h loader, which must span files).
    """
    return resample_60m_et(load_15m_day(cache_15m, instrument, eval_date))


def load_5m_premarket_window(
    cache_5m: str | Path, cache_15m: str | Path, instrument: str, eval_date: date
) -> dict:
    """Build the bars_5m Step-5/Step-6 input for `eval_date`.

    Returns {"bars": list[dict], "provenance": "5m" | "15m_proxy" | "empty"}.
    Primary path: true 5-minute bars for the whole day (premarket window +
    the 9:30 AM bar) whenever the premarket window has at least
    `_PREMARKET_COMPLETENESS_THRESHOLD` of the expected 66 bars. Fallback:
    the 15-minute-cache proxy for the premarket window only, still using the
    true 5-minute 9:30 AM bar (which is present on every date this fallback
    is reached for except the handful of dates the detector already fails
    closed on -- see module docstring).
    """
    window_start = datetime(eval_date.year, eval_date.month, eval_date.day, 4, 0, tzinfo=ET)
    window_end = datetime(eval_date.year, eval_date.month, eval_date.day, 9, 30, tzinfo=ET)

    true_5m = load_5m_day(cache_5m, instrument, eval_date)
    premarket_5m = [bar for bar in true_5m if window_start <= bar["ts"] < window_end]

    if len(premarket_5m) >= _PREMARKET_COMPLETENESS_THRESHOLD:
        return {"bars": true_5m, "provenance": "5m"}

    # Fallback: 15-minute proxy for the premarket window, real 5m bars for
    # everything else (in particular the 9:30 AM bar itself).
    bars_15m_day = load_15m_day(cache_15m, instrument, eval_date)
    premarket_15m_proxy = [
        {**bar, "ts": bar["ts"].astimezone(ET)}
        for bar in bars_15m_day
        if window_start <= bar["ts"].astimezone(ET) < window_end
    ]
    rest_of_day_5m = [bar for bar in true_5m if bar["ts"] >= window_end]
    combined = sorted(premarket_15m_proxy + rest_of_day_5m, key=lambda b: b["ts"])
    provenance = "15m_proxy" if premarket_15m_proxy or rest_of_day_5m else "empty"
    return {"bars": combined, "provenance": provenance}


def trading_days(start: date, end: date) -> list:
    days = []
    current = start
    while current <= end:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    return days
