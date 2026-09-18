#!/usr/bin/env python3
"""Structural-level prereg P2 — R1/R2 corpus build with a pinned builder + MANIFEST.

Read-only research tooling (prereg v1.4 §14 P5/P6, P2 spec §3.1 / §5 R1–R2). This is a
thin, faithful wrapper around ``scripts/polygon_to_replay.py``: it calls the SAME
``PolygonFuturesClient.fetch_continuous`` + ``derive_candles`` + day-file split that
``polygon_to_replay.main`` runs, so the candle schema and every derived field are
byte-identical to what the pinned builder would write. It adds only what the spec
requires and the builder lacks:

  • ``--roll-days`` (default 8 = ``DEFAULT_ROLL_DAYS``, the R1 rule that reproduces the
    known 2026-06-11 MNQ roll gap; R2 uses 3 so the U6 contract runs through
    2026-09-14 like the box did),
  • ``--end-ts-exclusive`` (R2 roll cut at 2026-09-14T22:00Z: bars at/after it are dropped),
  • ``--contract`` (v1.5 X0): one FIXED dated contract for the whole fetch range instead of
    the scheduler chain — no seam, so contract identity is exact by construction (#586's
    "one stable dated contract" case); the manifest records ``roll_rule = fixed dated
    contract``. Used when the live feed's underlying contract is proven for the window but
    the scheduler seam before it is not (amendment 1 to the expansion prereg, #625).
  • a fail-closed schema check (``london_orb_*`` must be present — the whole reason the
    pre-existing ``data/replay_polygon`` corpora are unusable),
  • ``MANIFEST.json``: pins (git HEAD, builder file hashes), contract segments + roll
    rule, per-file sha256/rows/first/last, coverage, a 15m-grid gap ledger inside CME
    hours, and a roll ledger (session-open price gap at every contract seam, prereg §9.5).

Nothing here touches runtime, config, ``.env`` or the box. Network: Polygon REST only,
via the pinned client (free-tier pacing).

Usage (R1, prereg v1.4 window; warm-up 2024-09-17 → 09-30 = the provider's retention start):
    python3 scripts/structural_level_corpus_build.py --symbol MNQ \
        --start 2024-10-01 --end 2026-06-26 --warmup-days 14 --out data/replay_polygon_v2
Usage (R2):
    python3 scripts/structural_level_corpus_build.py --symbol MNQ \
        --start 2026-07-16 --end 2026-09-14 --roll-days 3 \
        --end-ts-exclusive 2026-09-14T22:00:00+00:00 \
        --out data/replay_polygon_parity_2026_07_16_09_14
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from scripts.polygon_to_replay import derive_candles  # noqa: E402
from sources.polygon_client import (  # noqa: E402
    DEFAULT_ROLL_DAYS,
    PolygonFuturesClient,
    contract_schedule,
)

TOOL_VERSION = "slc-build-v1.4"
_ET = ZoneInfo("America/New_York")

# Files whose bytes define the corpus content (P2 spec §3.1 pins).
PINNED_FILES = (
    "scripts/polygon_to_replay.py",
    "scripts/csv_to_replay.py",
    "scripts/pine_market_condition.py",
    "sources/polygon_client.py",
    "context/trend.py",
    "scripts/structural_level_corpus_build.py",
)

# Fields the frozen prereg §3.6 London ORB definition requires in every candle.
REQUIRED_FIELDS = (
    "timestamp", "instrument", "session", "open", "high", "low", "close", "volume",
    "orb_high", "orb_low", "orb_status",
    "london_orb_high", "london_orb_low", "london_orb_status",
    "prev_week_high", "prev_week_low",
    "reconstructed_market_condition", "legacy_market_condition",
)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git_head(repo: Path) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
        ).strip()
    except Exception:  # pragma: no cover - git absent
        return "unknown"


def _parse_ts(s: str) -> datetime:
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _slot_open(t: datetime) -> bool:
    """CME equity-index 15m slot is inside open hours (17:00–18:00 ET halt and the
    Friday 17:00 → Sunday 18:00 ET weekend excluded). Same rule as
    research.structural_level_features._slot_open."""
    e = t.astimezone(_ET)
    wd, hr = e.weekday(), e.hour
    return not (hr == 17 or wd == 5 or (wd == 4 and hr >= 17) or (wd == 6 and hr < 18))


def gap_ledger(timestamps: list[datetime], timeframe_minutes: int) -> list[dict]:
    """Runs of missing grid slots inside CME open hours, [first, last] inclusive."""
    if not timestamps:
        return []
    present = set(timestamps)
    step = timedelta(minutes=timeframe_minutes)
    t = timestamps[0]
    end = timestamps[-1]
    runs: list[dict] = []
    cur: dict | None = None
    while t <= end:
        missing = _slot_open(t) and t not in present
        if missing:
            if cur is None:
                cur = {"first_missing": t.isoformat(), "last_missing": t.isoformat(), "slots": 1}
            else:
                cur["last_missing"] = t.isoformat()
                cur["slots"] += 1
        elif cur is not None:
            cur["minutes"] = cur["slots"] * timeframe_minutes
            runs.append(cur)
            cur = None
        t += step
    if cur is not None:
        cur["minutes"] = cur["slots"] * timeframe_minutes
        runs.append(cur)
    return runs


def roll_ledger(candles: list[dict], segments: list[tuple[str, date, date]]) -> list[dict]:
    """Session-open price gap at each contract seam (prereg §9.5 / P6).

    Segments are UTC-date ranges (the client fetches each contract by UTC date), so the
    seam is the first bar whose UTC date is the new segment's start. The gap is that
    bar's open minus the close of the last bar before it."""
    out: list[dict] = []
    by_idx = [(_parse_ts(c["timestamp"]), c) for c in candles]
    for i in range(1, len(segments)):
        prev_ticker = segments[i - 1][0]
        ticker, seg_start, _ = segments[i]
        first_idx = next(
            (k for k, (ts, _) in enumerate(by_idx) if ts.date() >= seg_start), None
        )
        if first_idx is None or first_idx == 0:
            out.append({"from": prev_ticker, "to": ticker, "roll_utc_date": seg_start.isoformat(),
                        "status": "NOT_IN_CORPUS"})
            continue
        prev_ts, prev_c = by_idx[first_idx - 1]
        ts, c = by_idx[first_idx]
        out.append({
            "from": prev_ticker, "to": ticker, "roll_utc_date": seg_start.isoformat(),
            "last_old_bar": prev_ts.isoformat(), "last_old_close": prev_c["close"],
            "first_new_bar": ts.isoformat(), "first_new_open": c["open"],
            "gap_points": round(c["open"] - prev_c["close"], 4),
        })
    return out


def build(
    *,
    symbol: str,
    start: date,
    end: date,
    timeframe: int,
    warmup_days: int,
    roll_days: int,
    out_root: Path,
    end_ts_exclusive: datetime | None,
    client: PolygonFuturesClient,
    corpus_label: str | None = None,
    fresh: bool = False,
    contract: str | None = None,
) -> dict:
    """Fetch, derive, write day files + MANIFEST.json. Returns the manifest dict.

    Refuses to write into a directory that already holds day files unless ``fresh`` (which
    removes them first) — a partial overwrite would leave stale days beside new ones."""
    out_dir = out_root / symbol
    existing = sorted(out_dir.glob("*.jsonl")) if out_dir.exists() else []
    if existing and not fresh:
        raise SystemExit(f"[build] {out_dir} already holds {len(existing)} day files — pass --fresh "
                         "to replace the corpus (stale days must never sit beside new ones)")
    fetch_start = start - timedelta(days=warmup_days)
    if contract:
        contract = contract.strip().upper()
        if not contract.startswith(symbol):
            raise SystemExit(f"[build] --contract {contract} does not belong to symbol {symbol}")
        segments = [(contract, fetch_start, end)]
        print(f"[build] fetching {contract} (fixed dated contract, no roll) {timeframe}m bars "
              f"{fetch_start}..{end} (warmup {warmup_days}d)")
        bars = client.fetch_bars(contract, fetch_start, end, timeframe)
    else:
        segments = contract_schedule(symbol, fetch_start, end, roll_days)
        print(f"[build] fetching {symbol} {timeframe}m bars {fetch_start}..{end} "
              f"(warmup {warmup_days}d, roll_days={roll_days}, segments={len(segments)})")
        bars = client.fetch_continuous(symbol, fetch_start, end, timeframe, roll_days=roll_days)
    print(f"[build] {len(bars)} bars fetched")
    if not bars:
        raise SystemExit("[build] no bars fetched — refusing to write an empty corpus")

    # Identical to polygon_to_replay.main from here to the day-file split.
    raw = [{"ts": int(b.ts.timestamp()), "open": b.open, "high": b.high,
            "low": b.low, "close": b.close, "volume": b.volume} for b in bars]
    candles = derive_candles(raw, symbol, timeframe)
    start_iso = start.isoformat()
    candles = [c for c in candles if c["timestamp"][:10] >= start_iso]
    dropped_after_cut = 0
    if end_ts_exclusive is not None:
        before = len(candles)
        candles = [c for c in candles if _parse_ts(c["timestamp"]) < end_ts_exclusive]
        dropped_after_cut = before - len(candles)
    print(f"[build] {len(candles)} candles derived ({dropped_after_cut} dropped at the roll cut)")

    if candles:
        missing = [k for k in REQUIRED_FIELDS if k not in candles[-1]]
        if missing:
            raise SystemExit(f"[build] builder output lacks required fields {missing} — "
                             "wrong builder version; refusing to write")

    # Replace only after a successful fetch + derivation (a failed fetch must not destroy
    # the previous corpus).
    for stale in existing:
        stale.unlink()
    if existing and (out_dir / "MANIFEST.json").exists():
        (out_dir / "MANIFEST.json").unlink()
    out_dir.mkdir(parents=True, exist_ok=True)
    by_day: dict[str, list[dict]] = {}
    for c in candles:
        by_day.setdefault(c["timestamp"][:10], []).append(c)
    files: dict[str, dict] = {}
    for day, day_candles in sorted(by_day.items()):
        path = out_dir / f"{symbol}_{day}.jsonl"
        with path.open("w") as f:
            for c in day_candles:
                f.write(json.dumps(c) + "\n")
        files[path.name] = {
            "sha256": sha256_file(path), "rows": len(day_candles),
            "first": day_candles[0]["timestamp"], "last": day_candles[-1]["timestamp"],
        }
    print(f"[build] wrote {len(by_day)} day files → {out_dir}")

    timestamps = [_parse_ts(c["timestamp"]) for c in candles]
    manifest = {
        "tool": TOOL_VERSION,
        "corpus": corpus_label or f"{out_root.name}/{symbol}",
        "instrument": symbol,
        "timeframe_minutes": timeframe,
        "schema": "scripts/polygon_to_replay.derive_candles (called unchanged; day-file split "
                  "identical to polygon_to_replay.main)",
        "source": {
            "provider": "Polygon futures aggs (sources/polygon_client.PolygonFuturesClient)",
            "endpoint": f"/futures/v1/aggs/{{ticker}}?resolution={timeframe}min",
            "fetched_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "requested_range": f"{start.isoformat()}..{end.isoformat()}",
            "fetch_range_with_warmup": f"{fetch_start.isoformat()}..{end.isoformat()}",
            "warmup_days": warmup_days,
            "end_ts_exclusive": end_ts_exclusive.isoformat() if end_ts_exclusive else None,
            "candles_dropped_at_end_cut": dropped_after_cut,
            "roll_rule": (f"fixed dated contract {contract} (no roll; single segment)" if contract else
                          f"roll_days={roll_days} (front contract advances {roll_days} calendar "
                          f"days before 3rd-Friday expiry; sources.polygon_client.front_contract)"
                          + (" [DEFAULT_ROLL_DAYS]" if roll_days == DEFAULT_ROLL_DAYS else "")),
            "contract_segments": [[t, s.isoformat(), e.isoformat()] for t, s, e in segments],
            "raw_bars_fetched": len(bars),
        },
        "builder": {
            "git_head_of_checkout": git_head(REPO),
            "file_sha256": {rel: sha256_file(REPO / rel) for rel in PINNED_FILES},
        },
        "coverage": {
            "files": len(files),
            "rows": len(candles),
            "first_bar": candles[0]["timestamp"] if candles else None,
            "last_bar": candles[-1]["timestamp"] if candles else None,
            "required_fields_present": list(REQUIRED_FIELDS),
        },
        "gap_ledger_cme_hours": gap_ledger(timestamps, timeframe),
        "roll_ledger": roll_ledger(candles, segments),
        "files": files,
    }
    manifest_path = out_dir / "MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest, indent=1) + "\n")
    print(f"[build] manifest → {manifest_path} (sha256 {sha256_file(manifest_path)[:16]}…)")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--start", required=True, help="YYYY-MM-DD (inclusive)")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD (inclusive, UTC date)")
    parser.add_argument("--timeframe", type=int, default=15)
    parser.add_argument("--warmup-days", type=int, default=10,
                        help="extra days fetched before --start for EMA200 warmup "
                             "(polygon_to_replay default)")
    parser.add_argument("--roll-days", type=int, default=DEFAULT_ROLL_DAYS)
    parser.add_argument("--end-ts-exclusive", default=None,
                        help="ISO timestamp; candles at/after it are dropped (roll cut)")
    parser.add_argument("--out", required=True, help="corpus root; writes <out>/<SYMBOL>/")
    parser.add_argument("--label", default=None, help="corpus label for the manifest")
    parser.add_argument("--fresh", action="store_true", help="replace an existing corpus directory")
    parser.add_argument("--contract", default=None,
                        help="one fixed dated contract (e.g. M2KZ6) for the whole range; no roll/seam")
    args = parser.parse_args(argv)

    try:
        from dotenv import load_dotenv
        load_dotenv(REPO / ".env")
    except ImportError:
        pass

    client = PolygonFuturesClient(min_request_interval=13.0)
    if not client.configured:
        print("[build] POLYGON_API_KEY not set", file=sys.stderr)
        return 1
    build(
        symbol=args.symbol.strip().upper(),
        start=date.fromisoformat(args.start),
        end=date.fromisoformat(args.end),
        timeframe=args.timeframe,
        warmup_days=args.warmup_days,
        roll_days=args.roll_days,
        out_root=Path(args.out),
        end_ts_exclusive=_parse_ts(args.end_ts_exclusive) if args.end_ts_exclusive else None,
        client=client,
        corpus_label=args.label,
        fresh=args.fresh,
        contract=args.contract,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
