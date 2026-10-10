"""Re-simulate options-scanner setups with time exits (research only).
usage: scan_resim.py MODE   MODE in {same, wk}
  same: the scanner's own selected contract; exits at EOD same day and next-day close
  wk  : ATM contract on the same underlying, first expiry >= 5 days out; same exits
"""
import json, os, sys, sqlite3, collections, statistics as st, math
from datetime import datetime, timedelta, timezone, date
from zoneinfo import ZoneInfo
MODE = sys.argv[1]
HERE = os.path.dirname(os.path.abspath(__file__))
import importlib.util
spec = importlib.util.spec_from_file_location("ob", os.path.join(HERE, "polylib.py")); pl = importlib.util.module_from_spec(spec); spec.loader.exec_module(pl)
ET = ZoneInfo("America/New_York")
DB = "/Users/djb.a.e/MAINVSCODE/autonomous-futures-system/private/trading-evidence-2026-10-09/options_scanner.sqlite"

c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
U, seen = [], set()
for id_, ts, tk, dr, status, si, sc in c.execute("select id,timestamp,ticker,direction,status,setup_inputs_json,selected_contract_json from options_shadow_journal where status in ('WIN','LOSS') order by id"):
    s = json.loads(si or "{}"); k = (tk, s.get("setup_type"), dr, s.get("setup_entry_trigger"), s.get("stop"))
    if k in seen: continue
    seen.add(k); U.append(dict(id=id_, ts=ts, tk=tk, dir=dr, st=status, s=s, sc=json.loads(sc or "{}")))

def trading_days_after(d, n):
    out = []; x = d
    while len(out) < n:
        x += timedelta(days=1)
        if x.weekday() < 5: out.append(x)
    return out

res = []
for u in U:
    t0 = datetime.fromisoformat(u["ts"]).astimezone(timezone.utc); d = t0.astimezone(ET).date()
    tm = int(t0.timestamp() * 1000)
    cp = "C" if u["dir"] == "LONG" else "P"
    if MODE == "same":
        con = u["sc"].get("contract") or u["s"].get("contract")
        if not con: continue
        tkr = "O:" + con
        hs = max(0.01, ((u["sc"].get("entry_ask") or 0) - (u["sc"].get("entry_bid") or 0)) / 2)
    else:
        px = u["s"].get("price")
        if not px: continue
        tkr = None
        for inc in (1.0, 2.5, 5.0, 10.0):
            k = round(px / inc) * inc
            for j in range(5, 12):
                e = d + timedelta(days=j)
                if e.weekday() >= 5: continue
                t = f"O:{u['tk']}{e.strftime('%y%m%d')}{cp}{int(round(k*1000)):08d}"
                if pl.minutes(t, d): tkr = t; break
            if tkr: break
        if not tkr: res.append(dict(id=u["id"], status="NO_CONTRACT")); continue
        hs = None
    bars0 = pl.minutes(tkr, d)
    pe, te = pl.px_at(bars0, tm, "entry")
    if pe is None or (te - tm) > 10 * 60000: res.append(dict(id=u["id"], status="NO_ENTRY")); continue
    if hs is None: hs = max(0.02, 0.015 * pe)
    row = dict(id=u["id"], status="OK", tk=u["tk"], typ=u["s"].get("setup_type"), lane=u["s"].get("paper_evidence_lane"), day=str(d), dir=u["dir"],
               orig=u["st"], tkr=tkr, entry=pe, hs=hs)
    cl = int(datetime.combine(d, datetime.strptime("15:59", "%H:%M").time(), ET).timestamp() * 1000)
    x0, tx = pl.px_at(bars0, cl, "exit"); row["eod"] = x0 if (x0 is not None and tx > te) else None
    for n, nd in enumerate(trading_days_after(d, 2), 1):
        b = pl.minutes(tkr, nd)
        cln = int(datetime.combine(nd, datetime.strptime("15:59", "%H:%M").time(), ET).timestamp() * 1000)
        xn, _ = pl.px_at(b, cln, "exit"); row[f"d{n}"] = xn
    res.append(row)
json.dump(res, open(os.path.join(HERE, f"scan_{MODE}.json"), "w"), indent=1)

def summ(label, R, key):
    R = [r for r in R if r.get(key) is not None]
    if len(R) < 8: return print(f"  {label:34s} {key}: n={len(R)}")
    pnl = [((r[key] - r["hs"]) - (r["entry"] + r["hs"])) * 100 - 1.30 for r in R]
    ds = sorted(r["day"] for r in R); mid = ds[len(ds) // 2]
    h1 = sum(p for p, r in zip(pnl, R) if r["day"] < mid); h2 = sum(p for p, r in zip(pnl, R) if r["day"] >= mid)
    t = st.mean(pnl) / (st.stdev(pnl) / math.sqrt(len(pnl)))
    print(f"  {label:34s} {key:3s}: n={len(R):3d} wins={sum(p>0 for p in pnl):3d} total=${sum(pnl):8,.0f} per=${st.mean(pnl):6.1f} t={t:5.2f} H1=${h1:7,.0f} H2=${h2:7,.0f} medPrem=${st.median(r['entry']*100 for r in R):.0f}")
OK = [r for r in res if r["status"] == "OK"]
print(MODE, collections.Counter(r["status"] for r in res), "days", min(r["day"] for r in OK), max(r["day"] for r in OK))
for key in ("eod", "d1", "d2"):
    summ("ALL", OK, key)
fam = lambda r: ("DAILY" if r["typ"].startswith("DAILY") else "H4" if r["typ"].startswith("H4") else "H1" if r["typ"].startswith("H1") else "212") if r["typ"] else "?"
for f in ("H1", "212", "H4", "DAILY"):
    for key in ("eod", "d1", "d2"): summ(f"family={f}", [r for r in OK if fam(r) == f], key)
for key in ("eod", "d1"):
    summ("LONG", [r for r in OK if r["dir"] == "LONG"], key); summ("SHORT", [r for r in OK if r["dir"] == "SHORT"], key)
