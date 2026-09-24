"""Audit robustness metrics from the archived Round-4 ledgers (read-only).
Usage: robustness.py <cache_dir> <ledger_dir> <out_json>"""
import sys, json
import numpy as np
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
CACHE, LED, OUT = sys.argv[1:4]
MULT = {"MNQ": 2.0, "MES": 5.0}
TICK = 0.25
OOS0 = date(2025, 9, 11)
DEFS = ["S_FULL", "S_EDGE", "L_FULL", "L_EDGE", "L_FULL_NY", "L_EDGE_NY"]


def tday(ts):
    return (datetime.fromtimestamp(int(ts), ET) + timedelta(hours=6)).date()


def conc(p):
    p = np.sort(np.asarray(p, float))[::-1]
    net = float(p.sum())
    k5 = max(1, int(np.ceil(0.05 * len(p)))) if len(p) else 0
    out = {"net": round(net, 2)}
    for lab, k in (("top1", 1), ("top3", 3), ("top5pct", k5)):
        s = float(p[:k].sum()) if len(p) >= k else float(p.sum())
        out[f"{lab}_n"] = int(min(k, len(p)))
        out[f"{lab}_share_of_net"] = round(s / net, 3) if net > 0 else None
        out[f"net_ex_{lab}"] = round(net - s, 2)
    return out


def summ(p, r):
    p = np.asarray(p, float)
    if not len(p):
        return {"n": 0}
    gw, gl = p[p > 0].sum(), -p[p < 0].sum()
    eq = np.cumsum(p)
    dd = float((np.maximum.accumulate(np.r_[0, eq])[1:] - eq).max())
    rm = np.asarray(r, float)
    return {"n": int(len(p)), "wins": int((p > 0).sum()), "losses": int((p <= 0).sum()),
            "net": round(float(p.sum()), 2), "pf": round(float(gw / gl), 3) if gl > 0 else None,
            "exp": round(float(p.mean()), 2), "maxdd": round(dd, 2),
            "R_mult": {"p10": round(float(np.percentile(rm, 10)), 2), "median": round(float(np.median(rm)), 2),
                       "p90": round(float(np.percentile(rm, 90)), 2), "max": round(float(rm.max()), 2),
                       "min": round(float(rm.min()), 2)},
            **conc(p)}


res = {}
for inst in ("MNQ", "MES"):
    z = np.load(f"{CACHE}/{inst}_5m.npz")
    T, H, L, C = z["t"], z["h"], z["l"], z["c"]
    for name in DEFS:
        led = np.load(f"{LED}/ledger_{inst}_{name}.npz")
        for meas in ("H2", "H15", "T2"):
            tk = led[f"{meas}_taken"]
            bar = led[f"{meas}_bar"][tk]
            pnl = led[f"{meas}_pnl"][tk]
            kind = led["kind"][tk]
            if meas == "T2":
                rpts = (led["top"] - led["bot"] + TICK)[tk]
            else:
                rpts = led["R"][tk]
            rmult = pnl / (rpts * MULT[inst])
            order = np.argsort(bar, kind="stable")
            bar, pnl, kind, rpts, rmult = bar[order], pnl[order], kind[order], rpts[order], rmult[order]
            days = np.array([tday(T[x]) for x in bar])
            cell = {}
            for per, m in (("ALL", np.ones(len(days), bool)), ("IS", days < OOS0), ("OOS", days >= OOS0)):
                cell[per] = summ(pnl[m], rmult[m])
            hy = {}
            for d0, lab in ((date(2024, 7, 1), "2024H2"), (date(2025, 1, 1), "2025H1"),
                            (date(2025, 7, 1), "2025H2"), (date(2026, 1, 1), "2026H1")):
                d1 = date(d0.year + (d0.month == 7), 1 if d0.month == 7 else 7, 1)
                m = (days >= d0) & (days < d1)
                hy[lab] = {"n": int(m.sum()), "net": round(float(pnl[m].sum()), 2)}
            cell["half_years"] = hy
            u, cnt = np.unique(days, return_counts=True)
            cell["trades_per_day"] = {"days": int(len(u)), "days_with_2plus": int((cnt >= 2).sum()),
                                      "max_per_day": int(cnt.max()) if len(cnt) else 0}
            cell["by_side_ALL"] = {"demand": summ(pnl[kind == 1], rmult[kind == 1]).get("net"),
                                   "supply": summ(pnl[kind == -1], rmult[kind == -1]).get("net")}
            res[f"{inst}:{name}:{meas}"] = cell
        # MAE/MFE in R for the hold trades (H2), entry bar .. exit bar (exit found by re-walk)
    for name in ("L_FULL", "L_EDGE", "L_FULL_NY"):
        led = np.load(f"{LED}/ledger_{inst}_{name}.npz")
        tk = led["H2_taken"]
        idx = np.flatnonzero(tk)
        maes, mfes = [], []
        for j in idx:
            e = int(led["H2_bar"][j]); d = int(led["kind"][j]); R = float(led["R"][j])
            entry = float(z["o"][e]) + d * TICK
            stop = entry - d * R
            tgt = entry + d * 2 * R
            dt = datetime.fromtimestamp(int(T[e]), ET).date()
            x = e
            mae = mfe = 0.0
            while x < len(T) and datetime.fromtimestamp(int(T[x]), ET).date() == dt:
                em = datetime.fromtimestamp(int(T[x]), ET)
                if em.hour * 60 + em.minute >= 960:
                    break
                adv = (entry - L[x]) if d == 1 else (H[x] - entry)
                fav = (H[x] - entry) if d == 1 else (entry - L[x])
                mae, mfe = max(mae, adv), max(mfe, fav)
                if ((L[x] <= stop) if d == 1 else (H[x] >= stop)) or ((H[x] >= tgt + TICK) if d == 1 else (L[x] <= tgt - TICK)):
                    break
                x += 1
            maes.append(mae / R); mfes.append(mfe / R)
        res[f"{inst}:{name}:H2:mae_mfe_R"] = {
            "median_MAE_R": round(float(np.median(maes)), 2), "median_MFE_R": round(float(np.median(mfes)), 2),
            "share_MFE_ge_1R": round(float(np.mean(np.array(mfes) >= 1)), 3),
            "share_MFE_ge_2R": round(float(np.mean(np.array(mfes) >= 2)), 3)}

json.dump(res, open(OUT, "w"), indent=1)
for k in ("MNQ:L_FULL:H2", "MNQ:L_FULL:H15", "MNQ:L_FULL:T2", "MNQ:L_FULL_NY:H2", "MNQ:L_EDGE:H2", "MES:L_FULL:H2"):
    c = res[k]
    print(k, "| ALL", {q: c["ALL"].get(q) for q in ("n", "net", "pf", "top1_share_of_net", "net_ex_top1", "top3_share_of_net",
                                                   "net_ex_top3", "top5pct_n", "net_ex_top5pct")})
    print("   OOS", {q: c["OOS"].get(q) for q in ("n", "net", "pf", "net_ex_top1", "net_ex_top3", "net_ex_top5pct")},
          "R", c["ALL"].get("R_mult"), "perday", c["trades_per_day"], "HY", {h: v["net"] for h, v in c["half_years"].items()})
for k in [k for k in res if k.endswith("mae_mfe_R")]:
    print(k, res[k])
