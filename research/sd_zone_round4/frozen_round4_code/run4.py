"""Round 4 scoring: 6 defs x 2 instruments x {H2, H15, T2}. Usage: run4.py [nseeds]"""
import sys, json, time, os
import numpy as np
from common import load, OOS_FIRST_DAY
from engine import Market
from zones4 import detect, life_end, DEFS
from engine4 import run_zones, random_zones, TimeGroups, take, NOLIFE, DEAD_PRE, NOARM, NOTOUCH, BROKEN, NOHOLD, HOLD

NSEEDS = int(sys.argv[1]) if len(sys.argv) > 1 else 500
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "results")
OOS0 = OOS_FIRST_DAY.toordinal()
MEAS = ("H2", "H15", "T2")


def stats(pnl):
    pnl = np.asarray(pnl, float)
    if len(pnl) == 0:
        return {"trades": 0, "wins": 0, "winrate": None, "pf": None, "exp": None, "net": 0.0, "maxdd": 0.0, "net_ex_top3": 0.0}
    gw = pnl[pnl > 0].sum(); gl = -pnl[pnl < 0].sum()
    eq = np.cumsum(pnl); peak = np.maximum.accumulate(np.r_[0.0, eq])[1:]
    srt = np.sort(pnl)
    return {"trades": int(len(pnl)), "wins": int((pnl > 0).sum()), "winrate": float((pnl > 0).mean()),
            "pf": float(gw / gl) if gl > 0 else float("inf"), "exp": float(pnl.mean()), "net": float(pnl.sum()),
            "maxdd": float((peak - eq).max()),
            "net_ex_top3": float(srt[:-3].sum()) if len(srt) > 3 else float(min(0.0, srt.sum()))}


def pf_of(p):
    p = p[np.isfinite(p)]
    gl = -p[p < 0].sum()
    return (p[p > 0].sum() / gl) if gl > 0 else (np.inf if len(p) else np.nan)


def pctile(real, null):
    null = np.asarray(null, float)
    if real is None or not np.isfinite(real) and not real == np.inf:
        return None
    null = null[~np.isnan(null)]
    if not len(null):
        return None
    return float(100.0 * ((null < real).sum() + 0.5 * (null == real).sum()) / len(null))


def verdict(o):
    if o["trades"] < 30:
        return "INSUFFICIENT"
    ok = (o["pf"] >= 1.3 and o["half1_net"] > 0 and o["half2_net"] > 0 and o["net_ex_top3"] > 0
          and o["pctile_pf"] is not None and o["pctile_pf"] >= 95)
    return "PASS" if ok else "FAIL"


def main():
    res = {}
    t0 = time.time()
    for inst in ("MNQ", "MES"):
        b = load(inst); mk = Market(b); life = life_end(b); tg = TimeGroups(b)
        oos_days = b.days[b.days >= OOS0]; is_days = b.days[b.days < OOS0]
        half = {"OOS": oos_days[len(oos_days) // 2], "IS": is_days[len(is_days) // 2]}
        nb = len(b.t)
        for name in DEFS:
            Z, nw = detect(b, name, life)
            tc = b.t[Z["i"]] + 300
            r = run_zones(mk, Z)
            tk = {m: take(r[m], tc) for m in MEAS}
            zper = np.where(b.tday[Z["i"]] >= OOS0, "OOS", "IS")
            st = r["status"]
            out = {"zones": int(len(Z["i"])), "dropped_width": nw,
                   "median_width_pts": float(np.median(Z["top"] - Z["bot"])) if len(Z["i"]) else None}
            for p in ("ALL", "IS", "OOS"):
                m = np.ones(len(st), bool) if p == "ALL" else zper == p
                out["funnel_" + p] = {"zones": int(m.sum()), "no_life": int((m & (st == NOLIFE)).sum()),
                    "dead_before_leaving": int((m & (st == DEAD_PRE)).sum()), "never_left": int((m & (st == NOARM)).sum()),
                    "armed": int((m & (st >= NOTOUCH)).sum()), "no_retest": int((m & (st == NOTOUCH)).sum()),
                    "retested": int((m & (st >= BROKEN)).sum()), "broken": int((m & (st == BROKEN)).sum()),
                    "no_hold": int((m & (st == NOHOLD)).sum()), "hold": int((m & (st == HOLD)).sum()),
                    "hold_entry_in_window": int((m & r["ent_ok"]).sum()), "skipped_R": int((m & r["skipR"]).sum()),
                    "H2_trades_1pos": int((m & tk["H2"]).sum()), "T2_fills": int((m & r["T2"]["fill"]).sum()),
                    "T2_trades_1pos": int((m & tk["T2"]).sum())}
            # null
            null = {mm: {p: {"pf": [], "net": []} for p in ("IS", "OOS")} for mm in MEAS}
            null_funnel = {"hold": [], "H2_trades": []}
            for seed in range(NSEEDS):
                rng = np.random.default_rng(seed)
                RZ = random_zones(b, Z, tg, life, rng)
                rr = run_zones(mk, RZ); rtc = b.t[RZ["i"]] + 300
                null_funnel["hold"].append(int((rr["status"] == HOLD).sum()))
                for mm in MEAS:
                    t = take(rr[mm], rtc)
                    d = b.tday[np.maximum(rr[mm]["bar"], 0)]
                    if mm == "H2": null_funnel["H2_trades"].append(int(t.sum()))
                    for p in ("IS", "OOS"):
                        pm = t & ((d >= OOS0) if p == "OOS" else (d < OOS0))
                        pp = rr[mm]["pnl"][pm]
                        null[mm][p]["pf"].append(pf_of(pp)); null[mm][p]["net"].append(float(np.nansum(pp)))
            out["null_mean_holds"] = float(np.mean(null_funnel["hold"])); out["null_mean_H2_trades"] = float(np.mean(null_funnel["H2_trades"]))
            for mm in MEAS:
                bar = r[mm]["bar"]; d = b.tday[np.maximum(bar, 0)]
                for p in ("IS", "OOS"):
                    pm = tk[mm] & ((d >= OOS0) if p == "OOS" else (d < OOS0))
                    order = np.argsort(bar[pm], kind="stable")
                    pnl = r[mm]["pnl"][pm][order]; dd = d[pm][order]
                    S = stats(pnl)
                    S["half1_net"] = float(pnl[dd < half[p]].sum()); S["half2_net"] = float(pnl[dd >= half[p]].sum())
                    S["half1_n"] = int((dd < half[p]).sum()); S["half2_n"] = int((dd >= half[p]).sum())
                    npf = np.array(null[mm][p]["pf"], float); nn = np.array(null[mm][p]["net"], float)
                    fin = npf[~np.isnan(npf)]
                    S["null_pf_median"] = float(np.median(fin)) if len(fin) else None
                    S["null_pf_p95"] = float(np.percentile(fin, 95)) if len(fin) else None
                    S["pctile_pf"] = pctile(S["pf"] if S["pf"] is not None else np.nan, npf)
                    S["null_net_median"] = float(np.median(nn)); S["pctile_net"] = pctile(S["net"], nn)
                    S["stopped"] = int(r[mm]["stopped"][pm].sum()); S["target"] = int(r[mm]["target"][pm].sum())
                    Rpts = r["R"] if mm != "T2" else (Z["top"] - Z["bot"] + 0.25)
                    S["median_R_pts"] = float(np.median(Rpts[pm])) if pm.any() else None
                    S["verdict"] = verdict(S) if p == "OOS" else None
                    out[f"{mm}_{p}"] = S
            res[f"{inst}:{name}"] = out
            o = {mm: out[f"{mm}_OOS"] for mm in MEAS}
            print(f"{inst} {name} zones={out['zones']} hold={out['funnel_ALL']['hold']} | " + " | ".join(
                f"{mm} n={o[mm]['trades']} pf={o[mm]['pf'] if o[mm]['pf'] is None else round(o[mm]['pf'],2)} net={o[mm]['net']:.0f} pct={o[mm]['pctile_pf']} {o[mm]['verdict']}" for mm in MEAS)
                  + f"  ({time.time()-t0:.0f}s)", flush=True)
            np.savez(os.path.join(OUT, f"ledger_{inst}_{name}.npz"), i=Z["i"], kind=Z["kind"], top=Z["top"], bot=Z["bot"],
                     status=st, touch=r["touch"], entry_bar=r["entry_bar"], R=r["R"],
                     **{f"{mm}_pnl": r[mm]["pnl"] for mm in MEAS}, **{f"{mm}_taken": tk[mm] for mm in MEAS},
                     **{f"{mm}_bar": r[mm]["bar"] for mm in MEAS}, t=b.t[Z["i"]])
    json.dump(res, open(os.path.join(OUT, f"results_{NSEEDS}seeds.json"), "w"), indent=1, default=float)


if __name__ == "__main__":
    main()
