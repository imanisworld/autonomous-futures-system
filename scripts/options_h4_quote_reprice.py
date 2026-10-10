#!/usr/bin/env python3
"""Re-price the options-scanner H4 setups with real NBBO quotes (research only).

Population (same as the 2026-10-10 time-exit re-sim, "wk" mode):
  options_shadow_journal rows with status WIN/LOSS, deduplicated to the first
  sighting of each (ticker, setup_type, direction, setup_entry_trigger, stop),
  restricted to setup_type starting with "H4".
Contract: ATM on the setup's own underlying, first listed expiry 5-11 calendar
  days after the signal date, strike increment probed in (1, 2.5, 5, 10) - the
  first strike/expiry whose minute bars exist on the signal date.
Entry: first NBBO quote at/after the journal timestamp (<= 10 min), at the ASK.
Exit: last NBBO quote at/before 15:59 ET on the next weekday session, at the BID.
Skip: entry ask x 100 > $300 -> SKIPPED_OVER_300 (not traded).
P&L: (exit_bid - entry_ask) x 100 - $1.30 commission. One contract.

Never places orders, never touches a broker or runtime config.

usage: POLYGON_API_KEY=... python scripts/options_h4_quote_reprice.py DB OUTDIR [--cache DIR]
"""
from __future__ import annotations

import argparse
import collections
import csv
import hashlib
import json
import math
import os
import sqlite3
import statistics as st
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
MAX_PREMIUM = 300.0
COMMISSION_RT = 1.30
MAX_LAG_NS = 10 * 60 * 10**9


class Poly:
    def __init__(self, cache: Path):
        self.key = os.environ["POLYGON_API_KEY"].strip()
        self.cache = cache
        cache.mkdir(parents=True, exist_ok=True)

    def get(self, path: str, params: dict) -> dict | None:
        q = urllib.parse.urlencode(params)
        ck = self.cache / (hashlib.sha1((path + "?" + q).encode()).hexdigest() + ".json")
        if ck.exists():
            try:
                return json.loads(ck.read_text())
            except ValueError:
                pass
        url = "https://api.polygon.io" + path + "?" + urllib.parse.urlencode({**params, "apiKey": self.key})
        for attempt in range(6):
            try:
                with urllib.request.urlopen(url, timeout=60) as r:
                    data = json.load(r)
                tmp = ck.with_name(ck.name + f".{os.getpid()}.tmp")
                tmp.write_text(json.dumps(data))
                os.replace(tmp, ck)
                return data
            except Exception:  # noqa: BLE001 - transient provider/proxy failure
                time.sleep(2 * (attempt + 1))
        return None

    def minutes(self, tkr: str, d: date) -> list[dict]:
        r = self.get(f"/v2/aggs/ticker/{tkr}/range/1/minute/{d.isoformat()}/{d.isoformat()}",
                     {"adjusted": "true", "sort": "asc", "limit": 50000})
        return (r or {}).get("results") or []

    def quote(self, tkr: str, t_ns: int, side: str) -> dict | None:
        if side == "entry":
            p = {"timestamp.gte": t_ns, "order": "asc", "sort": "timestamp", "limit": 1}
        else:
            p = {"timestamp.lte": t_ns, "order": "desc", "sort": "timestamp", "limit": 1}
        r = self.get(f"/v3/quotes/{tkr}", p)
        res = (r or {}).get("results") or []
        return res[0] if res else None


def next_weekday(d: date) -> date:
    x = d + timedelta(days=1)
    while x.weekday() >= 5:
        x += timedelta(days=1)
    return x


def load_setups(db: str) -> list[dict]:
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    out, seen = [], set()
    for id_, ts, tk, dr, status, si in c.execute(
            "select id,timestamp,ticker,direction,status,setup_inputs_json from options_shadow_journal "
            "where status in ('WIN','LOSS') order by id"):
        s = json.loads(si or "{}")
        key = (tk, s.get("setup_type"), dr, s.get("setup_entry_trigger"), s.get("stop"))
        if key in seen:
            continue
        seen.add(key)
        if not str(s.get("setup_type") or "").startswith("H4"):
            continue
        out.append({"journal_id": id_, "ts": ts, "ticker": tk, "direction": dr, "journal_status": status,
                    "setup_type": s.get("setup_type"), "paper_lane": s.get("paper_evidence_lane"),
                    "underlying_price": s.get("price"), "trigger": s.get("setup_entry_trigger"), "stop": s.get("stop"),
                    "target_1": s.get("target_1")})
    return out


def pick_contract(px: Poly, u: dict, d: date) -> str | None:
    cp = "C" if u["direction"] == "LONG" else "P"
    price = u["underlying_price"]
    if not price:
        return None
    for inc in (1.0, 2.5, 5.0, 10.0):
        k = round(price / inc) * inc
        for j in range(5, 12):
            e = d + timedelta(days=j)
            if e.weekday() >= 5:
                continue
            t = f"O:{u['ticker']}{e.strftime('%y%m%d')}{cp}{int(round(k * 1000)):08d}"
            if px.minutes(t, d):
                return t
    return None


def tstat(x: list[float]) -> float:
    return st.mean(x) / (st.stdev(x) / math.sqrt(len(x))) if len(x) > 2 and st.stdev(x) > 0 else float("nan")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    ap.add_argument("outdir")
    ap.add_argument("--cache", default=None)
    ap.add_argument("--max-premium", type=float, default=MAX_PREMIUM)
    a = ap.parse_args()
    out = Path(a.outdir)
    out.mkdir(parents=True, exist_ok=True)
    px = Poly(Path(a.cache) if a.cache else out / "cache")

    rows = []
    for u in load_setups(a.db):
        t0 = datetime.fromisoformat(u["ts"]).astimezone(timezone.utc)
        d = t0.astimezone(ET).date()
        xd = next_weekday(d)
        exit_t = datetime.combine(xd, datetime.strptime("15:59", "%H:%M").time(), ET).astimezone(timezone.utc)
        r = {**u, "signal_date": d.isoformat(), "exit_date": xd.isoformat()}
        tkr = pick_contract(px, u, d)
        if not tkr:
            rows.append({**r, "status": "NO_CONTRACT"})
            continue
        r["contract"] = tkr
        t0_ns = int(t0.timestamp() * 10**9)
        q_in = px.quote(tkr, t0_ns, "entry")
        q_out = px.quote(tkr, int(exit_t.timestamp() * 10**9), "exit")
        if not q_in or (q_in["sip_timestamp"] - t0_ns) > MAX_LAG_NS:
            rows.append({**r, "status": "NO_ENTRY_QUOTE"})
            continue
        ask, bid_in = q_in.get("ask_price") or 0, q_in.get("bid_price") or 0
        r.update(entry_quote_ts=datetime.fromtimestamp(q_in["sip_timestamp"] / 1e9, timezone.utc).isoformat(),
                 entry_bid=bid_in, entry_ask=ask)
        if ask <= 0 or ask < bid_in:
            rows.append({**r, "status": "BAD_ENTRY_QUOTE"})
            continue
        if ask * 100 > a.max_premium:
            rows.append({**r, "status": "SKIPPED_OVER_300"})
            continue
        if not q_out or datetime.fromtimestamp(q_out["sip_timestamp"] / 1e9, timezone.utc).astimezone(ET).date() != xd:
            rows.append({**r, "status": "NO_EXIT_QUOTE"})
            continue
        bid, ask_out = q_out.get("bid_price") or 0, q_out.get("ask_price") or 0
        r.update(exit_quote_ts=datetime.fromtimestamp(q_out["sip_timestamp"] / 1e9, timezone.utc).isoformat(),
                 exit_bid=bid, exit_ask=ask_out,
                 pnl=round((bid - ask) * 100 - COMMISSION_RT, 2), status="OK")
        rows.append(r)

    rows.sort(key=lambda r: r["ts"])
    (out / "trades.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    fields = ["journal_id", "ts", "signal_date", "exit_date", "ticker", "setup_type", "direction", "journal_status", "paper_lane",
              "underlying_price", "trigger", "stop", "target_1", "contract", "entry_quote_ts", "entry_bid", "entry_ask",
              "exit_quote_ts", "exit_bid", "exit_ask", "pnl", "status"]
    with (out / "trades.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    ok = [r for r in rows if r["status"] == "OK"]
    pnl = [r["pnl"] for r in ok]
    summary = {"max_premium": a.max_premium, "population_h4_unique_setups": len(rows), "status_counts": dict(collections.Counter(r["status"] for r in rows))}
    if len(pnl) >= 3:
        days = sorted({r["signal_date"] for r in ok})
        mid = sorted(r["signal_date"] for r in ok)[len(ok) // 2]
        by_day = collections.defaultdict(float)
        for r in ok:
            by_day[r["signal_date"]] += r["pnl"]
        by_type = collections.defaultdict(list)
        for r in ok:
            by_type[r["setup_type"]].append(r["pnl"])
        by_dir = collections.defaultdict(list)
        for r in ok:
            by_dir[r["direction"]].append(r["pnl"])
        eq = pk = dd = 0.0
        for p in pnl:
            eq += p
            pk = max(pk, eq)
            dd = max(dd, pk - eq)
        summary.update({
            "traded": len(ok), "distinct_signal_days": len(days), "first_day": days[0], "last_day": days[-1],
            "wins": sum(p > 0 for p in pnl), "net": round(sum(pnl), 2), "mean": round(st.mean(pnl), 2),
            "t_trade": round(tstat(pnl), 2), "t_day_clustered": round(tstat(list(by_day.values())), 2),
            "h1_net": round(sum(r["pnl"] for r in ok if r["signal_date"] < mid), 2),
            "h2_net": round(sum(r["pnl"] for r in ok if r["signal_date"] >= mid), 2),
            "max_drawdown": round(dd, 2),
            "best_day_share_of_net": round(max(by_day.values()) / sum(pnl), 3) if sum(pnl) > 0 else None,
            "median_entry_cost": round(st.median(r["entry_ask"] * 100 for r in ok), 2),
            "median_entry_spread_pct": round(st.median((r["entry_ask"] - r["entry_bid"]) / r["entry_ask"] * 100 for r in ok), 2),
            "by_setup_type": {k: {"n": len(v), "net": round(sum(v), 2), "wins": sum(x > 0 for x in v)} for k, v in sorted(by_type.items())},
            "by_direction": {k: {"n": len(v), "net": round(sum(v), 2), "wins": sum(x > 0 for x in v)} for k, v in sorted(by_dir.items())},
            "by_day": {k: round(v, 2) for k, v in sorted(by_day.items())},
        })
    (out / "summary.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps({k: v for k, v in summary.items() if k != "by_day"}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
