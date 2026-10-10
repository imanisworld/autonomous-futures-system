#!/usr/bin/env python3
"""ORB / VWAP sweep: bar timeframe (10/15/30m) × ET windows × IOC bracket P&L.

Research only. Reuses edge_decomposition_audit predicates and PaperBroker
ioc_limit at the decision-bar close (same fill reference as production replay).

Windows (bar **close** in America/New_York):
  morning:   09:40 – 11:00 inclusive
  afternoon: 13:30 – 16:00 inclusive

15m uses the native ``replay_corpus_v1_market_condition_fixed`` corpus.
10m and 30m resample ``replay_corpus_v1_5m`` (last 5m snapshot in each bucket
for ORB/VWAP/trend fields; OHLC aggregated).
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from config.settings import load_config  # noqa: E402
from scripts.edge_decomposition_audit import (  # noqa: E402
    COMMISSION_ROUND_TRIP,
    CORPUS_15M,
    CORPUS_5M_LATE,
    Lane,
    StateBuilder,
    extract_predicate,
    load_bars,
    resolve_bracket,
    run_bracket_stage,
)
from strategy.four_hr_retrigger import et_bucket_start  # noqa: E402

ET = ZoneInfo("America/New_York")

STRATEGIES = (
    ("orb_reclaim", "_try_orb_reclaim", "orb_campaign"),
    ("orb_breakout", "_try_orb_breakout", "date_direction"),
    ("vwap_hold", "_try_vwap_hold", None),
)

WINDOWS = {
    "morning_940_1100": (time(9, 40), time(11, 0)),
    "afternoon_1330_1600": (time(13, 30), time(16, 0)),
}


def _parse_dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _bar_close_et(row: dict, timeframe_minutes: int) -> datetime:
    start = _parse_dt(row["timestamp"]).astimezone(ET)
    return start + timedelta(minutes=timeframe_minutes)


def _in_window(close_et: datetime, start: time, end: time) -> bool:
    t = close_et.time()
    return start <= t <= end


def resample_bars_from_5m(source: Path, instrument: str, minutes: int):
    """Build a Bars-like flat list from 5m daily jsonl files."""
    files = sorted((source / instrument).glob(f"{instrument}_*.jsonl"))
    rows: list[dict] = []
    file_of_idx: list[int] = []
    for fi, path in enumerate(files):
        day_rows = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            row["_dt"] = _parse_dt(row["timestamp"])
            day_rows.append(row)
        if not day_rows:
            continue
        buckets: dict[datetime, list[dict]] = {}
        for row in day_rows:
            start = et_bucket_start(row["_dt"].astimezone(ET), minutes)
            buckets.setdefault(start, []).append(row)
        for start in sorted(buckets):
            chunk = buckets[start]
            template = dict(chunk[-1])
            template["open"] = chunk[0]["open"]
            template["high"] = max(float(r["high"]) for r in chunk)
            template["low"] = min(float(r["low"]) for r in chunk)
            template["close"] = chunk[-1]["close"]
            template["volume"] = sum(int(r.get("volume") or 0) for r in chunk)
            template["timestamp"] = start.astimezone(timezone.utc).isoformat()
            template["_dt"] = _parse_dt(template["timestamp"])
            template["timeframe"] = f"{minutes}m"
            rows.append(template)
            file_of_idx.append(fi)
    order = sorted(range(len(rows)), key=lambda i: rows[i]["_dt"])
    rows = [rows[i] for i in order]
    file_of_idx = [file_of_idx[i] for i in order]
    by_ts = {row["timestamp"]: i for i, row in enumerate(rows)}
    by_dt = {row["_dt"]: i for i, row in enumerate(rows)}
    from scripts.edge_decomposition_audit import Bars

    return Bars(
        corpus_dir=source,
        instrument=instrument,
        rows=rows,
        files=files,
        by_ts=by_ts,
        by_dt=by_dt,
        file_of_idx=file_of_idx,
        bars_per_day=max(1, round(len(rows) / max(1, len(files)))),
    )


def make_lane(strategy: str, predicate: str, campaign: str | None, tf: int, key: str) -> Lane:
    return Lane(
        key=key,
        strategy=strategy,
        instrument="MNQ",
        corpus=CORPUS_15M if tf == 15 else CORPUS_5M_LATE,
        timeframe_minutes=tf,
        source="predicate",
        day_only=False,
        engine=True,
        predicate=predicate,
        campaign=campaign,
        label=key,
        entry_style="close_confirmed",
    )


def summarize(resolved: list[dict]) -> dict:
    nets = [float(r["net"]) for r in resolved if r.get("status") == "RESOLVED" and r.get("net") is not None]
    if not nets:
        return {"resolved": 0, "net": 0.0, "pf": None, "wins": 0, "losses": 0}
    gw = sum(x for x in nets if x > 0)
    gl = abs(sum(x for x in nets if x <= 0))
    return {
        "resolved": len(nets),
        "net": round(sum(nets), 2),
        "pf": round(gw / gl, 3) if gl else None,
        "wins": sum(1 for x in nets if x > 0),
        "losses": sum(1 for x in nets if x <= 0),
    }


def run_sweep(data_root: Path, ny_only: bool = True) -> dict:
    config = load_config()
    config = dataclasses.replace(config, require_trending_condition=False)
    instrument = "MNQ"
    out: dict = {"meta": {"instrument": instrument, "ny_only": ny_only, "fill": "ioc_limit"}, "cells": []}

    tf_loaders: dict[int, object] = {
        15: load_bars(data_root / CORPUS_15M, instrument),
        10: resample_bars_from_5m(data_root / CORPUS_5M_LATE, instrument, 10),
        30: resample_bars_from_5m(data_root / CORPUS_5M_LATE, instrument, 30),
    }

    for tf, bars in tf_loaders.items():
        builder = StateBuilder(config, bars)
        for strategy, predicate, campaign in STRATEGIES:
            lane = make_lane(strategy, predicate, campaign, tf, f"{strategy}_mnq_{tf}m")
            candidates = extract_predicate(lane, bars, builder)
            bracket_rows = run_bracket_stage(
                lane, bars, candidates,
                fill_model="ioc_limit", slippage_ticks=1.0, tolerance_ticks=32.0,
            )
            by_idx = {row["cand"].bar_idx: row for row in bracket_rows}

            for window_name, (wstart, wend) in WINDOWS.items():
                filtered = []
                for cand in candidates:
                    if ny_only and cand.session != "new_york":
                        continue
                    close_et = _bar_close_et(bars.rows[cand.bar_idx], tf)
                    if not _in_window(close_et, wstart, wend):
                        continue
                    row = by_idx.get(cand.bar_idx, {"status": "MISSING"})
                    filtered.append(row)
                resolved = [r for r in filtered if r.get("status") == "RESOLVED"]
                out["cells"].append({
                    "timeframe_minutes": tf,
                    "strategy": strategy,
                    "window": window_name,
                    "candidates": len(filtered),
                    "ioc_resolved": summarize(resolved),
                    "no_fill": sum(1 for r in filtered if r.get("status") == "NO_FILL"),
                })

            # full NY day reference
            ny_all = []
            for cand in candidates:
                if ny_only and cand.session != "new_york":
                    continue
                ny_all.append(by_idx.get(cand.bar_idx, {"status": "MISSING"}))
            out["cells"].append({
                "timeframe_minutes": tf,
                "strategy": strategy,
                "window": "ny_session_all",
                "candidates": len(ny_all),
                "ioc_resolved": summarize([r for r in ny_all if r.get("status") == "RESOLVED"]),
                "no_fill": sum(1 for r in ny_all if r.get("status") == "NO_FILL"),
            })

    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=REPO / "data")
    parser.add_argument("--output", type=Path, default=REPO / "scripts/orb_vwap_tf_window_sweep_results.json")
    args = parser.parse_args()
    report = run_sweep(args.data_root)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
