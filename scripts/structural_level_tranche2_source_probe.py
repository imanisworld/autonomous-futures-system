#!/usr/bin/env python3
"""Tranche-2 X0 source probe — dated-contract inventory, coverage and volume-front chain for a root.

Read-only (`docs/prereg-cross-instrument-historical-expansion-2026-09-17.md` §3 / §8 "X0 read-only
data/source/roll probes"). For one root (MGC / MCL / MBT / …) it records, from the provider only:

1. **Contract inventory** — `GET /futures/v1/contracts?product_code=<ROOT>` paginated; rows are
   de-duplicated by ticker (the endpoint returns one row per listing revision) keeping the
   provider's `first_trade_date` / `last_trade_date` / `trading_venue`; the month-code pattern
   actually listed is derived from the inventory, never assumed.
2. **Coverage per contract** — 15m bars for every contract whose listed life intersects
   `[--start, --end]`: bar count, first/last bar, total volume, per-UTC-day (bars, volume).
3. **Volume-front chain** — per UTC day the contract with the largest volume; consecutive
   crossovers form the candidate chain, reported with the overlap/one-sided census around each
   crossover and whether the chain is causally ordered by expiry. This is *evidence about the
   provider's data*, not a roll rule: a rule may only be proposed from it, and the live feed's
   switch is proven separately (`scripts/structural_level_x0_roll_proof.py --bars-root`).
4. **Retention edge** — earliest bar served across the inventory.
5. **Continuity accounting** — for the volume-front contract, missing 15m slots per UTC day
   against `context.futures_session.product_session_active` (feed-expectation calendar, used
   here only for continuity accounting; it is not a structural-session definition).

Nothing here writes a corpus, touches runtime or the box, or reads any outcome.

Usage:
    python3 scripts/structural_level_tranche2_source_probe.py --root MGC \
        --start 2024-09-01 --end 2026-09-17 --out <report.json>
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
import time as _time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from context.futures_session import product_session_active  # noqa: E402  (pure calendar helper)
from sources.polygon_client import PolygonFuturesClient  # noqa: E402

TOOL_VERSION = "slt2-source-probe-v1.2"
MONTH_CODES = {"F": 1, "G": 2, "H": 3, "J": 4, "K": 5, "M": 6, "N": 7, "Q": 8, "U": 9, "V": 10, "X": 11, "Z": 12}


def _d(s) -> date | None:
    try:
        return date.fromisoformat(str(s)[:10])
    except (TypeError, ValueError):
        return None


def contract_month(ticker: str, root: str) -> tuple[int, int] | None:
    """('MGCZ5' → (2025, 12)); None when the suffix is not <code><digit>."""
    suf = ticker[len(root):]
    if len(suf) != 2 or suf[0] not in MONTH_CODES or not suf[1].isdigit():
        return None
    return 2020 + int(suf[1]), MONTH_CODES[suf[0]]


LISTING_RETRIES = 5     # a 200-page listing must survive a transient 5xx (seen: 503 mid-walk)


def _get_json(http: "httpx.Client", url: str, headers: dict, params: dict | None, pace: float) -> dict:
    last: Exception | None = None
    for attempt in range(LISTING_RETRIES):
        try:
            r = http.get(url, headers=headers, params=params)
            if r.status_code >= 500:
                raise httpx.HTTPStatusError(f"{r.status_code} from provider", request=r.request, response=r)
            r.raise_for_status()
            return r.json()
        except (httpx.HTTPStatusError, httpx.TransportError) as e:
            if isinstance(e, httpx.HTTPStatusError) and e.response.status_code < 500:
                raise
            last = e
            _time.sleep(pace * (attempt + 1))
    raise RuntimeError(f"provider listing failed after {LISTING_RETRIES} attempts: {last}")


def list_contracts(client: PolygonFuturesClient, root: str, pace: float = 13.0) -> tuple[list[dict], int]:
    url = f"{client.base_url}/futures/v1/contracts"
    params: dict | None = {"product_code": root, "limit": 1000}
    headers = {"Authorization": f"Bearer {client.api_key}"}
    seen: dict[str, dict] = {}
    raw = 0
    with httpx.Client(timeout=60.0) as http:
        while url:
            j = _get_json(http, url, headers, params, pace)
            params = None
            for row in j.get("results") or []:
                raw += 1
                t = str(row.get("ticker") or "")
                if not t.startswith(root) or contract_month(t, root) is None:
                    continue        # spreads ("MBTF5-MBTG5"), odd suffixes: not dated outrights
                cur = seen.get(t)
                rec = {"ticker": t, "first_trade_date": row.get("first_trade_date"),
                       "last_trade_date": row.get("last_trade_date"), "trading_venue": row.get("trading_venue"),
                       "active": row.get("active")}
                if cur is None:
                    seen[t] = rec
                else:  # keep the widest life the provider ever listed for this ticker
                    if (_d(rec["first_trade_date"]) or date.max) < (_d(cur["first_trade_date"]) or date.max):
                        cur["first_trade_date"] = rec["first_trade_date"]
                    if (_d(rec["last_trade_date"]) or date.min) > (_d(cur["last_trade_date"]) or date.min):
                        cur["last_trade_date"] = rec["last_trade_date"]
                    cur["active"] = cur["active"] or rec["active"]
            url = j.get("next_url") or ""
            if url:
                _time.sleep(pace)
    out = sorted(seen.values(), key=lambda r: (contract_month(r["ticker"], root) or (9999, 99), r["ticker"]))
    return out, raw


def probe(root: str, client: PolygonFuturesClient, start: date, end: date, *, max_contracts: int = 60,
          horizon_months: int = 3, inventory: list[dict] | None = None, raw_rows: int = 0) -> dict:
    if inventory is None:
        inventory, raw_rows = list_contracts(client, root)
    codes = collections.Counter()
    for r in inventory:
        cm = contract_month(r["ticker"], root)
        if cm:
            codes[r["ticker"][len(root)]] += 1
    # contracts whose listed life intersects the window and whose delivery month is at most
    # `horizon_months` past the window end (far-dated listings carry no bars of interest)
    lim_y, lim_m = end.year, end.month + horizon_months
    while lim_m > 12:
        lim_y, lim_m = lim_y + 1, lim_m - 12
    in_window = [r for r in inventory
                 if (_d(r["last_trade_date"]) or date.max) >= start and (_d(r["first_trade_date"]) or date.min) <= end
                 and (contract_month(r["ticker"], root) or (0, 0)) <= (lim_y, lim_m)]
    in_window = in_window[:max_contracts]
    per_contract: dict[str, dict] = {}
    day_vol: dict[str, dict[str, float]] = collections.defaultdict(dict)   # day → ticker → volume
    day_bars: dict[str, dict[str, int]] = collections.defaultdict(dict)
    earliest: datetime | None = None
    for r in in_window:
        t = r["ticker"]
        fs = max(_d(r["first_trade_date"]) or start, start)
        fe = min(_d(r["last_trade_date"]) or end, end)
        if fs > fe:
            continue
        bars = client.fetch_bars(t, fs, fe, 15)
        by_day_v: dict[str, float] = collections.defaultdict(float)
        by_day_n: dict[str, int] = collections.defaultdict(int)
        for b in bars:
            k = b.ts.date().isoformat()
            by_day_v[k] += b.volume
            by_day_n[k] += 1
            if earliest is None or b.ts < earliest:
                earliest = b.ts
        for k, v in by_day_v.items():
            day_vol[k][t] = v
            day_bars[k][t] = by_day_n[k]
        per_contract[t] = {"listed_first_trade": r["first_trade_date"], "listed_last_trade": r["last_trade_date"],
                           "fetched_range": [fs.isoformat(), fe.isoformat()], "bars": len(bars),
                           "first_bar": bars[0].ts.isoformat() if bars else None,
                           "last_bar": bars[-1].ts.isoformat() if bars else None,
                           "volume": sum(b.volume for b in bars),
                           "days_with_bars": len(by_day_n)}
    # volume-front chain
    front: list[tuple[str, str, float, dict]] = []
    for k in sorted(day_vol):
        vols = day_vol[k]
        if not vols:
            continue
        t, v = max(vols.items(), key=lambda kv: kv[1])
        front.append((k, t, v, vols))
    chain: list[dict] = []
    for i, (k, t, v, vols) in enumerate(front):
        if chain and chain[-1]["ticker"] == t:
            chain[-1]["last_day"] = k
            chain[-1]["days"] += 1
        else:
            chain.append({"ticker": t, "first_day": k, "last_day": k, "days": 1})
    crossovers = []
    for i in range(1, len(chain)):
        a, b = chain[i - 1], chain[i]
        k = b["first_day"]
        vols = day_vol[k]
        prev_day = a["last_day"]
        crossovers.append({"from": a["ticker"], "to": b["ticker"], "crossover_utc_day": k,
                           "from_volume_prev_day": day_vol[prev_day].get(a["ticker"]),
                           "to_volume_prev_day": day_vol[prev_day].get(b["ticker"]),
                           "from_volume_on_day": vols.get(a["ticker"]), "to_volume_on_day": vols.get(b["ticker"]),
                           "both_trading_on_day": a["ticker"] in vols and b["ticker"] in vols,
                           "from_bars_after": sum(n for d, m in day_bars.items() if d >= k for tt, n in m.items() if tt == a["ticker"]),
                           "to_bars_before": sum(n for d, m in day_bars.items() if d < k for tt, n in m.items() if tt == b["ticker"])})
    # causal ordering: every crossover moves to a later expiry month; flip-flops flagged
    def _cm(t):
        return contract_month(t, root) or (0, 0)
    monotone = all(_cm(c["to"]) > _cm(c["from"]) for c in crossovers)
    tickers_in_chain = [c["ticker"] for c in chain]
    flip_flops = len(tickers_in_chain) - len(set(tickers_in_chain))
    # continuity accounting on the volume-front contract (feed-expectation calendar only)
    missing_by_month: dict[str, int] = collections.defaultdict(int)
    expected_by_month: dict[str, int] = collections.defaultdict(int)
    present: set[datetime] = set()
    front_by_day = {k: t for k, t, _v, _ in front}
    # we only have per-day counts; re-derive slot presence needs bars → approximate with counts
    for k, t in front_by_day.items():
        d = date.fromisoformat(k)
        exp = 0
        t0 = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
        for i in range(96):
            if product_session_active(root, t0 + timedelta(minutes=15 * i)):
                exp += 1
        got = day_bars[k].get(t, 0)
        expected_by_month[k[:7]] += exp
        missing_by_month[k[:7]] += max(exp - got, 0)
    months = sorted(expected_by_month)
    continuity = {m: {"expected_slots": expected_by_month[m], "missing_slots": missing_by_month[m],
                      "missing_pct": round(100.0 * missing_by_month[m] / expected_by_month[m], 2) if expected_by_month[m] else None}
                  for m in months}
    days_with_data = len(front)
    return {
        "tool": TOOL_VERSION, "root": root, "window": [start.isoformat(), end.isoformat()],
        "provider": {"name": "Polygon futures (sources/polygon_client.PolygonFuturesClient)",
                     "inventory_endpoint": "/futures/v1/contracts?product_code=<ROOT>",
                     "bars_endpoint": "/futures/v1/aggs/{DATED_TICKER}?resolution=15min",
                     "fetched_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")},
        "inventory": {"raw_rows": raw_rows, "unique_tickers": len(inventory),
                      "month_codes_listed": dict(sorted(codes.items(), key=lambda kv: MONTH_CODES.get(kv[0], 99))),
                      "trading_venues": sorted({str(r["trading_venue"]) for r in inventory}),
                      "listed_life_min": min((r["first_trade_date"] for r in inventory if r["first_trade_date"]), default=None),
                      "listed_life_max": max((r["last_trade_date"] for r in inventory if r["last_trade_date"]), default=None),
                      "contracts_in_window": [r["ticker"] for r in in_window]},
        "contracts": per_contract,
        "retention": {"earliest_bar_served": earliest.isoformat() if earliest else None,
                      "days_with_any_bar": days_with_data},
        "volume_front_chain": chain,
        "crossovers": crossovers,
        "chain_causally_ordered_by_expiry": monotone,
        "chain_flip_flops": flip_flops,
        "continuity_front_contract_by_month": continuity,
        "note": "volume-front chain and crossovers are provider evidence for proposing a roll rule; "
                "they are not the rule and not live-feed provenance",
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", required=True)
    ap.add_argument("--start", default="2024-09-01")
    ap.add_argument("--end", default=date.today().isoformat())
    ap.add_argument("--max-contracts", type=int, default=60)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    try:
        from dotenv import load_dotenv
        load_dotenv(REPO / ".env")
    except ImportError:
        pass
    client = PolygonFuturesClient(min_request_interval=13.0)
    if not client.configured:
        print("[t2] POLYGON_API_KEY not set", file=sys.stderr)
        return 1
    rep = probe(args.root.upper(), client, date.fromisoformat(args.start), date.fromisoformat(args.end),
                max_contracts=args.max_contracts)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(rep, fh, indent=1)
        fh.write("\n")
    inv = rep["inventory"]
    print(f"[t2] {rep['root']} inventory: {inv['unique_tickers']} tickers ({inv['raw_rows']} rows), codes={inv['month_codes_listed']}, "
          f"venues={inv['trading_venues']}, life {inv['listed_life_min']}..{inv['listed_life_max']}")
    print(f"[t2]   in window: {len(inv['contracts_in_window'])} contracts; earliest bar served {rep['retention']['earliest_bar_served']}; "
          f"days with data {rep['retention']['days_with_any_bar']}")
    for t, c in rep["contracts"].items():
        print(f"[t2]   {t:8s} listed {c['listed_first_trade']}..{c['listed_last_trade']} bars={c['bars']:6d} vol={c['volume']:.0f} "
              f"{(c['first_bar'] or '')[:10]}..{(c['last_bar'] or '')[:10]}")
    for c in rep["crossovers"]:
        print(f"[t2]   crossover {c['from']}→{c['to']} on {c['crossover_utc_day']} (prev-day vol {c['from_volume_prev_day']}/{c['to_volume_prev_day']})")
    print(f"[t2]   chain causally ordered: {rep['chain_causally_ordered_by_expiry']}  flip-flops: {rep['chain_flip_flops']}")
    for m, v in rep["continuity_front_contract_by_month"].items():
        print(f"[t2]   {m} expected {v['expected_slots']} missing {v['missing_slots']} ({v['missing_pct']}%)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
