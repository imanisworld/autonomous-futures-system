"""Bisect helper for the MES strat_122 max_drawdown root cause (2026-09-08).

Runs the isolated strat_212/strat_122 pass (max_drawdown gate OFF) using the
proof module found under a given worktree, matches resolved trades to the #337
canonical MES rows, and prints one JSON line: extras vs canonical, min balance
and the first date the carried balance falls below $1,200 (the replay gate's
effective floor).

Usage: python3 scripts/mes_122_drawdown_bisect_runner.py <worktree> <out_dir> <label>
Evidence tooling only. Needs AFS_122_CORPUS and the proof module + source
snapshot copied into <worktree>/scripts/.
"""
import sys,json,dataclasses,glob,os
from collections import defaultdict
from pathlib import Path
wt=Path(sys.argv[1]); out=Path(sys.argv[2]); label=sys.argv[3]
sys.path.insert(0,str(wt)); sys.path.insert(0,str(wt/"scripts"))
import mes_122_fallback_full_engine_proof as P
base=P._frozen_373_config()
iso=dataclasses.replace(base, enabled_concepts=["strat_212","strat_122"], disabled_concepts_per_instrument={}, max_drawdown_percent=0.0)
import shutil; shutil.rmtree(out, ignore_errors=True)
run=P._run(iso, out, treatment=False)
can=[json.loads(l) for l in open(wt/"scripts/strat_212_122_canonical_evidence_raw_trades.jsonl") if l.strip()]
can=[r for r in can if r["instrument"]=="MES"]
dec={}; cur=[]
for p in sorted(out.glob("journal_*.jsonl")):
    for l in open(p):
        if not l.strip(): continue
        e=json.loads(l)
        if e.get("decision")=="TRADE" and e.get("paper_order_id"): dec[e["paper_order_id"]]=e
        if e.get("type")=="OUTCOME":
            o=e["outcome"]; d=dec.get(o.get("paper_order_id"))
            if d: cur.append({"date":d["bar_ts"][:10],"ts":d["bar_ts"],"strategy":d["setup"]["strategy"],"direction":d["setup"]["direction"],"pnl":float(o.get("pnl_dollars") or 0)})
pool=defaultdict(list)
for r in cur: pool[(r["date"],r["strategy"],r["direction"])].append(r)
m122=0; same=0
for k in can:
    key=(k["date"],k["strategy"],k["direction"])
    if pool[key]:
        c=pool[key].pop(0)
        if k["strategy"]=="strat_122": m122+=1
        if abs(c["pnl"]-float(k["pnl"]))<1e-6: same+=1
extras=[r for v in pool.values() for r in v]
bal=1500; mn=1e9; hit=None
for r in sorted(cur,key=lambda r:r["ts"]):
    bal+=r["pnl"]; mn=min(mn,bal)
    if bal<1200 and hit is None: hit=r["date"]
print(json.dumps({"label":label,"trades":len(cur),"net":round(sum(r["pnl"] for r in cur),2),"strat_122_found":f"{m122}/33","identical_pnl":f"{same}/{len(can)}","extras":len(extras),"extras_net":round(sum(r["pnl"] for r in extras),2),"extras_122":sum(1 for r in extras if r["strategy"]=="strat_122"),"min_bal":round(mn,2),"first_below_1200":hit}))
