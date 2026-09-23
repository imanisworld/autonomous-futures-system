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


# ─── Amendment 1 (§9): Polygon forward source, settlement, gap days ─────────
#
# §9.1  forward bars come from Polygon via the unchanged polygon_to_replay path
#       (PolygonFuturesClient.fetch_continuous + derive_candles, roll 8 days);
#       every run re-fetches the whole window; a CME observation day is used
#       only if it ended >= 24h before the fetch; raw bars are SHA-256 hashed.
# §9.2  forward corpus starts 2026-07-25 (60 days before the first scored day)
#       with polygon_to_replay's own 10-day pre-roll on top.
# §9.3  gap days are detected mechanically, never filled, and removed from BOTH
#       timeframes after enrichment.

SOURCE_POLYGON = "polygon"
FORWARD_CORPUS_START = date(2026, 7, 25)
POLYGON_PREROLL_DAYS = 10          # polygon_to_replay --warmup-days default
STEP0_WARMUP_DAYS = 60             # §9.2 step-0 rebuild
SETTLE_HOURS = 24
TOOL_VERSION_POLYGON = "prereg929-forward-corpus-polygon-v1"

from datetime import time as _time, timedelta as _timedelta  # noqa: E402
from zoneinfo import ZoneInfo as _ZoneInfo  # noqa: E402

_ET = _ZoneInfo("America/New_York")
_SESSION_OPEN = _time(18, 0)   # prior civil day, ET
_SESSION_CLOSE = _time(17, 0)  # observation day, ET


def raw_bars_sha256(bars: list[dict]) -> str:
    """Canonical hash of raw OHLCV bars (the §7 record of what the look used)."""
    h = hashlib.sha256()
    for b in bars:
        h.update(json.dumps(
            [b["ts"], b["open"], b["high"], b["low"], b["close"], b["volume"]], separators=(",", ":")
        ).encode())
        h.update(b"\n")
    return h.hexdigest()


def fetch_polygon_bars(
    timeframe_minutes: int,
    fetch_start: date,
    fetch_end: date,
    *,
    client=None,
    instrument: str = INSTRUMENT,
) -> LoadedBars:
    """Fresh Polygon fetch through the unchanged client path (no cache)."""
    from sources.polygon_client import DEFAULT_ROLL_DAYS, PolygonFuturesClient

    if client is None:
        try:
            from dotenv import load_dotenv

            load_dotenv(REPO / ".env")
        except ImportError:
            pass
        client = PolygonFuturesClient(min_request_interval=13.0)
    if not getattr(client, "configured", True):
        raise RuntimeError("POLYGON_API_KEY not set (the key lives only in the local .env)")
    bars = client.fetch_continuous(instrument, fetch_start, fetch_end, timeframe_minutes, roll_days=DEFAULT_ROLL_DAYS)
    raw = [{"ts": int(b.ts.timestamp()), "open": b.open, "high": b.high,
            "low": b.low, "close": b.close, "volume": b.volume} for b in bars]
    raw.sort(key=lambda r: r["ts"])
    return LoadedBars(
        timeframe_minutes,
        raw,
        [{
            "source": SOURCE_POLYGON,
            "instrument": instrument,
            "fetch_range": [fetch_start.isoformat(), fetch_end.isoformat()],
            "roll_days": DEFAULT_ROLL_DAYS,
            "raw_bars": len(raw),
            "raw_sha256": raw_bars_sha256(raw),
        }],
    )


def obs_day_window(day: date) -> tuple[datetime, datetime]:
    """UTC [open, close) of the CME observation day: prior 18:00 ET -> 17:00 ET."""
    open_et = datetime.combine(day - _timedelta(days=1), _SESSION_OPEN, tzinfo=_ET)
    close_et = datetime.combine(day, _SESSION_CLOSE, tzinfo=_ET)
    return open_et.astimezone(timezone.utc), close_et.astimezone(timezone.utc)


def obs_day_of(ts: int) -> date:
    et = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(_ET)
    return et.date() + _timedelta(days=1) if et.time() >= _SESSION_OPEN else et.date()


def settled_cutoff(fetched_at: datetime) -> datetime:
    """Latest observation-day close (17:00 ET) at least 24h before ``fetched_at``."""
    limit = fetched_at.astimezone(timezone.utc) - _timedelta(hours=SETTLE_HOURS)
    et = limit.astimezone(_ET)
    close = datetime.combine(et.date(), _SESSION_CLOSE, tzinfo=_ET)
    if close > et:
        close -= _timedelta(days=1)
    return close.astimezone(timezone.utc)


def settle(loaded: LoadedBars, cutoff: datetime) -> LoadedBars:
    width = loaded.timeframe_minutes * 60
    lim = int(cutoff.timestamp())
    kept = [b for b in loaded.bars if b["ts"] + width <= lim]
    return LoadedBars(loaded.timeframe_minutes, kept, loaded.source_files, loaded.exact_duplicates_dropped)


def _reduced_schedule(day: date) -> bool:
    """Observation days whose hours the calendar cannot pin (holiday / early close).

    ``context.cme_trading_day`` knows which civil dates are not trade dates but
    not the abbreviated hours around them. For an observation day that is such
    a date, or a usual early-close date (Dec 24, the day after Thanksgiving,
    Jul 3), the gap test is the internal-hole test only.
    """
    from context.cme_trading_day import cme_equity_index_non_trade_dates

    closed = cme_equity_index_non_trade_dates(day.year) | cme_equity_index_non_trade_dates(day.year - 1)
    if day in closed:
        return True
    thanksgiving = next(d for d in (date(day.year, 11, 22) + _timedelta(days=i) for i in range(7)) if d.weekday() == 3)
    early = {date(day.year, 12, 24), thanksgiving + _timedelta(days=1), date(day.year, 7, 3)}
    return day in early


def _slots(start: datetime, end: datetime, width: int) -> list[int]:
    a, b = int(start.timestamp()), int(end.timestamp())
    return list(range(a - (a % width) + (width if a % width else 0), b, width))


def _runs(missing: list[int], width: int) -> list[list[str]]:
    out: list[list[str]] = []
    for ts in missing:
        iso = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
        end = datetime.fromtimestamp(ts + width, tz=timezone.utc).isoformat()
        if out and out[-1][1] == iso:
            out[-1][1] = end
        else:
            out.append([iso, end])
    return out


def detect_gap_days(bars_5m: list[dict], bars_15m: list[dict], first_day: date, last_day: date) -> list[dict]:
    """§9.3 gap days over observation days [first_day, last_day] (weekdays only).

    Regular day: every 5m and 15m slot of prior-18:00 ET -> 17:00 ET must exist.
    Reduced-schedule day (see ``_reduced_schedule``): no missing slot between the
    first and last bar present on that day, per timeframe. On every day a 15m
    bar without its three 5m bars, or 5m bars without their 15m bar, is a gap.
    A weekday with no bars at all on a regular day is a gap; on a reduced day
    it is treated as an exchange closure.
    """
    have = {5: {b["ts"] for b in bars_5m}, 15: {b["ts"] for b in bars_15m}}
    out: list[dict] = []
    d = first_day
    while d <= last_day:
        if d.weekday() < 5:
            lo, hi = obs_day_window(d)
            reduced = _reduced_schedule(d)
            missing: dict[int, list[int]] = {}
            for tf in (5, 15):
                width = tf * 60
                slots = _slots(lo, hi, width)
                present = [s for s in slots if s in have[tf]]
                if reduced:
                    if present:
                        slots = [s for s in slots if present[0] <= s <= present[-1]]
                    else:
                        slots = []
                missing[tf] = [s for s in slots if s not in have[tf]]
            lo_i, hi_i = int(lo.timestamp()), int(hi.timestamp())
            p15 = [t for t in have[15] if lo_i <= t < hi_i]
            p5 = [t for t in have[5] if lo_i <= t < hi_i]
            cov = sorted(
                {t for t in p15 if any((t + k * 300) not in have[5] for k in range(3))}
                | {t - (t % 900) for t in p5 if (t - (t % 900)) not in have[15]}
            )
            if missing[5] or missing[15] or cov:
                out.append({
                    "obs_day": d.isoformat(),
                    "reduced_schedule": reduced,
                    "missing_5m": _runs(missing[5], 300),
                    "missing_15m": _runs(missing[15], 900),
                    "coverage_mismatch_15m_slots": [datetime.fromtimestamp(t, tz=timezone.utc).isoformat() for t in cov],
                })
        d += _timedelta(days=1)
    return out


def drop_obs_days(candles: list[dict], days: set[str]) -> list[dict]:
    """Remove every candle whose CME observation day is in ``days``."""
    if not days:
        return list(candles)
    keep = []
    for c in candles:
        ts = int(datetime.fromisoformat(c["timestamp"].replace("Z", "+00:00")).timestamp())
        if obs_day_of(ts).isoformat() not in days:
            keep.append(c)
    return keep


def build_polygon_corpus(
    out_root: Path,
    corpus_start: date,
    *,
    corpus_end: date | None = None,
    preroll_days: int = POLYGON_PREROLL_DAYS,
    fetched_at: datetime | None = None,
    client=None,
    remove_gap_days: bool = True,
    allow_inside_repo: bool = False,
    loaded: dict[int, LoadedBars] | None = None,
) -> dict:
    """§9.1–§9.3: fresh Polygon fetch -> settle -> derive -> drop gap days -> write.

    Writes ``out_root/5m/MNQ/*``, ``out_root/15m/MNQ/*``, a per-timeframe
    MANIFEST and ``out_root/FORWARD_MANIFEST.json`` (gap days, raw hashes).
    ``loaded`` lets step 0 reuse the bars it already fetched in the same run.
    """
    fetched_at = (fetched_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    cutoff = settled_cutoff(fetched_at)
    last_day = corpus_end or (cutoff.astimezone(_ET).date())
    fetch_start = corpus_start - _timedelta(days=preroll_days)
    bars: dict[int, LoadedBars] = {}
    for tf in (5, 15):
        lb = (loaded or {}).get(tf) or fetch_polygon_bars(tf, fetch_start, last_day, client=client)
        bars[tf] = settle(lb, cutoff)
    gaps = detect_gap_days(bars[5].bars, bars[15].bars, corpus_start, last_day)
    gap_set = {g["obs_day"] for g in gaps} if remove_gap_days else set()
    out_root = Path(out_root)
    per_tf = {}
    for tf in (5, 15):
        candles = derive_corpus_rows(bars[tf].bars, tf)
        candles = [c for c in candles if c["timestamp"][:10] >= corpus_start.isoformat()]
        n_derived = len(candles)
        candles = drop_obs_days(candles, gap_set)
        by_day = split_by_utc_day(candles, end=last_day + _timedelta(days=1))
        files = write_corpus(out_root / f"{tf}m", by_day, allow_inside_repo=allow_inside_repo)
        manifest = {
            "tool_version": TOOL_VERSION_POLYGON,
            "source": SOURCE_POLYGON,
            "enrichment_path": "scripts.polygon_to_replay.derive_candles",
            "enrichment_pins_sha256": pin_hashes(),
            "instrument": INSTRUMENT,
            "timeframe_minutes": tf,
            "corpus_start": corpus_start.isoformat(),
            "preroll_days": preroll_days,
            "fetched_at": fetched_at.isoformat(),
            "settled_cutoff": cutoff.isoformat(),
            "raw_bars_after_settlement": len(bars[tf].bars),
            "raw_sha256_after_settlement": raw_bars_sha256(bars[tf].bars),
            "source_fetch": bars[tf].source_files,
            "derived_candles_in_range": n_derived,
            "candles_removed_as_gap_days": n_derived - len(candles),
            "gap_days": sorted(gap_set),
            "output_files": files,
            "note": "Research data. Never commit. No P&L is computed by this builder.",
        }
        (out_root / f"{tf}m" / f"MANIFEST_{tf}m.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        per_tf[f"{tf}m"] = manifest
    forward = {
        "tool_version": TOOL_VERSION_POLYGON,
        "source": SOURCE_POLYGON,
        "fetched_at": fetched_at.isoformat(),
        "settled_cutoff": cutoff.isoformat(),
        "corpus_start": corpus_start.isoformat(),
        "last_obs_day_checked": last_day.isoformat(),
        "gap_days_removed": remove_gap_days,
        "gap_days": gaps,
        "raw_sha256": {k: v["raw_sha256_after_settlement"] for k, v in per_tf.items()},
    }
    (out_root / "FORWARD_MANIFEST.json").write_text(json.dumps(forward, indent=2, sort_keys=True) + "\n")
    return {"forward": forward, "timeframes": per_tf, "bars": bars}


def load_gap_days(corpus_tf_root: Path) -> list[str]:
    """Gap days recorded by ``build_polygon_corpus`` (fail closed if absent)."""
    root = Path(corpus_tf_root)
    for name in ("MANIFEST_5m.json", "MANIFEST_15m.json"):
        p = root / name
        if p.is_file():
            m = json.loads(p.read_text())
            if m.get("source") != SOURCE_POLYGON:
                raise RuntimeError(f"{p}: corpus source is {m.get('source')!r}, §9.1 requires polygon")
            return sorted(m.get("gap_days") or [])
    raise RuntimeError(f"no Polygon corpus manifest under {root} (§9.1: build with prereg929_forward_corpus.py)")


def rebuild_overlap_corpus(
    out_root: Path,
    loaded: dict[int, LoadedBars],
    starts: dict[int, date],
    end: date,
    *,
    allow_inside_repo: bool = False,
) -> dict:
    """Step-0 rebuild (§9.4): same pipeline as the forward corpus (derive,
    gap-day removal from both timeframes), over the historical overlap.
    ``loaded`` bars must already include the §9.2 60-day pre-roll."""
    first = min(starts.values())
    gaps = detect_gap_days(loaded[5].bars, loaded[15].bars, first, end)
    gap_set = {g["obs_day"] for g in gaps}
    out_root = Path(out_root)
    written = {}
    for tf in (5, 15):
        candles = derive_corpus_rows(loaded[tf].bars, tf)
        candles = [c for c in candles if starts[tf].isoformat() <= c["timestamp"][:10] <= end.isoformat()]
        candles = drop_obs_days(candles, gap_set)
        files = write_corpus(out_root / f"{tf}m", split_by_utc_day(candles), allow_inside_repo=allow_inside_repo)
        manifest = {
            "tool_version": TOOL_VERSION_POLYGON,
            "source": SOURCE_POLYGON,
            "purpose": "step0 overlap rebuild",
            "timeframe_minutes": tf,
            "corpus_start": starts[tf].isoformat(),
            "corpus_end": end.isoformat(),
            "raw_sha256": raw_bars_sha256(loaded[tf].bars),
            "source_fetch": loaded[tf].source_files,
            "gap_days": sorted(gap_set),
            "output_files": files,
        }
        (out_root / f"{tf}m" / f"MANIFEST_{tf}m.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        written[f"{tf}m"] = manifest
    return {"gap_days": gaps, "timeframes": written}
