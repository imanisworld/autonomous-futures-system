"""4HR MNQ signals expressed as ATM QQQ options held to the close. Research only."""
import gzip, json, os, sys, time, urllib.request, urllib.parse, hashlib, statistics as st, math, collections
from datetime import datetime, timedelta, timezone, date
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
KEY = open(os.path.join(os.environ["TMPDIR"], "pk")).read().strip()
CACHE = os.path.join(os.path.dirname(__file__), "cache2"); os.makedirs(CACHE, exist_ok=True)
SRC = "/Users/djb.a.e/MAINVSCODE/autonomous-futures-system/scripts/edge_decomposition_audit_results_candidates.jsonl.gz"

def get(path, params=None):
    params = dict(params or {}); q = urllib.parse.urlencode(params)
    ck = os.path.join(CACHE, hashlib.sha1((path + "?" + q).encode()).hexdigest() + ".json")
    if os.path.exists(ck): return json.load(open(ck))
    params["apiKey"] = KEY
    url = "https://api.polygon.io" + path + "?" + urllib.parse.urlencode(params)
    for i in range(4):
        try:
            with urllib.request.urlopen(url, timeout=30) as r: d = json.load(r)
            json.dump(d, open(ck, "w")); return d
        except Exception as e:
            err = str(e).replace(KEY, "***"); time.sleep(1.5 * (i + 1))
    print("FAIL", path, err, file=sys.stderr); return None

def nearest_expiry(d, min_days, est):
    for k in range(min_days, min_days + 8):
        e = d + timedelta(days=k)
        if e.weekday() >= 5: continue
        if minutes(occ(e, "C", float(round(est))), d): return e
    return None

def occ(exp, cp, k):
    return f"O:QQQ{exp.strftime('%y%m%d')}{cp}{int(round(k*1000)):08d}"

def minutes(tkr, d):
    r = get(f"/v2/aggs/ticker/{tkr}/range/1/minute/{d.isoformat()}/{d.isoformat()}", {"adjusted": "true", "sort": "asc", "limit": 50000})
    return (r or {}).get("results") or []

def px_at(bars, t_ms, side):
    """side='entry': first bar at/after t; 'exit': last bar at/before t."""
    if side == "entry":
        for b in bars:
            if b["t"] >= t_ms: return b["vw"] if b.get("vw") else b["o"], b["t"]
    else:
        prev = None
        for b in bars:
            if b["t"] <= t_ms: prev = b
        if prev: return prev.get("vw") or prev["c"], prev["t"]
    return None, None

def pick_atm(exp, d, est, t_ms):
    k = float(round(est))
    for _ in range(4):  # put-call parity: S ~ K + C - P ; QQQ $1 strikes
        c, _t = px_at(minutes(occ(exp, "C", k), d), t_ms, "entry")
        p, _t = px_at(minutes(occ(exp, "P", k), d), t_ms, "entry")
        if c is None or p is None: return k
        k2 = float(round(k + c - p))
        if k2 == k: return k
        k = k2
    return k

rows = [json.loads(l) for l in gzip.open(SRC, "rt")]
sig = sorted([r for r in rows if r["lane"] == "4hr_mnq" and r.get("control", {}).get("EOD")], key=lambda r: r["bar_ts"])
ratio = None
out = []
for mode, min_days in (("0-1DTE", 0), ("~7DTE", 6)):
    for r in sig:
        ts = datetime.fromisoformat(r["bar_ts"]); entry_t = ts + timedelta(minutes=5)
        d = entry_t.astimezone(ET).date()
        close_et = datetime.combine(d, datetime.strptime("15:59", "%H:%M").time(), ET)
        eod = datetime.fromisoformat(r["control"]["EOD"]["exit_bar_ts"]) + timedelta(minutes=5)
        exit_t = min(eod, close_et.astimezone(timezone.utc))
        est = r["gates"]["decision_close"] / (ratio or 41.0)
        exp = nearest_expiry(d, min_days, est)
        if not exp: out.append(dict(mode=mode, date=str(d), status="NO_EXPIRY")); continue
        k = pick_atm(exp, d, est, int(entry_t.timestamp() * 1000))
        if k is None: out.append(dict(mode=mode, date=str(d), status="NO_STRIKES")); continue
        cp = "C" if r["direction"] == "LONG" else "P"
        bars = minutes(occ(exp, cp, k), d)
        e, et_ = px_at(bars, int(entry_t.timestamp() * 1000), "entry")
        x, xt = px_at(bars, int(exit_t.timestamp() * 1000), "exit")
        if e is None or x is None or xt <= et_:
            out.append(dict(mode=mode, date=str(d), status="NO_PRICE")); continue
        lag = (et_ - entry_t.timestamp() * 1000) / 60000
        out.append(dict(mode=mode, date=str(d), status="OK", dir=r["direction"], exp=str(exp), strike=k, entry=e, exit=x,
                        entry_lag_min=round(lag, 1), mnq_eod_net=r["control"]["EOD"]["net"]))
json.dump(out, open(os.path.join(os.path.dirname(__file__), "results.json"), "w"), indent=1)

def summ(mode, half_spread):
    R = [o for o in out if o["mode"] == mode and o["status"] == "OK" and o["entry_lag_min"] <= 10]
    pnl = [((o["exit"] - half_spread) - (o["entry"] + half_spread)) * 100 - 1.30 for o in R]
    if len(pnl) < 3: return print(mode, "too few", len(R))
    ds = sorted(o["date"] for o in R); mid = ds[len(ds) // 2]
    h1 = sum(p for p, o in zip(pnl, R) if o["date"] < mid); h2 = sum(p for p, o in zip(pnl, R) if o["date"] >= mid)
    m = collections.defaultdict(float)
    for p, o in zip(pnl, R): m[o["date"][:7]] += p
    tot = sum(pnl); top3 = sum(sorted(m.values(), reverse=True)[:3])
    eq = pk = dd = 0
    for p in pnl: eq += p; pk = max(pk, eq); dd = max(dd, pk - eq)
    t = st.mean(pnl) / (st.stdev(pnl) / math.sqrt(len(pnl)))
    prem = st.median(o["entry"] * 100 for o in R)
    print(f"{mode:7s} spread/side=${half_spread:.2f}: n={len(R)} wins={sum(p>0 for p in pnl)} total=${tot:,.0f} per=${st.mean(pnl):.0f} t={t:.2f} "
          f"H1=${h1:,.0f} H2=${h2:,.0f} maxDD=${dd:,.0f} top3mo={top3/tot*100 if tot>0 else float('nan'):.0f}% medianPremium=${prem:.0f} worst=${min(pnl):.0f}")

print(collections.Counter((o["mode"], o["status"]) for o in out))
for mode in ("0-1DTE", "~7DTE"):
    for hs in (0.01, 0.03, 0.05): summ(mode, hs)
