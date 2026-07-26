"""MNQ bar loaders + 15m->60m resampler for the 60M 3-2-2 expanded-evidence study.

Scoped exclusively to the 60M 3-2-2 evidence lane (file name intentionally
prefixed `bars_322_` to avoid any collision with the parallel 2-1-2/1-2-2
evidence lane working in a different worktree of the same repo).

This module is research-only: it reads the git-ignored local JSONL bar caches
under `data/replay_polygon*` / `data/replay_corpus_v1` and produces plain
in-memory dict lists shaped exactly the way `detect_322_first_live()` and
`replay_322_honest_fill.py`'s functions expect (`ts`/`open`/`high`/`low`/
`close` keys, tz-aware `ts`). It never imports runtime/execution/broker code.

Resampling rule (15m -> 60m, ET-anchored):
  open  = first available 15m sub-bar's open in that ET hour
  high  = max high across available 15m sub-bars in that ET hour
  low   = min low across available 15m sub-bars in that ET hour
  close = last available 15m sub-bar's close in that ET hour
If an ET hour has zero 15m sub-bars present in the source file, no 60m bar is
produced for that hour (fail-closed -- the detector's own `_find()` already
treats a missing bar as "no signal for this date", so this is consistent with
upstream fail-closed behavior, not a new leniency).

Manually verified against raw source rows for 2024-08-30 (see
docs/strategy-rules/60M_322_EXPANDED_EVIDENCE_2026-07-26.md, "Resampler
verification") and for one DST-transition date, before being trusted at scale.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")


def _read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open() as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def load_15m_day(cache_dir: str | Path, instrument: str, day: date) -> list[dict]:
    """Load one day's raw 15-minute bars, tz-aware, sorted by timestamp."""
    path = Path(cache_dir) / instrument / f"{instrument}_{day.isoformat()}.jsonl"
    if not path.exists():
        return []
    rows = _read_jsonl(path)
    bars = []
    for row in rows:
        ts = datetime.fromisoformat(row["timestamp"])
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        bars.append(
            {
                "ts": ts,
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": row.get("volume"),
                "market_condition": row.get("market_condition"),
                "trend_direction": row.get("trend_direction"),
                "trend_strength": row.get("trend_strength"),
                "reconstructed_market_condition": row.get("reconstructed_market_condition"),
                "reconstructed_trend_direction": row.get("reconstructed_trend_direction"),
            }
        )
    return sorted(bars, key=lambda b: b["ts"])


def resample_60m_et(bars_15m: list[dict]) -> list[dict]:
    """Resample a day's 15m bars (any tz) into ET-hour-anchored 60m OHLC bars."""
    buckets: dict[datetime, list[dict]] = {}
    for bar in bars_15m:
        local = bar["ts"].astimezone(ET)
        hour_key = local.replace(minute=0, second=0, microsecond=0)
        buckets.setdefault(hour_key, []).append({**bar, "ts": local})

    out = []
    for hour_key in sorted(buckets):
        subs = sorted(buckets[hour_key], key=lambda b: b["ts"])
        out.append(
            {
                "ts": hour_key,
                "open": subs[0]["open"],
                "high": max(s["high"] for s in subs),
                "low": min(s["low"] for s in subs),
                "close": subs[-1]["close"],
                "n_sub_bars": len(subs),
            }
        )
    return out


def load_60m_day_et(cache_dir: str | Path, instrument: str, day: date) -> list[dict]:
    return resample_60m_et(load_15m_day(cache_dir, instrument, day))


def load_5m_day(cache_dir: str | Path, instrument: str, day: date) -> list[dict]:
    """Load one day's raw 5-minute (RTH-only) bars as ET-tz-aware ts/OHLC dicts,
    matching the key shape `replay_322_honest_fill.py`'s functions expect."""
    path = Path(cache_dir) / instrument / f"{instrument}_{day.isoformat()}.jsonl"
    if not path.exists():
        return []
    rows = _read_jsonl(path)
    bars = []
    for row in rows:
        ts = datetime.fromisoformat(row["timestamp"])
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        bars.append(
            {
                "ts": ts.astimezone(ET),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
            }
        )
    return sorted(bars, key=lambda b: b["ts"])


def trading_days(start: date, end: date) -> list[date]:
    days = []
    current = start
    while current <= end:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    return days
