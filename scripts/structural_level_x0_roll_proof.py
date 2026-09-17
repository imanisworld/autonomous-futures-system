#!/usr/bin/env python3
"""X0 — dated-contract identity + roll-seam provenance proof for a stitched replay corpus.

Expansion prereg (`docs/prereg-cross-instrument-historical-expansion-2026-09-17.md` §3) and
amendment 1 (#625): a `contract_schedule` from `sources/polygon_client` is only a *candidate*
chain and a clean MANIFEST is not roll provenance. This tool re-establishes, from the provider,
what the corpus actually holds:

1. **Contract identity per segment** — every dated ticker in the manifest is re-fetched as a
   *dated* contract over its segment; every corpus bar in that segment must exist in that
   contract's own series (the provider's per-row ``ticker`` is recorded). OHLCV equality is
   reported separately so a provider revision (C18) is visible but never mistaken for an
   identity failure.
2. **Seam census** — for every transition both contracts are fetched around the seam:
   corpus last-old / first-new bar, price gap at the seam, timestamps present in both
   contracts (overlap), one-sided timestamps, conflicting duplicates inside the corpus, and
   the per-UTC-day volume of each contract with the provider volume-crossover day
   (informational — it is *not* used to move a seam).
3. **Live-feed reconciliation** (optional ``--bars-root``) — each live bar from the box's
   ``bars_<INST>_*.jsonl`` (a continuous ``*1!`` feed that proves nothing by itself) is
   matched, OHLC within one tick, against every candidate dated contract fetched here. That
   identifies the feed's underlying contract per bar without inferring it from the scheduler.
   A seam is ``FEED_CONFIRMED`` only when live bars exist on both sides and the identity flips
   exactly at the seam; ``FEED_CONTRADICTED`` when any co-timestamped live bar identifies as
   the other contract; ``NOT_OBSERVABLE`` when the live span does not cover the seam.

Corpus classification (frozen here, reported not tuned):

* ``CONTRACT_IDENTITY_PROVEN`` — every corpus bar found in its declared dated contract;
* seam rule — ``SCHEDULER_CONVENTION`` (``roll_days=N``) or ``FIXED_DATED_CONTRACT``
  (single contract, no seam);
* feed reconciliation — ``FEED_CONFIRMED`` / ``FEED_CONTRADICTED`` / ``NOT_OBSERVABLE`` per
  seam, ``NOT_APPLICABLE`` when no live bars are supplied (historical window with no feed);
* ``roll_provenance`` — ``PROVEN`` when there is no seam or every seam is ``FEED_CONFIRMED``;
  ``ROLL_PROVENANCE_UNKNOWN`` when a live feed is supplied and any seam is ``NOT_OBSERVABLE``
  or contradicted; ``SCHEDULER_CONVENTION_ONLY`` when no live feed exists to reconcile against.

Read-only: Polygon GETs only; nothing on the box is touched; no outcome field exists in any
input. Never a runtime import.

Usage:
    python3 scripts/structural_level_x0_roll_proof.py --corpus-dir data/replay_polygon_v2/M2K \
        [--bars-root <box bars snapshot>] [--seam-window-days 3] --out <report.json>
"""

from __future__ import annotations

import argparse
import collections
import glob
import hashlib
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from research.structural_level_p2 import load_corpus_bars, parse_dt, read_jsonl  # noqa: E402
from sources.polygon_client import PolygonBar, PolygonFuturesClient  # noqa: E402

TOOL_VERSION = "slx0-roll-proof-v1.5"


def _tick(inst: str) -> float:
    from config.futures_contracts import contract_economics
    return float(contract_economics(inst)[0])


def _within(a: float, b: float, tol: float) -> bool:
    return abs(float(a) - float(b)) <= tol + 1e-9


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
                            "low": float(r["low"]), "close": float(r["close"]),
                            "source_ticker": r.get("source_ticker")}
            except (KeyError, TypeError, ValueError):
                continue
    return [seen[k] for k in sorted(seen)]


def _bars_by_ts(bars: list[PolygonBar]) -> dict[datetime, PolygonBar]:
    return {b.ts: b for b in bars}


def _fetch(client: PolygonFuturesClient, cache: dict, ticker: str, s: date, e: date,
           timeframe: int) -> dict[datetime, PolygonBar]:
    key = (ticker, s.isoformat(), e.isoformat())
    if key not in cache:
        cache[key] = _bars_by_ts(client.fetch_bars(ticker, s, e, timeframe))
    return cache[key]


def _merge(*maps: dict[datetime, PolygonBar]) -> dict[datetime, PolygonBar]:
    out: dict[datetime, PolygonBar] = {}
    for m in maps:
        out.update(m)
    return out


def segment_identity(corpus: dict[datetime, dict], ticker: str, fetched: dict[datetime, PolygonBar],
                     s: date, e: date, example_limit: int = 5) -> dict:
    rows = [(ts, c) for ts, c in corpus.items() if s <= ts.date() <= e]
    found = missing = ohlcv_equal = ohlcv_differs = 0
    resp_tickers: collections.Counter = collections.Counter()
    ex_missing: list[str] = []
    ex_diff: list[dict] = []
    for ts, c in sorted(rows):
        b = fetched.get(ts)
        if b is None:
            missing += 1
            if len(ex_missing) < example_limit:
                ex_missing.append(ts.isoformat())
            continue
        found += 1
        resp_tickers[b.ticker] += 1
        same = all(_within(getattr(b, k), c[k], 0.0) for k in ("open", "high", "low", "close")) \
            and _within(b.volume, float(c.get("volume") or 0), 0.0)
        if same:
            ohlcv_equal += 1
        else:
            ohlcv_differs += 1
            if len(ex_diff) < example_limit:
                ex_diff.append({"ts": ts.isoformat(),
                                "corpus": [c["open"], c["high"], c["low"], c["close"], c.get("volume")],
                                "provider": [b.open, b.high, b.low, b.close, b.volume]})
    provider_not_in_corpus = sum(1 for ts in fetched if s <= ts.date() <= e and ts not in corpus)
    return {
        "ticker": ticker, "segment_start": s.isoformat(), "segment_end": e.isoformat(),
        "corpus_rows": len(rows), "found_in_dated_contract": found, "missing_from_dated_contract": missing,
        "ohlcv_equal": ohlcv_equal, "ohlcv_differs_provider_revision": ohlcv_differs,
        "provider_rows_not_in_corpus": provider_not_in_corpus,
        "provider_response_tickers": dict(resp_tickers),
        "identity": "PROVEN" if rows and missing == 0 else ("NO_ROWS" if not rows else "NOT_PROVEN"),
        "examples_missing": ex_missing, "examples_ohlcv_differs": ex_diff,
    }


def seam_census(corpus: dict[datetime, dict], old: str, new: str, seam: date,
                old_bars: dict[datetime, PolygonBar], new_bars: dict[datetime, PolygonBar],
                window_days: int) -> dict:
    seam_ts = datetime(seam.year, seam.month, seam.day, tzinfo=timezone.utc)
    lo = seam_ts - timedelta(days=window_days)
    hi = seam_ts + timedelta(days=window_days)
    in_win = lambda ts: lo <= ts < hi  # noqa: E731
    o = {ts for ts in old_bars if in_win(ts)}
    n = {ts for ts in new_bars if in_win(ts)}
    corp = sorted(ts for ts in corpus if in_win(ts))
    last_old = max((ts for ts in corp if ts < seam_ts), default=None)
    first_new = min((ts for ts in corp if ts >= seam_ts), default=None)
    gap = None
    if last_old is not None and first_new is not None:
        gap = round(float(corpus[first_new]["open"]) - float(corpus[last_old]["close"]), 4)
    # corpus bars on the wrong side of the seam relative to their declared contract
    wrong_side = 0
    for ts in corp:
        want = old_bars if ts < seam_ts else new_bars
        if ts not in want:
            wrong_side += 1
    vol: dict[str, dict[str, float]] = collections.defaultdict(lambda: {"old": 0.0, "new": 0.0})
    for ts, b in old_bars.items():
        if in_win(ts):
            vol[ts.date().isoformat()]["old"] += b.volume
    for ts, b in new_bars.items():
        if in_win(ts):
            vol[ts.date().isoformat()]["new"] += b.volume
    days = sorted(vol)
    crossover = next((d for d in days if vol[d]["new"] > vol[d]["old"]), None)
    # both-hit on the same timestamp with different prices is expected across contracts (they
    # are different instruments); a *conflict* is the same timestamp twice inside the corpus,
    # which load_corpus_bars already collapses — count raw duplicates from the day files.
    return {
        "from": old, "to": new, "seam_utc": seam_ts.isoformat(),
        "seam_rule_note": "seam is the first bar whose UTC date equals the new segment's start "
                          "(UTC midnight, i.e. inside the Globex trading day that opened 22:00Z)",
        "corpus_last_old_bar": last_old.isoformat() if last_old else None,
        "corpus_last_old_close": float(corpus[last_old]["close"]) if last_old else None,
        "corpus_first_new_bar": first_new.isoformat() if first_new else None,
        "corpus_first_new_open": float(corpus[first_new]["open"]) if first_new else None,
        "gap_points_at_seam": gap,
        "window": {"from": lo.isoformat(), "to": hi.isoformat(), "days_each_side": window_days},
        "old_contract_bars_in_window": len(o), "new_contract_bars_in_window": len(n),
        "overlap_timestamps_both_contracts": len(o & n),
        "old_only_timestamps": len(o - n), "new_only_timestamps": len(n - o),
        "old_contract_bars_after_seam": sum(1 for ts in o if ts >= seam_ts),
        "new_contract_bars_before_seam": sum(1 for ts in n if ts < seam_ts),
        "corpus_bars_in_window": len(corp),
        "corpus_bars_not_in_declared_contract": wrong_side,
        "volume_by_utc_day": {d: vol[d] for d in days},
        "provider_volume_crossover_utc_day": crossover,
        "crossover_vs_seam_days": ((date.fromisoformat(crossover) - seam).days if crossover else None),
    }


IDENTITY_TOL_TICKS = 4       # feed-vs-provider revisions of 1–2 ticks (C18) must not defeat identity
MIN_SEPARATION_TICKS = 20    # the runner-up contract must be far away (contract spreads are ~200 ticks)


def live_identity(live: list[dict], candidates: dict[str, dict[datetime, PolygonBar]], tick: float,
                  example_limit: int = 5) -> dict:
    """Identify the dated contract behind each live bar: nearest candidate by max |OHLC diff|,
    admitted only when within ``IDENTITY_TOL_TICKS`` and the runner-up is at least
    ``MIN_SEPARATION_TICKS`` farther. Exact one-tick agreement is tallied separately (that is
    the parity tolerance, not the identity criterion)."""
    per: collections.Counter = collections.Counter()
    strict: collections.Counter = collections.Counter()
    ident: list[tuple[datetime, str]] = []
    ex_none: list[dict] = []
    for b in live:
        dists = {}
        for t, m in candidates.items():
            pb = m.get(b["ts"])
            if pb is None:
                continue
            dists[t] = max(abs(float(getattr(pb, k)) - float(b[k])) for k in ("open", "high", "low", "close"))
        if not dists:
            per["NOT_SERVED_BY_PROVIDER"] += 1
            if len(ex_none) < example_limit:
                ex_none.append({"ts": b["ts"].isoformat(), "reason": "no candidate contract has this bar"})
            continue
        ranked = sorted(dists.items(), key=lambda kv: kv[1])
        best, d0 = ranked[0]
        d1 = ranked[1][1] if len(ranked) > 1 else float("inf")
        if d0 <= IDENTITY_TOL_TICKS * tick + 1e-9 and d1 - d0 >= MIN_SEPARATION_TICKS * tick - 1e-9:
            per[best] += 1
            ident.append((b["ts"], best))
            if d0 <= tick + 1e-9:
                strict[best] += 1
        elif d0 <= IDENTITY_TOL_TICKS * tick + 1e-9:
            per["AMBIGUOUS"] += 1
        else:
            per["NONE"] += 1
            if len(ex_none) < example_limit:
                ex_none.append({"ts": b["ts"].isoformat(), "live": [b["open"], b["high"], b["low"], b["close"]],
                                "max_abs_diff_by_contract": {t: round(d, 4) for t, d in ranked}})
    runs: list[dict] = []
    for ts, t in ident:
        if runs and runs[-1]["contract"] == t:
            runs[-1]["last"] = ts.isoformat(); runs[-1]["bars"] += 1
        else:
            runs.append({"contract": t, "first": ts.isoformat(), "last": ts.isoformat(), "bars": 1})
    return {"live_bars": len(live),
            "live_first": live[0]["ts"].isoformat() if live else None,
            "live_last": live[-1]["ts"].isoformat() if live else None,
            "source_tickers": sorted({str(b.get("source_ticker")) for b in live}),
            "identity_rule": {"tolerance_ticks": IDENTITY_TOL_TICKS, "min_separation_ticks": MIN_SEPARATION_TICKS},
            "identified_per_contract": dict(per),
            "within_one_tick_per_contract": dict(strict),
            "identity_runs": runs,
            "feed_switch_observed_in_live_span": len({r["contract"] for r in runs}) > 1,
            "examples_unidentified": ex_none}


def reconcile_seam(seam: dict, live_id: dict | None) -> str:
    if live_id is None:
        return "NOT_APPLICABLE"
    seam_ts = parse_dt(seam["seam_utc"])
    runs = live_id["identity_runs"]
    if not runs:
        return "NOT_OBSERVABLE"
    before = [r for r in runs if parse_dt(r["first"]) < seam_ts]
    after = [r for r in runs if parse_dt(r["last"]) >= seam_ts]
    if not before or not after:
        return "NOT_OBSERVABLE"
    # identity on each side must equal the declared contract on that side
    ok_before = all(r["contract"] == seam["from"] for r in runs if parse_dt(r["last"]) < seam_ts)
    ok_after = all(r["contract"] == seam["to"] for r in runs if parse_dt(r["first"]) >= seam_ts)
    straddle = [r for r in runs if parse_dt(r["first"]) < seam_ts <= parse_dt(r["last"])]
    if ok_before and ok_after and not straddle:
        return "FEED_CONFIRMED"
    return "FEED_CONTRADICTED"


def run(corpus_dir: str, client: PolygonFuturesClient, *, bars_root: str | None = None,
        seam_window_days: int = 3, timeframe: int = 15) -> dict:
    manifest_path = Path(corpus_dir) / "MANIFEST.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    inst = str(manifest["instrument"]).upper()
    tick = _tick(inst)
    corpus = load_corpus_bars(corpus_dir)
    if not corpus:
        raise SystemExit(f"[x0] no corpus bars under {corpus_dir}")
    first_ts, last_ts = min(corpus), max(corpus)
    src = manifest.get("source", {})
    segments = [(t, date.fromisoformat(s), date.fromisoformat(e)) for t, s, e in src.get("contract_segments", [])]
    if not segments:
        raise SystemExit("[x0] manifest has no contract_segments")
    # raw duplicate census straight from the day files (load_corpus_bars collapses them)
    raw_ts: collections.Counter = collections.Counter()
    for f in sorted(glob.glob(os.path.join(corpus_dir, "*.jsonl"))):
        for c in read_jsonl(f):
            ts = parse_dt(c.get("timestamp"))
            if ts is not None:
                raw_ts[ts] += 1
    duplicates = sum(1 for v in raw_ts.values() if v > 1)

    cache: dict = {}
    seg_reports = []
    for t, s, e in segments:
        cs, ce = max(s, first_ts.date()), min(e, last_ts.date())
        if cs > ce:
            seg_reports.append({"ticker": t, "segment_start": s.isoformat(), "segment_end": e.isoformat(),
                                "corpus_rows": 0, "identity": "WARMUP_ONLY_NOT_IN_CORPUS"})
            continue
        fetched = _fetch(client, cache, t, cs, ce, timeframe)
        seg_reports.append(segment_identity(corpus, t, fetched, cs, ce))

    seams = []
    candidates: dict[str, dict[datetime, PolygonBar]] = {}
    for i in range(1, len(segments)):
        old, new, seam = segments[i - 1][0], segments[i][0], segments[i][1]
        if seam < first_ts.date() or seam > last_ts.date():
            seams.append({"from": old, "to": new, "seam_utc": datetime(seam.year, seam.month, seam.day,
                                                                       tzinfo=timezone.utc).isoformat(),
                          "status": "OUTSIDE_CORPUS_WINDOW"})
            continue
        lo, hi = seam - timedelta(days=seam_window_days), seam + timedelta(days=seam_window_days)
        ob = _fetch(client, cache, old, lo, hi, timeframe)
        nb = _fetch(client, cache, new, lo, hi, timeframe)
        candidates[old] = _merge(candidates.get(old, {}), ob)
        candidates[new] = _merge(candidates.get(new, {}), nb)
        seams.append(seam_census(corpus, old, new, seam, ob, nb, seam_window_days))

    live_id = None
    if bars_root:
        live = load_live_bars(bars_root, inst, timeframe)
        if live:
            ls, le = live[0]["ts"].date(), live[-1]["ts"].date()
            # every declared contract is a candidate over the live span; the contract before
            # the first seam and after the last one are included by construction
            for t, _s, _e in segments:
                candidates[t] = _merge(candidates.get(t, {}), _fetch(client, cache, t, ls, le, timeframe))
            live_id = live_identity(live, candidates, tick)
    for s in seams:
        if "status" not in s:
            s["feed_reconciliation"] = reconcile_seam(s, live_id)
            s["seam_provenance"] = "SCHEDULER_CONVENTION"

    identity_ok = all(r["identity"] in ("PROVEN", "WARMUP_ONLY_NOT_IN_CORPUS") for r in seg_reports) \
        and any(r["identity"] == "PROVEN" for r in seg_reports)
    in_window_seams = [s for s in seams if "status" not in s]
    if not in_window_seams:
        seam_rule = "FIXED_DATED_CONTRACT" if len(segments) == 1 else "NO_SEAM_IN_CORPUS_WINDOW"
        roll_prov = "PROVEN" if identity_ok else "NOT_PROVEN"
    else:
        seam_rule = "SCHEDULER_CONVENTION"
        recs = {s["feed_reconciliation"] for s in in_window_seams}
        if not identity_ok:
            roll_prov = "NOT_PROVEN"
        elif recs == {"NOT_APPLICABLE"}:
            roll_prov = "SCHEDULER_CONVENTION_ONLY"
        elif recs == {"FEED_CONFIRMED"}:
            roll_prov = "PROVEN"
        else:
            roll_prov = "ROLL_PROVENANCE_UNKNOWN"
    report = {
        "tool": TOOL_VERSION, "instrument": inst, "tick": tick, "corpus_dir": str(corpus_dir),
        "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "provider": {"name": src.get("provider"), "endpoint": src.get("endpoint"),
                     "request_form": "GET {base}/futures/v1/aggs/{DATED_TICKER}?resolution=15min"
                                     "&window_start.gte=<utc date>&window_start.lt=<utc date+1>",
                     "corpus_fetched_utc": src.get("fetched_utc"),
                     "proof_fetched_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                     "declared_roll_rule": src.get("roll_rule")},
        "corpus": {"first_bar": first_ts.isoformat(), "last_bar": last_ts.isoformat(), "rows": len(corpus),
                   "raw_duplicate_timestamps": duplicates},
        "segments": seg_reports,
        "seams": seams,
        "live_feed": live_id,
        "classification": {
            "contract_identity": "CONTRACT_IDENTITY_PROVEN" if identity_ok else "CONTRACT_IDENTITY_NOT_PROVEN",
            "seam_rule": seam_rule,
            "feed_reconciliation": (sorted({s["feed_reconciliation"] for s in in_window_seams})
                                    if in_window_seams else ["NO_SEAM"]),
            "roll_provenance": roll_prov,
        },
    }
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus-dir", required=True)
    ap.add_argument("--bars-root", default=None, help="box bars snapshot (bars_<INST>_*.jsonl) for feed reconciliation")
    ap.add_argument("--seam-window-days", type=int, default=3)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    try:
        from dotenv import load_dotenv
        load_dotenv(REPO / ".env")
    except ImportError:
        pass
    client = PolygonFuturesClient(min_request_interval=13.0)
    if not client.configured:
        print("[x0] POLYGON_API_KEY not set", file=sys.stderr)
        return 1
    rep = run(args.corpus_dir, client, bars_root=args.bars_root, seam_window_days=args.seam_window_days)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(rep, fh, indent=1)
        fh.write("\n")
    c = rep["classification"]
    print(f"[x0] {rep['instrument']} {rep['corpus']['rows']} rows  identity={c['contract_identity']}  "
          f"seam_rule={c['seam_rule']}  feed={c['feed_reconciliation']}  roll_provenance={c['roll_provenance']}")
    for s in rep["segments"]:
        print(f"[x0]   seg {s['ticker']} {s['segment_start']}..{s['segment_end']} rows={s['corpus_rows']} "
              f"found={s.get('found_in_dated_contract')} missing={s.get('missing_from_dated_contract')} "
              f"revised={s.get('ohlcv_differs_provider_revision')} → {s['identity']}")
    for s in rep["seams"]:
        if "status" in s:
            print(f"[x0]   seam {s['from']}→{s['to']} {s['seam_utc']} {s['status']}")
            continue
        print(f"[x0]   seam {s['from']}→{s['to']} {s['seam_utc']} gap={s['gap_points_at_seam']} "
              f"overlap={s['overlap_timestamps_both_contracts']} crossover={s['provider_volume_crossover_utc_day']} "
              f"({s['crossover_vs_seam_days']}d vs seam) feed={s['feed_reconciliation']}")
    if rep["live_feed"]:
        lf = rep["live_feed"]
        print(f"[x0]   live {lf['live_bars']} bars {lf['live_first']}..{lf['live_last']} "
              f"identified={lf['identified_per_contract']} switch_observed={lf['feed_switch_observed_in_live_span']}")
    # 0 = PROVEN; 3 = identity proven but seams are convention-only (documented, not a pass);
    # 2 = ROLL_PROVENANCE_UNKNOWN / NOT_PROVEN.
    return {"PROVEN": 0, "SCHEDULER_CONVENTION_ONLY": 3}.get(c["roll_provenance"], 2)


if __name__ == "__main__":
    raise SystemExit(main())
