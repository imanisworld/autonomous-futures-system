#!/usr/bin/env python3
"""P3-source — bar-source parity: live bar files vs a Polygon corpus, bars and admitted levels.

Structural-level prereg v1.5 §14 (P3-M2K). A collection-only root (M2K) has no journaled
level copy to run the P3 levels-only parity against (the observation lane journals candidates,
not `location_context`). What CAN be proven is that the two bar sources the study relies on —
the box's `bars_<INST>_*.jsonl` (TradingView-fed, what the live detectors saw) and the pinned
Polygon corpus (what replay will see) — agree: (1) OHLC of every common bar within one tick,
and (2) every admitted level (prereg §3, P1 `build_levels`) computed from each source at every
common B0 within one tick (the frozen P3 tolerance and ≥ 98 % gate, eligible-row denominator
under §9.4 as in P3). Levels available on only one side (the live history is short) are
counted, not scored. Read-only; imports the pure P1 module only.

Usage:
    python3 scripts/structural_level_bar_source_parity.py --instrument M2K \
        --bars-root <snapshot> --corpus-dir data/replay_polygon_parity_m2k_2026_09_16/M2K \
        --out <report.json>
"""

from __future__ import annotations

import argparse
import collections
import glob
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research import structural_level_features as slf  # noqa: E402
from research.structural_level_p2 import load_corpus_bars, parse_dt, read_jsonl  # noqa: E402

TOOL_VERSION = "slf-bar-source-parity-v1.5"
PASS_PCT = 98.0


def load_live_bars(bars_root: str, inst: str, timeframe_minutes: int = 15) -> list[dict]:
    seen: dict[datetime, dict] = {}
    for f in sorted(glob.glob(os.path.join(bars_root, f"bars_{inst}_*.jsonl"))):
        for r in read_jsonl(f):
            tf = r.get("timeframe")
            if tf is not None and int(str(tf).rstrip("m") or 0) != timeframe_minutes:
                continue
            ts = parse_dt(r.get("ts"))
            if ts is None or ts in seen:
                continue
            try:
                seen[ts] = {"ts": ts, "open": float(r["open"]), "high": float(r["high"]),
                            "low": float(r["low"]), "close": float(r["close"]), "volume": r.get("volume")}
            except (KeyError, TypeError, ValueError):
                continue
    return [seen[k] for k in sorted(seen)]


def corpus_as_bars(corpus: dict[datetime, dict]) -> list[dict]:
    out = []
    for ts in sorted(corpus):
        c = corpus[ts]
        out.append({"ts": ts, "open": float(c["open"]), "high": float(c["high"]), "low": float(c["low"]),
                    "close": float(c["close"]), "volume": c.get("volume")})
    return out


def _within(a: float, b: float, tick: float) -> bool:
    return abs(a - b) <= tick + 1e-9


def run(inst: str, bars_root: str, corpus_dir: str, *, end_ts_exclusive: datetime | None = None,
        example_limit: int = 5) -> dict:
    live = load_live_bars(bars_root, inst)
    corpus = corpus_as_bars(load_corpus_bars(corpus_dir))
    if end_ts_exclusive is not None:
        live = [b for b in live if b["ts"] < end_ts_exclusive]
        corpus = [b for b in corpus if b["ts"] < end_ts_exclusive]
    tick = slf.contract_economics(inst)[0]
    live_by = {b["ts"]: b for b in live}
    corp_by = {b["ts"]: b for b in corpus}
    common = sorted(set(live_by) & set(corp_by))

    # (1) OHLC parity
    ohlc = collections.Counter()
    ohlc_bad: list[dict] = []
    for ts in common:
        l, c = live_by[ts], corp_by[ts]
        ok = all(_within(l[k], c[k], tick) for k in ("open", "high", "low", "close"))
        ohlc["compared"] += 1
        ohlc["agree" if ok else "disagree"] += 1
        if not ok and len(ohlc_bad) < example_limit:
            ohlc_bad.append({"ts": ts.isoformat(), "live": {k: l[k] for k in ("open", "high", "low", "close")},
                             "corpus": {k: c[k] for k in ("open", "high", "low", "close")}})

    # (2) admitted levels from each source at every common B0
    names = tuple(slf.MAJOR) + tuple(slf.ZONE_NAMES) + ("PDC_BAR",)
    tally: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    examples: dict[str, list] = collections.defaultdict(list)
    for ts in common:
        lw = [b for b in live if b["ts"] <= ts][-slf.LIVE_BAR_WINDOW:]
        cw = [b for b in corpus if b["ts"] <= ts][-slf.LIVE_BAR_WINDOW:]
        try:
            ll = slf.build_levels(lw, inst, b0_ts=ts).levels
            cl = slf.build_levels(cw, inst, b0_ts=ts).levels
        except Exception as exc:  # noqa: BLE001 — counted, never hidden
            tally["_build_failed"]["rows"] += 1
            if len(examples["_build_failed"]) < example_limit:
                examples["_build_failed"].append({"ts": ts.isoformat(), "error": str(exc)})
            continue
        for n in names:
            a, b = ll.get(n), cl.get(n)
            t = tally[n]
            t["rows"] += 1
            sa = a.status if a else "MISSING"; sb = b.status if b else "MISSING"
            if sa != "AVAILABLE" and sb != "AVAILABLE":
                t["both_unavailable"] += 1
                continue
            if sa != "AVAILABLE":
                t["live_unavailable_only"] += 1
                continue
            if sb != "AVAILABLE":
                t["corpus_unavailable_only"] += 1
                continue
            if a.gap_contaminated or b.gap_contaminated:
                t["excluded_gap_contaminated"] += 1
            if a.kind == "zone":
                ok = _within(a.top, b.top, tick) and _within(a.bottom, b.bottom, tick)
                lv, cv = [a.top, a.bottom], [b.top, b.bottom]
            else:
                ok = _within(a.value, b.value, tick)
                lv, cv = a.value, b.value
            eligible = not (a.gap_contaminated or b.gap_contaminated)
            t["compared_all"] += 1
            t["agree_all" if ok else "disagree_all"] += 1
            if eligible:
                t["compared"] += 1
                t["agree" if ok else "disagree"] += 1
            if not ok and len(examples[n]) < example_limit:
                examples[n].append({"ts": ts.isoformat(), "live": lv, "corpus": cv, "eligible": eligible})

    levels: dict[str, dict] = {}
    failures: list[str] = []
    for n in names:
        t = tally[n]
        pct = round(100.0 * t["agree"] / t["compared"], 3) if t["compared"] else None
        pct_all = round(100.0 * t["agree_all"] / t["compared_all"], 3) if t["compared_all"] else None
        status = "NO_ROWS" if not t["compared"] else ("PASS" if pct >= PASS_PCT else "FAIL")
        if status == "FAIL":
            failures.append(f"{n}: {pct}% of {t['compared']} eligible")
        levels[n] = {"rows": t["rows"], "compared_eligible": t["compared"], "agree_eligible": t["agree"],
                     "pct_eligible": pct, "compared_all": t["compared_all"], "pct_all": pct_all,
                     "excluded_gap_contaminated": t["excluded_gap_contaminated"],
                     "live_unavailable_only": t["live_unavailable_only"],
                     "corpus_unavailable_only": t["corpus_unavailable_only"],
                     "both_unavailable": t["both_unavailable"], "status": status,
                     "examples": examples.get(n, [])}
    ohlc_pct = round(100.0 * ohlc["agree"] / ohlc["compared"], 3) if ohlc["compared"] else None
    return {
        "tool": TOOL_VERSION, "prereg_version": slf.PREREG_VERSION, "instrument": inst, "tick": tick,
        "gate": {"tolerance": "one tick", "pass_pct": PASS_PCT, "denominator": "eligible rows (§9.4)"},
        "bars": {"live": len(live), "corpus": len(corpus), "common": len(common),
                 "live_only": len(set(live_by) - set(corp_by)), "corpus_only": len(set(corp_by) - set(live_by)),
                 "live_first": live[0]["ts"].isoformat() if live else None,
                 "live_last": live[-1]["ts"].isoformat() if live else None},
        "ohlc": {"compared": ohlc["compared"], "agree": ohlc["agree"], "pct": ohlc_pct,
                 "status": "NO_ROWS" if not ohlc["compared"] else ("PASS" if ohlc_pct >= PASS_PCT else "FAIL"),
                 "examples": ohlc_bad},
        "levels": levels,
        "build_failed_rows": tally["_build_failed"]["rows"],
        "failures": failures,
        "verdict": "NO_ROWS" if not common else ("FAIL" if failures or (ohlc_pct is not None and ohlc_pct < PASS_PCT) else "PASS"),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--instrument", required=True)
    ap.add_argument("--bars-root", required=True)
    ap.add_argument("--corpus-dir", required=True)
    ap.add_argument("--end-ts", default=None, help="exclusive end ISO ts")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    rep = run(args.instrument.upper(), args.bars_root, args.corpus_dir,
              end_ts_exclusive=parse_dt(args.end_ts) if args.end_ts else None)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(rep, fh, indent=1)
        fh.write("\n")
    print(f"[bsp] {rep['instrument']} bars live={rep['bars']['live']} corpus={rep['bars']['corpus']} common={rep['bars']['common']} "
          f"ohlc={rep['ohlc']['pct']}% ({rep['ohlc']['status']})")
    for n, v in rep["levels"].items():
        print(f"[bsp]   {n:20s} eligible {v['agree_eligible']}/{v['compared_eligible']} = {v['pct_eligible']}%  "
              f"live_na={v['live_unavailable_only']} corpus_na={v['corpus_unavailable_only']} both_na={v['both_unavailable']}  {v['status']}")
    print(f"[bsp] verdict {rep['verdict']}")
    return 0 if rep["verdict"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
