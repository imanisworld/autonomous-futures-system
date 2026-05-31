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
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from context.market_context import HTFContext


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

    # ── Lookup ────────────────────────────────────────────────────────────────

    def _at(self, tf: str, ts_unix: int) -> Optional[_Bar]:
        """Return the most recent bar for timeframe tf at or before ts_unix."""
        bars = self._frames.get(tf)
        if not bars:
            return None
        keys = [b.unix for b in bars]
        idx = bisect.bisect_right(keys, ts_unix) - 1
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
