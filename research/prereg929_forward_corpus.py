"""Forward corpus builder for prereg #929 (MNQ ex-Asia forward portfolio).

Research only. Converts box-collected raw MNQ OHLCV bars into replay-corpus
rows using the EXISTING enrichment path, ``scripts.polygon_to_replay.derive_candles``
(the same function ``scripts/structural_level_corpus_build.py`` pins and the
function that produced the ``replay_corpus_v1*`` row schema). Nothing here
computes an indicator: this module only

  1. parses box bar files (``bars_MNQ_YYYY-MM-DD.jsonl``) into the raw bar shape
     ``derive_candles`` takes (``ts`` unix seconds + OHLCV), normalizing the two
     timestamp spellings the box writes (ISO-8601 and epoch milliseconds);
  2. calls ``derive_candles(raw, "MNQ", timeframe_minutes)`` unchanged;
  3. splits the result into UTC-day files exactly like ``polygon_to_replay.main``
     (``MNQ/MNQ_YYYY-MM-DD.jsonl``, one ``json.dumps(candle)`` per line).

The output directory must be outside the repository (the corpus is data and
must never be committed). No P&L is computed here.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable

REPO = Path(__file__).resolve().parents[1]
INSTRUMENT = "MNQ"
TOOL_VERSION = "prereg929-forward-corpus-v1"

# Accepted box ``timeframe`` spellings per bar size (the 15m collector writes
# "15", the 5m collector writes "5m").
_TIMEFRAME_LABELS = {5: {"5m", "5"}, 15: {"15m", "15"}}

# Files whose bytes define the enrichment (same pins as the structural-level
# corpus builder, minus its Polygon client which this builder never uses).
ENRICHMENT_PINS = (
    "scripts/polygon_to_replay.py",
    "scripts/csv_to_replay.py",
    "scripts/pine_market_condition.py",
    "context/trend.py",
    "context/cme_trading_day.py",
)


class BoxBarError(RuntimeError):
    """Raised when box bars are malformed or contradictory (fail closed)."""


@dataclass
class LoadedBars:
    timeframe_minutes: int
    bars: list[dict]
    source_files: list[dict] = field(default_factory=list)
    exact_duplicates_dropped: int = 0


def parse_box_ts(value) -> int:
    """Return unix seconds for a box ``ts`` (ISO string, epoch s or epoch ms)."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        num = int(value)
    else:
        text = str(value).strip()
        if text.lstrip("-").isdigit():
            num = int(text)
        else:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                raise BoxBarError(f"naive box timestamp not allowed: {value!r}")
            return int(dt.astimezone(timezone.utc).timestamp())
    # Epoch milliseconds are 13 digits for any date this study can touch.
    if abs(num) >= 10**12:
        if num % 1000:
            raise BoxBarError(f"sub-second epoch-ms box timestamp: {value!r}")
        num //= 1000
    return num


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def load_box_bars(
    bars_dir: Path,
    timeframe_minutes: int,
    *,
    instrument: str = INSTRUMENT,
) -> LoadedBars:
    """Load every ``bars_<instrument>_*.jsonl`` under ``bars_dir``.

    Exact duplicate rows (same ts, same OHLCV) are dropped and counted;
    conflicting duplicates, off-grid timestamps, wrong timeframe labels or
    non-positive prices raise ``BoxBarError``.
    """
    if timeframe_minutes not in _TIMEFRAME_LABELS:
        raise BoxBarError(f"unsupported timeframe {timeframe_minutes}")
    files = sorted(Path(bars_dir).glob(f"bars_{instrument}_*.jsonl"))
    if not files:
        raise BoxBarError(f"no bars_{instrument}_*.jsonl files under {bars_dir}")
    labels = _TIMEFRAME_LABELS[timeframe_minutes]
    width = timeframe_minutes * 60
    by_ts: dict[int, dict] = {}
    dupes = 0
    sources: list[dict] = []
    for path in files:
        n = 0
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            obj = json.loads(line)
            label = obj.get("timeframe")
            if label is not None and str(label) not in labels:
                raise BoxBarError(f"{path.name}:{line_no}: timeframe {label!r} != {timeframe_minutes}m")
            ts = parse_box_ts(obj["ts"])
            if ts % width:
                raise BoxBarError(f"{path.name}:{line_no}: ts {obj['ts']!r} off the {timeframe_minutes}m grid")
            bar = {
                "ts": ts,
                "open": float(obj["open"]),
                "high": float(obj["high"]),
                "low": float(obj["low"]),
                "close": float(obj["close"]),
                "volume": float(obj.get("volume") or 0),
            }
            if min(bar["open"], bar["high"], bar["low"], bar["close"]) <= 0:
                raise BoxBarError(f"{path.name}:{line_no}: non-positive price")
            if not (bar["low"] <= min(bar["open"], bar["close"]) and bar["high"] >= max(bar["open"], bar["close"])):
                raise BoxBarError(f"{path.name}:{line_no}: OHLC inconsistent")
            prior = by_ts.get(ts)
            if prior is not None:
                if prior == bar:
                    dupes += 1
                    continue
                raise BoxBarError(f"{path.name}:{line_no}: conflicting duplicate bar at {obj['ts']!r}")
            by_ts[ts] = bar
            n += 1
        sources.append({"file": path.name, "sha256": _sha256(path), "rows_used": n})
    bars = [by_ts[k] for k in sorted(by_ts)]
    return LoadedBars(timeframe_minutes, bars, sources, dupes)


def derive_corpus_rows(bars: list[dict], timeframe_minutes: int, *, instrument: str = INSTRUMENT) -> list[dict]:
    """The existing enrichment path, called unchanged."""
    from scripts.polygon_to_replay import derive_candles

    return derive_candles(bars, instrument, timeframe_minutes)


def split_by_utc_day(
    candles: Iterable[dict],
    *,
    start: date | None = None,
    end: date | None = None,
) -> dict[str, list[dict]]:
    """Group candles by UTC date exactly like ``polygon_to_replay.main``."""
    out: dict[str, list[dict]] = {}
    for c in candles:
        day = c["timestamp"][:10]
        if start is not None and day < start.isoformat():
            continue
        if end is not None and day > end.isoformat():
            continue
        out.setdefault(day, []).append(c)
    return dict(sorted(out.items()))


def _inside_repo(path: Path) -> bool:
    try:
        path.resolve().relative_to(REPO.resolve())
        return True
    except ValueError:
        return False


def pin_hashes() -> dict[str, str]:
    return {rel: _sha256(REPO / rel) for rel in ENRICHMENT_PINS}


def write_corpus(
    out_root: Path,
    by_day: dict[str, list[dict]],
    *,
    instrument: str = INSTRUMENT,
    allow_inside_repo: bool = False,
) -> list[dict]:
    """Write ``out_root/<instrument>/<instrument>_<day>.jsonl``; refuse to overwrite."""
    out_root = Path(out_root)
    if _inside_repo(out_root) and not allow_inside_repo:
        raise RuntimeError(f"refusing to write corpus inside the repository: {out_root}")
    leaf = out_root / instrument
    if leaf.exists() and any(leaf.iterdir()):
        raise RuntimeError(f"output directory is not empty: {leaf}")
    leaf.mkdir(parents=True, exist_ok=True)
    files = []
    for day, rows in by_day.items():
        path = leaf / f"{instrument}_{day}.jsonl"
        with path.open("w") as fh:
            for c in rows:
                fh.write(json.dumps(c) + "\n")
        files.append({
            "file": path.name,
            "rows": len(rows),
            "first": rows[0]["timestamp"],
            "last": rows[-1]["timestamp"],
            "sha256": _sha256(path),
        })
    return files


def build(
    bars_dir: Path,
    timeframe_minutes: int,
    out_root: Path,
    *,
    start: date | None = None,
    end: date | None = None,
    allow_inside_repo: bool = False,
) -> dict:
    """Box bars -> enriched corpus rows -> day files + MANIFEST.json."""
    loaded = load_box_bars(bars_dir, timeframe_minutes)
    candles = derive_corpus_rows(loaded.bars, timeframe_minutes)
    by_day = split_by_utc_day(candles, start=start, end=end)
    files = write_corpus(out_root, by_day, allow_inside_repo=allow_inside_repo)
    manifest = {
        "tool_version": TOOL_VERSION,
        "enrichment_path": "scripts.polygon_to_replay.derive_candles",
        "enrichment_pins_sha256": pin_hashes(),
        "instrument": INSTRUMENT,
        "timeframe_minutes": timeframe_minutes,
        "bars_dir": str(bars_dir),
        "start": start.isoformat() if start else None,
        "end": end.isoformat() if end else None,
        "raw_bars": len(loaded.bars),
        "exact_duplicates_dropped": loaded.exact_duplicates_dropped,
        "derived_candles": len(candles),
        "dropped_by_derive_candles": len(loaded.bars) - len(candles),
        "source_files": loaded.source_files,
        "output_files": files,
        "note": "Research data. Never commit. No P&L is computed by this builder.",
    }
    (Path(out_root) / f"MANIFEST_{timeframe_minutes}m.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    )
    return manifest
