"""P8 auxiliary auditor checks (read-only): H1/H3 overlap, dedupe sensitivity, T/F/NA vs base, K7 roll gap."""
import json, sys, importlib.util, numpy as np
from collections import Counter, defaultdict
spec = importlib.util.spec_from_file_location("p8", "<worktree>/scripts/structural_level_p8_outcome_analysis.py")
p8 = importlib.util.module_from_spec(spec); spec.loader.exec_module(p8)
R5="<checkout>/logs/structural_level_r5_2026_09_17/structural_level_r5"
R6="<checkout>/logs/structural_level_r6_2026_09_17/structural_level_r6"
out={}
for inst in ("MNQ","MES"):
    rows=p8.load_candidates_features(R5,R6,inst)
    p8.load_outcomes(R5,inst,rows)
    xs=list(rows.values())
    term=[x for x in xs if x["result"] in ("WIN","LOSS")]
    base=np.mean([x["net_R"] for x in term]); o={"base_mean_net_R_all_terminal":round(float(base),4)}
    # K7: roll ledger gap check comes from the R5 manifest (printed separately)
    # overlap H1 T vs H3 T
    h1t={x["key"] for x in term if x["hyp"]["H1"]["label"]=="T"}; h3t={x["key"] for x in term if x["hyp"]["H3"]["label"]=="T"}
    o["H1T_terminal"]=len(h1t); o["H3T_terminal"]=len(h3t); o["H1T_and_H3T"]=len(h1t&h3t)
    o["share_of_H1T_also_H3T"]=round(len(h1t&h3t)/len(h1t),4); o["share_of_H3T_also_H1T"]=round(len(h1t&h3t)/len(h3t),4)
    # T/F/NA means vs base (terminal rows, no gap/roll filter here — descriptive)
    for h in ("H1","H3","H5","H6"):
        d={}
        for lab in ("T","F","NOT_APPLICABLE"):
            v=[x["net_R"] for x in term if x["hyp"][h]["label"]==lab]
            d[lab]={"n":len(v),"mean_net_R":round(float(np.mean(v)),4) if v else None,"delta_vs_base":round(float(np.mean(v)-base),4) if v else None,
                    "win_rate":round(float(np.mean([x["result"]=="WIN" for x in term if x["hyp"][h]["label"]==lab])),4) if v else None}
        o[f"{h}_T_F_NA_vs_base"]=d
    # dedupe sensitivity: one row per (B0, direction), deterministic pick = lexicographically smallest strategy then key
    for h in ("H1","H3"):
        elig=[x for x in term if x["hyp"][h]["label"] in ("T","F") and not x["hyp"][h]["gap"]]
        # roll flag not computed here (needs precommit) -> apply gap-only; report both raw and dedup on the same basis
        byk={}
        for x in sorted(elig,key=lambda x:(x["strategy"],x["key"])):
            byk.setdefault((x["ts"],x["direction"]),x)
        dd=list(byk.values())
        def eff(v):
            t=[x["net_R"] for x in v if x["hyp"][h]["label"]=="T"]; f=[x["net_R"] for x in v if x["hyp"][h]["label"]=="F"]
            return {"n":len(v),"n_T":len(t),"n_F":len(f),"effect_R":round(float(np.mean(t)-np.mean(f)),4)}
        o[f"{h}_dedupe_sensitivity"]={"all_rows_gap_filtered":eff(elig),"one_per_bar_direction":eff(dd),
                                    "rows_per_bar":round(len(elig)/len({x['ts'] for x in elig}),3)}
    # concentration of H1/H3 T rows by session and by family (share)
    for h in ("H1","H3"):
        t=[x for x in term if x["hyp"][h]["label"]=="T"]
        o[f"{h}_T_share_by_session"]={k:round(v/len(t),3) for k,v in Counter(x["session"] for x in t).items()}
        o[f"{h}_T_share_by_family"]={k:round(v/len(t),3) for k,v in sorted(Counter(x["strategy"] for x in t).items(), key=lambda kv:-kv[1])}
    # direction split of H1/H3 effects
    for h in ("H1","H3"):
        d={}
        for dirn in ("LONG","SHORT"):
            v=[x for x in term if x["direction"]==dirn and x["hyp"][h]["label"] in ("T","F") and not x["hyp"][h]["gap"]]
            t=[x["net_R"] for x in v if x["hyp"][h]["label"]=="T"]; f=[x["net_R"] for x in v if x["hyp"][h]["label"]=="F"]
            d[dirn]={"n_T":len(t),"n_F":len(f),"effect_R":round(float(np.mean(t)-np.mean(f)),4)}
        o[f"{h}_by_direction"]=d
    out[inst]=o
    print(inst, json.dumps(o, indent=1))
json.dump(out, open(sys.argv[1],"w"), indent=1)
