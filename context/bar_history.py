"""Per-instrument rolling bar history.

The decision pipeline is otherwise stateless-per-alert: each webhook is judged
only from the fields in that one payload. That means when webhooks gap (the
system is "off", the TradingView alert is disabled, or the known 499 ingestion
outage drops bars) the next bar's regime read has NO continuity — the system
never accumulated a picture of recent price action.

This module gives the system a CONTINUOUS record of every bar it ingests — even
bars it does not trade — so it can:
  • judge regime over a WINDOW of recent closes (not a single snapshot), and
  • DETECT gaps, so a post-gap bar is not silently trusted as continuous.

Storage is append-only JSONL per instrument per day
(logs/bars_<INSTRUMENT>_<date>.jsonl), mirroring JournalLogger — including its
mtime/size process cache so repeated reads stay cheap.

NOTE: this is a record + read layer. Backfilling the ACTUAL missing bars after a
gap needs a price-history data source the service does not yet have (the
Tradovate broker only exposes get_quote); `detect_gap` surfaces the gap so a
future backfill step can fill it. We never fabricate bars we did not receive.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import List, Optional


def _parse_dt(ts: str) -> Optional[datetime]:
    """Parse an ISO or epoch timestamp to an aware UTC datetime; None on failure.

    Epoch strings MUST be handled before fromisoformat: live TradingView
    payloads carry epoch timestamps, and a 10-digit epoch-seconds value like
    "1781011800" otherwise parses as basic-ISO "1781-01-18T00", silently
    fragmenting bar history into one junk year-1781 file per bar — which
    breaks the window_direction / gap-continuity reads.
    """
    if not ts:
        return None
    ts = ts.strip()
    if ts.replace(".", "", 1).isdigit():
        try:
            value = float(ts)
        except ValueError:
            return None
        if value > 1e12:  # epoch milliseconds
            value /= 1000.0
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc)
        except (ValueError, OSError, OverflowError):
            return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _iso(ts) -> str:
    """Normalize a timestamp (str, epoch, or datetime) to an ISO string."""
    if isinstance(ts, datetime):
        dt = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    parsed = _parse_dt(str(ts))
    return parsed.isoformat() if parsed else str(ts)


def _ts_date(ts) -> date:
    dt = ts if isinstance(ts, datetime) else _parse_dt(str(ts))
    return (dt or datetime.now(timezone.utc)).date()


class BarHistory:
    """Append-only per-instrument rolling bar buffer with a cheap read cache."""

    # Process-wide cache keyed by path: {path: ((mtime_ns, size), [bars])}
    _cache: dict = {}

    def __init__(self, log_dir: str = "logs"):
        self.log_dir = Path(log_dir)

    # ── paths ────────────────────────────────────────────────────────────────
    def _path_for(self, instrument: str, d: date) -> Path:
        return self.log_dir / f"bars_{instrument}_{d.isoformat()}.jsonl"

    # ── write ────────────────────────────────────────────────────────────────
    def record(
        self,
        instrument: str,
        *,
        ts,
        open: float,
        high: float,
        low: float,
        close: float,
        volume: Optional[float] = None,
        timeframe: Optional[str] = None,
        for_date: Optional[date] = None,
        source: Optional[str] = None,
    ) -> dict:
        """Append one bar-close record. Idempotent on the LAST timestamp: if the
        most recent stored bar for this instrument/day has the same ts (a resend),
        it is NOT appended again. Returns the stored record.

        `source` marks bars that did NOT arrive via live ingestion (e.g.
        "polygon" backfill); live bars omit it.
        """
        d = for_date or _ts_date(ts)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        path = self._path_for(instrument, d)
        rec = {
            "ts": _iso(ts),
            "open": float(open),
            "high": float(high),
            "low": float(low),
            "close": float(close),
            "volume": None if volume is None else float(volume),
            "timeframe": timeframe,
        }
        if source:
            rec["source"] = source
        existing = self._read_bars(path)
        if existing and existing[-1].get("ts") == rec["ts"]:
            return existing[-1]
        with path.open("a") as f:  # path.open avoids the `open` param shadow
            f.write(json.dumps(rec) + "\n")
        return rec

    def merge_backfill(
        self,
        instrument: str,
        d: date,
        bars: List[dict],
        *,
        source: str = "polygon",
    ) -> int:
        """Merge externally-sourced bars into one day file, GAPS ONLY.

        Live ingestion appends in arrival order, so a backfilled bar (older
        than the file tail) cannot simply be appended — it would corrupt the
        chronological order recent()/window_direction rely on. This merges new
        bars with the existing file, keeps EXISTING records on timestamp
        collision (live data always wins), sorts by ts, and atomically rewrites
        the file. Returns the number of bars actually added.

        Offline-maintenance path only — not called by the live pipeline.
        """
        path = self._path_for(instrument, d)
        existing = self._read_bars(path)
        have = {b.get("ts") for b in existing}
        added = []
        for b in bars:
            ts = _iso(b.get("ts"))
            if ts in have:
                continue
            have.add(ts)
            rec = {
                "ts": ts,
                "open": float(b["open"]),
                "high": float(b["high"]),
                "low": float(b["low"]),
                "close": float(b["close"]),
                "volume": None if b.get("volume") is None else float(b["volume"]),
                "timeframe": b.get("timeframe"),
                "source": source,
            }
            added.append(rec)
        if not added:
            return 0
        merged = sorted(existing + added, key=lambda r: r.get("ts") or "")
        self.log_dir.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".jsonl.tmp")
        with tmp.open("w") as f:
            for rec in merged:
                f.write(json.dumps(rec) + "\n")
        tmp.replace(path)
        BarHistory._cache.pop(str(path), None)
        return len(added)

    # ── read (cached) ──────────────────────────────────────────────────────────
    def _read_bars(self, path: Path) -> List[dict]:
        key = str(path)
        try:
            st = path.stat()
        except FileNotFoundError:
            BarHistory._cache.pop(key, None)
            return []
        sig = (st.st_mtime_ns, st.st_size)
        cached = BarHistory._cache.get(key)
        if cached is not None and cached[0] == sig:
            return cached[1]
        bars: List[dict] = []
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    bars.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        BarHistory._cache[key] = (sig, bars)
        return bars

    def recent(
        self,
        instrument: str,
        n: int,
        *,
        for_date: Optional[date] = None,
        lookback_days: int = 3,
    ) -> List[dict]:
        """Return up to the last n bars for instrument, oldest→newest, spanning up
        to lookback_days of files ending at for_date (default today)."""
        if n <= 0:
            return []
        end = for_date or datetime.now(timezone.utc).date()
        collected: List[dict] = []
        # Walk newest day backward, prepend, stop once we have enough.
        for days_back in range(0, max(1, lookback_days)):
            d = date.fromordinal(end.toordinal() - days_back)
            bars = self._read_bars(self._path_for(instrument, d))
            collected = bars + collected
            if len(collected) >= n:
                break
        return collected[-n:]

    def last_bar(
        self, instrument: str, *, for_date: Optional[date] = None, lookback_days: int = 3
    ) -> Optional[dict]:
        bars = self.recent(instrument, 1, for_date=for_date, lookback_days=lookback_days)
        return bars[-1] if bars else None

    # ── gap detection ────────────────────────────────────────────────────────
    def detect_gap(
        self,
        instrument: str,
        new_ts,
        timeframe_minutes: int,
        *,
        for_date: Optional[date] = None,
    ) -> dict:
        """Compare an incoming bar's timestamp to the last STORED bar.

        Returns {gapped, missing_bars, last_ts}. `gapped` is True when more than
        one timeframe interval elapsed since the last stored bar — i.e. at least
        one bar was never received. Call this BEFORE record() for the new bar.
        """
        out = {"gapped": False, "missing_bars": 0, "last_ts": None}
        if timeframe_minutes is None or timeframe_minutes <= 0:
            return out
        prev = self.last_bar(instrument, for_date=for_date)
        if not prev or not prev.get("ts"):
            return out
        last_dt = _parse_dt(prev["ts"])
        new_dt = _parse_dt(_iso(new_ts))
        out["last_ts"] = prev["ts"]
        if last_dt is None or new_dt is None:
            return out
        elapsed_min = (new_dt - last_dt).total_seconds() / 60.0
        if elapsed_min <= 0:
            return out
        intervals = round(elapsed_min / timeframe_minutes)
        missing = max(0, intervals - 1)
        out["gapped"] = missing > 0
        out["missing_bars"] = missing
        return out

    # ── window regime ──────────────────────────────────────────────────────────
    @staticmethod
    def window_direction(
        bars: List[dict], *, min_steps: int = 3, agree_ratio: float = 0.75
    ) -> Optional[str]:
        """Directional read over a window of bars from close-to-close steps.

        Returns "UP"/"DOWN" only when at least `agree_ratio` of the steps move one
        way AND the net move is in that direction — a continuous-data regime
        signal that does NOT depend on Pine's per-bar label or strat fields.
        Returns None when there are too few bars or the window is mixed (chop).
        """
        closes = [b.get("close") for b in bars if b.get("close") is not None]
        if len(closes) < min_steps + 1:
            return None
        steps = [closes[i + 1] - closes[i] for i in range(len(closes) - 1)]
        n = len(steps)
        ups = sum(1 for s in steps if s > 0)
        downs = sum(1 for s in steps if s < 0)
        net = closes[-1] - closes[0]
        if ups / n >= agree_ratio and net > 0:
            return "UP"
        if downs / n >= agree_ratio and net < 0:
            return "DOWN"
        return None
