#!/usr/bin/env python3
"""4HR Re-Trigger (MNQ/MES) signals expressed as ~7DTE ATM ETF options.

Paper / research only.  It never places orders, never touches a broker, and
never changes runtime configuration.

Pipeline, frozen per docs/prereg-options-4hr-etf-forward-2026-10-10.md:
  1. Continuous front-month 5-minute bars (Polygon futures) for MNQ and MES.
  2. Canonical pure state machine ``strategy.four_hr_retrigger.advance_4hr_retrigger``,
     walked bar by bar inside 09:30-11:00 ET exactly as
     ``scripts/edge_decomposition_audit.extract_state_machine`` walks it.
  3. Each candidate -> ETF option (MNQ->QQQ, MES->SPY): LONG buys a call,
     SHORT buys a put; strike = ATM by put-call parity on $1 strikes; expiry =
     first listed expiry >= 6 calendar days after the signal date.
  4. Entry = first option minute bar at/after the candidate's entry time
     (<= 10 minutes lag, else NO_ENTRY); exit = last minute bar at/before
     15:59 ET the same day.  Price = minute VWAP.  Costs = $0.03 per side
     half-spread haircut + $0.65 commission per side.  One contract.

usage:
  POLYGON_API_KEY=... python scripts/options_4hr_etf_forward.py START END [--out DIR]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from sources.polygon_client import PolygonFuturesClient  # noqa: E402
from strategy.four_hr_retrigger import advance_4hr_retrigger  # noqa: E402

ET = ZoneInfo("America/New_York")
ETF = {"MNQ": ("QQQ", 41.0), "MES": ("SPY", 10.0)}
HALF_SPREAD = 0.03
COMMISSION_PER_SIDE = 0.65
MIN_DAYS_TO_EXPIRY = 6
MAX_ENTRY_LAG_MIN = 10
BARS_PER_DAY = 276
WINDOW = ((9, 30), (11, 0))


def _key() -> str:
    k = os.environ.get("POLYGON_API_KEY", "").strip()
    if not k:
        raise SystemExit("POLYGON_API_KEY is not set")
    return k


class OptionPrices:
    def __init__(self, cache_dir: Path):
        self.cache = cache_dir
        self.cache.mkdir(parents=True, exist_ok=True)
        self.key = _key()

    def minutes(self, ticker: str, d: date) -> list[dict]:
        path = f"/v2/aggs/ticker/{ticker}/range/1/minute/{d.isoformat()}/{d.isoformat()}"
        params = {"adjusted": "true", "sort": "asc", "limit": 50000}
        ck = self.cache / (hashlib.sha1((path + json.dumps(params, sort_keys=True)).encode()).hexdigest() + ".json")
        if ck.exists():
            try:
                return json.loads(ck.read_text()).get("results") or []
            except ValueError:
                pass
        url = "https://api.polygon.io" + path + "?" + urllib.parse.urlencode({**params, "apiKey": self.key})
        for attempt in range(6):
            try:
                with urllib.request.urlopen(url, timeout=60) as r:
                    data = json.load(r)
                tmp = ck.with_suffix(f".{os.getpid()}.tmp")
                tmp.write_text(json.dumps(data))
                os.replace(tmp, ck)
                return data.get("results") or []
            except Exception:  # noqa: BLE001 - transient provider/proxy failures
                time.sleep(2 * (attempt + 1))
        return []


def occ(und: str, exp: date, cp: str, strike: float) -> str:
    return f"O:{und}{exp.strftime('%y%m%d')}{cp}{int(round(strike * 1000)):08d}"


def price_at(bars: list[dict], t_ms: int, side: str):
    if side == "entry":
        for b in bars:
            if b["t"] >= t_ms:
                return (b.get("vw") or b["o"]), b["t"]
        return None, None
    prev = None
    for b in bars:
        if b["t"] <= t_ms:
            prev = b
    return ((prev.get("vw") or prev["c"]), prev["t"]) if prev else (None, None)


def candidates(client: PolygonFuturesClient, instrument: str, start: date, end: date) -> list[dict]:
    bars = [b.to_dict() for b in client.fetch_continuous(instrument, start - timedelta(days=10), end, timeframe_minutes=5)]
    for b in bars:
        b["_dt"] = datetime.fromisoformat(b["ts"])
    out, state, day = [], {}, None
    for i, b in enumerate(bars):
        et = b["_dt"].astimezone(ET)
        if et.date() != day:
            day, state = et.date(), {}
        if not (start <= day <= end):
            continue
        if not (WINDOW[0] <= (et.hour, et.minute) < WINDOW[1]):
            continue
        if state.get("status") in {"TRIGGERED", "INVALIDATED", "EXPIRED"}:
            continue
        window = bars[max(0, i - BARS_PER_DAY * 5): i + 1]
        state, cand = advance_4hr_retrigger(bars_5m=window, current_bar_ts=b["_dt"], instrument=instrument, persisted_state=state)
        if cand is not None:
            out.append({"instrument": instrument, "date": day.isoformat(), "bar_ts": b["ts"], "direction": cand["direction"],
                        "entry": cand["entry"], "stop": cand["stop"], "target": cand["target"],
                        "entry_time": cand["entry_time"].isoformat(), "decision_close": b["close"]})
    return out


def option_trade(px: OptionPrices, c: dict) -> dict:
    und, ratio = ETF[c["instrument"]]
    entry_t = datetime.fromisoformat(c["entry_time"]).astimezone(timezone.utc)
    d = entry_t.astimezone(ET).date()
    t_ms = int(entry_t.timestamp() * 1000)
    exit_ms = int(datetime.combine(d, datetime.strptime("15:59", "%H:%M").time(), ET).timestamp() * 1000)
    cp = "C" if c["direction"] == "LONG" else "P"
    k = float(round(c["decision_close"] / ratio))
    exp = None
    for j in range(MIN_DAYS_TO_EXPIRY, MIN_DAYS_TO_EXPIRY + 8):
        e = d + timedelta(days=j)
        if e.weekday() < 5 and px.minutes(occ(und, e, "C", k), d):
            exp = e
            break
    if exp is None:
        return {**c, "status": "NO_EXPIRY"}
    for _ in range(5):  # put-call parity: S ~ K + C - P
        cc, _ = price_at(px.minutes(occ(und, exp, "C", k), d), t_ms, "entry")
        pp, _ = price_at(px.minutes(occ(und, exp, "P", k), d), t_ms, "entry")
        if cc is None or pp is None:
            break
        k2 = float(round(k + cc - pp))
        if k2 == k:
            break
        k = k2
    ticker = occ(und, exp, cp, k)
    bars = px.minutes(ticker, d)
    pe, te = price_at(bars, t_ms, "entry")
    pxit, tx = price_at(bars, exit_ms, "exit")
    if pe is None or pxit is None or tx <= te or (te - t_ms) > MAX_ENTRY_LAG_MIN * 60000:
        return {**c, "status": "NO_ENTRY", "option": ticker}
    pnl = ((pxit - HALF_SPREAD) - (pe + HALF_SPREAD)) * 100 - 2 * COMMISSION_PER_SIDE
    return {**c, "status": "OK", "option": ticker, "underlying": und, "expiry": exp.isoformat(), "strike": k,
            "entry_price": round(pe, 4), "exit_price": round(pxit, 4), "max_loss": round((pe + HALF_SPREAD) * 100 + 2 * COMMISSION_PER_SIDE, 2),
            "pnl": round(pnl, 2)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("start", type=date.fromisoformat)
    ap.add_argument("end", type=date.fromisoformat)
    ap.add_argument("--out", default=str(REPO / "logs" / "options_4hr_etf_forward"))
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    client = PolygonFuturesClient(api_key=_key())
    px = OptionPrices(out / "cache")
    rows = []
    for inst in ("MNQ", "MES"):
        for c in candidates(client, inst, a.start, a.end):
            rows.append(option_trade(px, c))
    rows.sort(key=lambda r: r["entry_time"])
    (out / f"trades_{a.start}_{a.end}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    ok = [r for r in rows if r["status"] == "OK"]
    tot = sum(r["pnl"] for r in ok)
    print(f"signals={len(rows)} priced={len(ok)} wins={sum(r['pnl'] > 0 for r in ok)} total=${tot:,.2f}")
    for r in rows:
        print(r["date"], r["instrument"], r["direction"], r["status"], r.get("option", ""), r.get("pnl", ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
