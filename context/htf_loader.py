"""
context/htf_loader.py

Loads HTF JSONL files produced by scripts/csv_to_htf.py and provides a
timestamp-keyed lookup for use in replay and live signal evaluation.

Usage:
    from context.htf_loader import HTFLookup, build_htf_context
    from context.market_context import HTFContext

    lookup = HTFLookup()
    lookup.load("data/htf/CME_MINI_MNQ1!_1D.jsonl", timeframe="1D")
    lookup.load("data/htf/CME_MINI_MNQ1!_240_(1).jsonl", timeframe="4H")

    htf: HTFContext | None = lookup.get_context(ts, direction="LONG")
"""

from __future__ import annotations

import bisect
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from context.market_context import HTFContext


def _tf_to_seconds(tf: str) -> int:
    """Best-effort parse of a timeframe label to seconds (0 if unknown).

    Handles "1D"/"4H"/"1h"/"240"/"60"/"15m" etc. Bare numbers are minutes
    (TradingView convention: "240" = 240m = 4h).
    """
    t = str(tf).strip().lower()
    explicit = {
        "1d": 86400, "d": 86400, "day": 86400, "daily": 86400,
        "4h": 14400, "240": 14400,
        "1h": 3600, "60": 3600, "h": 3600, "hourly": 3600,
        "30m": 1800, "30": 1800, "15m": 900, "15": 900,
        "5m": 300, "5": 300,
    }
    if t in explicit:
        return explicit[t]
    m = re.fullmatch(r"(\d+)\s*([a-z]*)", t)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        if unit in ("d", "day", "days"):
            return n * 86400
        if unit in ("h", "hr", "hour", "hours"):
            return n * 3600
        if unit in ("m", "min", "minute", "minutes", ""):
            return n * 60  # bare number = minutes
    return 0


@dataclass
class _Bar:
    unix: int
    direction: str   # UP | DOWN | FLAT
    bias: str        # UP | DOWN | NEUTRAL


class HTFLookup:
    """
    Holds sorted bars for each timeframe and answers "what was the HTF reading
    at timestamp T?" via binary search (O log n).

    Call load() once per HTF file, then get_context() per 5m bar.
    """

    def __init__(self) -> None:
        self._frames: dict[str, list[_Bar]] = {}
        # Bar duration (seconds) per timeframe, inferred from data spacing at
        # load time. Used to expose a bar only AFTER it has closed (no lookahead).
        self._durations: dict[str, int] = {}

    @staticmethod
    def _infer_interval(bars: list[_Bar]) -> int:
        """Smallest positive gap between consecutive bar opens = the bar interval."""
        gaps = [b2.unix - b1.unix for b1, b2 in zip(bars, bars[1:]) if b2.unix > b1.unix]
        return min(gaps) if gaps else 0

    # ── Data loading ──────────────────────────────────────────────────────────

    def load(self, path: str | Path, timeframe: Optional[str] = None) -> None:
        """
        Load a HTF JSONL file.  timeframe overrides the file's own field if set.
        """
        path = Path(path)
        bars: list[_Bar] = []
        with path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                tf = timeframe or rec.get("timeframe", "unknown")
                bars.append(_Bar(
                    unix=rec["unix"],
                    direction=rec.get("direction", "FLAT"),
                    bias=rec.get("bias", "NEUTRAL"),
                ))
                key = tf  # one list per timeframe key
        if not bars:
            return
        bars.sort(key=lambda b: b.unix)
        # merge with any previously loaded bars for the same timeframe
        existing = self._frames.get(key, [])
        merged = sorted(existing + bars, key=lambda b: b.unix)
        # deduplicate (same unix → keep last)
        seen: dict[int, _Bar] = {}
        for b in merged:
            seen[b.unix] = b
        self._frames[key] = sorted(seen.values(), key=lambda b: b.unix)
        # Record the bar interval so _at can require a bar to be CLOSED.
        explicit = _tf_to_seconds(key)
        self._durations[key] = explicit or self._infer_interval(self._frames[key])

    # ── Lookup ────────────────────────────────────────────────────────────────

    def _at(self, tf: str, ts_unix: int) -> Optional[_Bar]:
        """Return the most recent CLOSED bar for timeframe tf at/before ts_unix.

        HTF rows are timestamped at bar OPEN. A bar opening at b.unix only closes
        at b.unix + duration, so exposing it before then would leak future OHLC
        into a lower-timeframe decision (lookahead). We therefore require
        b.unix + duration <= ts_unix, i.e. b.unix <= ts_unix - duration.
        """
        bars = self._frames.get(tf)
        if not bars:
            return None
        duration = self._durations.get(tf) or _tf_to_seconds(tf)
        cutoff = ts_unix - duration  # bar must have closed by ts_unix
        keys = [b.unix for b in bars]
        idx = bisect.bisect_right(keys, cutoff) - 1
        return bars[idx] if idx >= 0 else None

    def get_context(
        self,
        timestamp: datetime | str,
        direction: Optional[str] = None,
    ) -> Optional[HTFContext]:
        """
        Return an HTFContext for the given timestamp.

        direction: "LONG" or "SHORT" — used to evaluate ftfc_aligned.
        Returns None if no HTF data is loaded at all.
        """
        if not self._frames:
            return None

        if isinstance(timestamp, str):
            timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        ts_unix = int(timestamp.astimezone(timezone.utc).timestamp())

        daily = self._at("1D", ts_unix)
        four_h = self._at("4H", ts_unix)

        daily_direction = daily.direction if daily else None
        daily_bias = daily.bias if daily else None
        four_hour_direction = four_h.direction if four_h else None
        four_hour_bias = four_h.bias if four_h else None

        # FTFC: "full-timeframe trend confluence" — all loaded frames agree
        directions = [d for d in (daily_direction, four_hour_direction) if d and d != "FLAT"]
        if len(directions) >= 2 and len(set(directions)) == 1:
            ftfc_direction = directions[0]
        elif len(directions) == 1:
            ftfc_direction = directions[0]
        else:
            ftfc_direction = None

        # Alignment: does FTFC agree with the intended trade direction?
        if direction and ftfc_direction:
            expected = "UP" if direction == "LONG" else "DOWN"
            ftfc_aligned = ftfc_direction == expected
        else:
            ftfc_aligned = None

        return HTFContext(
            daily_direction=daily_direction,
            four_hour_direction=four_hour_direction,
            ftfc_direction=ftfc_direction,
            ftfc_aligned=ftfc_aligned,
            # Pass bias through as extra fields if HTFContext supports them;
            # otherwise they're silently unused (no error).
        )

    # ── Convenience ──────────────────────────────────────────────────────────

    def loaded_timeframes(self) -> list[str]:
        return sorted(self._frames.keys())

    def bar_count(self, tf: str) -> int:
        return len(self._frames.get(tf, []))
