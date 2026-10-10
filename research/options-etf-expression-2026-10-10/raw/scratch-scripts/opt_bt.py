"""Futures-signal -> ETF option backtest (research only).
usage: opt_bt.py LANE UNDERLYING RATIO MIN_DAYS WIDTH(0=single) TAG
"""
import gzip, json, os, sys, time, urllib.request, urllib.parse, hashlib, statistics as st, math, collections
from datetime import datetime, timedelta, timezone, date
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
KEY = open(os.path.join(os.environ["TMPDIR"], "pk")).read().strip()
HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "cache2"); os.makedirs(CACHE, exist_ok=True)
SRC = "/Users/djb.a.e/MAINVSCODE/autonomous-futures-system/scripts/edge_decomposition_audit_results_candidates.jsonl.gz"
LANE, UND, RATIO, MIN_DAYS, WIDTH, TAG = sys.argv[1], sys.argv[2], float(sys.argv[3]), int(sys.argv[4]), float(sys.argv[5]), sys.argv[6]
OTM = float(sys.argv[7]) if len(sys.argv) > 7 else 0.0

def get(path, params=None):
    params = dict(params or {}); q = urllib.parse.urlencode(params)
    ck = os.path.join(CACHE, hashlib.sha1((path + "?" + q).encode()).hexdigest() + ".json")
    if os.path.exists(ck):
        try: return json.load(open(ck))
        except ValueError: pass
    params["apiKey"] = KEY
    url = "https://api.polygon.io" + path + "?" + urllib.parse.urlencode(params)
    err = ""
    for i in range(6):
        try:
            with urllib.request.urlopen(url, timeout=60) as r: d = json.load(r)
            tmp = ck + f".{os.getpid()}.tmp"; json.dump(d, open(tmp, "w")); os.replace(tmp, ck); return d
        except Exception as e:
            err = str(e).replace(KEY, "***"); time.sleep(2 * (i + 1))
    print("FAIL", path, err, file=sys.stderr); return None

def occ(exp, cp, k): return f"O:{UND}{exp.strftime('%y%m%d')}{cp}{int(round(k*1000)):08d}"
def minutes(tkr, d):
    r = get(f"/v2/aggs/ticker/{tkr}/range/1/minute/{d.isoformat()}/{d.isoformat()}", {"adjusted": "true", "sort": "asc", "limit": 50000})
    return (r or {}).get("results") or []
def px_at(bars, t_ms, side):
    if side == "entry":
        for b in bars:
            if b["t"] >= t_ms: return (b.get("vw") or b["o"]), b["t"]
    else:
        prev = None
        for b in bars:
            if b["t"] <= t_ms: prev = b
        if prev: return (prev.get("vw") or prev["c"]), prev["t"]
    return None, None
def nearest_expiry(d, min_days, k):
    for j in range(min_days, min_days + 8):
        e = d + timedelta(days=j)
        if e.weekday() >= 5: continue
        if minutes(occ(e, "C", k), d): return e
    return None
def pick_atm(exp, d, k, t_ms):
    for _ in range(5):
        c, _ = px_at(minutes(occ(exp, "C", k), d), t_ms, "entry")
        p, _ = px_at(minutes(occ(exp, "P", k), d), t_ms, "entry")
        if c is None or p is None: return k, None
        k2 = float(round(k + c - p))
        if k2 == k: return k, k + c - p
        k = k2
    return k, None

rows = [json.loads(l) for l in gzip.open(SRC, "rt")]
sig = sorted([r for r in rows if r["lane"] == LANE and r.get("control", {}).get("EOD")], key=lambda r: r["bar_ts"])
out = []
for r in sig:
    entry_t = datetime.fromisoformat(r["bar_ts"]) + timedelta(minutes=5)
    d = entry_t.astimezone(ET).date()
    close_et = datetime.combine(d, datetime.strptime("15:59", "%H:%M").time(), ET).astimezone(timezone.utc)
    exit_t = min(datetime.fromisoformat(r["control"]["EOD"]["exit_bar_ts"]) + timedelta(minutes=5), close_et)
    k0 = float(round(r["gates"]["decision_close"] / RATIO))
    exp = nearest_expiry(d, MIN_DAYS, k0)
    base = dict(date=str(d), dir=r["direction"], bar_ts=r["bar_ts"])
    if not exp: out.append(dict(base, status="NO_EXPIRY")); continue
    tm = int(entry_t.timestamp() * 1000); xm = int(exit_t.timestamp() * 1000)
    k, spot = pick_atm(exp, d, k0, tm)
    cp = "C" if r["direction"] == "LONG" else "P"
    if OTM and spot:
        k = float(round(spot * (1 + OTM / 100) if cp == "C" else spot * (1 - OTM / 100)))
    legs = [(k, +1)]
    if WIDTH: legs.append((k + WIDTH if cp == "C" else k - WIDTH, -1))
    e = x = 0.0; ok = True; lag = 0
    for kk, sgn in legs:
        bars = minutes(occ(exp, cp, kk), d)
        pe, te = px_at(bars, tm, "entry"); px, tx = px_at(bars, xm, "exit")
        if pe is None or px is None or tx <= te: ok = False; break
        e += sgn * pe; x += sgn * px; lag = max(lag, (te - tm) / 60000)
    if not ok or lag > 10: out.append(dict(base, status="NO_PRICE")); continue
    out.append(dict(base, status="OK", exp=str(exp), strike=k, spot=spot, nlegs=len(legs), entry=e, exit=x, lag=lag))
json.dump(out, open(os.path.join(HERE, f"res_{TAG}.json"), "w"), indent=1)

def summ(hs):
    R = [o for o in out if o["status"] == "OK"]
    pnl = [((o["exit"] - hs * o["nlegs"]) - (o["entry"] + hs * o["nlegs"])) * 100 - 1.30 * o["nlegs"] for o in R]
    if len(pnl) < 5: return print(TAG, "too few", len(pnl))
    ds = sorted(o["date"] for o in R); mid = ds[len(ds) // 2]
    h1 = sum(p for p, o in zip(pnl, R) if o["date"] < mid); h2 = sum(p for p, o in zip(pnl, R) if o["date"] >= mid)
    m = collections.defaultdict(float)
    for p, o in zip(pnl, R): m[o["date"][:7]] += p
    tot = sum(pnl); top3 = sum(sorted(m.values(), reverse=True)[:3])
    eq = pk = dd = 0
    for p in pnl: eq += p; pk = max(pk, eq); dd = max(dd, pk - eq)
    t = st.mean(pnl) / (st.stdev(pnl) / math.sqrt(len(pnl)))
    prem = st.median(o["entry"] * 100 for o in R)
    yrs = collections.defaultdict(float)
    for p, o in zip(pnl, R): yrs[o["date"][:4]] += p
    print(f"{TAG:24s} hs=${hs:.2f} n={len(R)} wins={sum(p>0 for p in pnl)} total=${tot:,.0f} per=${st.mean(pnl):.0f} t={t:.2f} H1=${h1:,.0f} H2=${h2:,.0f} "
          f"maxDD=${dd:,.0f} top3={top3/tot*100 if tot>0 else float('nan'):.0f}% medCost=${prem:.0f} worst=${min(pnl):.0f} yrs={ {k: round(v) for k, v in yrs.items()} }")
print(TAG, collections.Counter(o["status"] for o in out))
for hs in (0.01, 0.03): summ(hs)
