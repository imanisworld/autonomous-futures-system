"""Temporary evidence-only Miyagi MNQ repair probe. Intentionally fails CI."""
from __future__ import annotations
import gzip, json
from pathlib import Path
import pytest

CANDIDATES = Path("scripts/edge_decomposition_audit_results_candidates.jsonl.gz")
TICK=0.25
CAPS=(300.0,400.0,500.0,600.0,700.0,800.0,900.0)
HORIZONS=("30m","60m","120m","EOD")

def rows():
    out=[]
    with gzip.open(CANDIDATES,"rt") as f:
        for line in f:
            r=json.loads(line)
            if r.get("lane") == "miyagi_mnq":
                st=abs(float(r["entry"])-float(r["stop"]))/TICK
                out.append({**r,"stop_ticks_recalc":st})
    return sorted(out,key=lambda r:(r.get("date",""),r.get("bar_ts","")))

def _summarize(vals):
    gp=sum(v for v in vals if v>0); gl=-sum(v for v in vals if v<0)
    mid=len(vals)//2
    return {"n":len(vals),"net":round(sum(vals),2),"wins":sum(v>0 for v in vals),"losses":sum(v<0 for v in vals),"pf":round(gp/gl,6) if gl else None,"h1":round(sum(vals[:mid]),2),"h2":round(sum(vals[mid:]),2),"worst":round(min(vals),2) if vals else None}

def metrics(cap):
    rs=[r for r in rows() if r["stop_ticks_recalc"]<=cap]
    resolved=[r for r in rs if (r.get("bracket") or {}).get("status")=="RESOLVED"]
    vals=[float(r["bracket"]["net"]) for r in resolved]
    return {"cap":cap,"max_risk_1c":cap*0.50,**_summarize(vals),"dates":[r.get("date") for r in rs]}

def control_summary(rs):
    out={}
    for h in HORIZONS:
        vals=[float(r["control"][h]["net"]) for r in rs if (r.get("control") or {}).get(h) and r["control"][h].get("net") is not None]
        out[h]=_summarize(vals)
    return out

def hybrid_summary(rs):
    """Documented stop/target stays active; time exit applies only if earlier."""
    out={}
    for h in HORIZONS:
        vals=[]; sources=[]
        for r in rs:
            item=(r.get("control") or {}).get(h)
            if not item or item.get("net") is None:
                continue
            b=r.get("bracket") or {}
            bts=b.get("exit_bar_ts")
            hts=item.get("exit_bar_ts")
            if b.get("status")=="RESOLVED" and bts and hts and bts <= hts:
                vals.append(float(b["net"])); sources.append("bracket")
            else:
                vals.append(float(item["net"])); sources.append("time")
        out[h]={**_summarize(vals),"bracket_first":sources.count("bracket"),"time_first":sources.count("time")}
    return out

def test_emit_miyagi_repair_probe():
    rs=rows(); cap600=[r for r in rs if r["stop_ticks_recalc"]<=600]
    details=[{"date":r.get("date"),"stop_ticks":r["stop_ticks_recalc"],"rr":r.get("rr"),"condition":(r.get("gates") or {}).get("market_condition"),"bracket":r.get("bracket"),"control":r.get("control")} for r in rs]
    pytest.fail("MIYAGI_PROBE="+json.dumps({"caps":{str(int(c)):metrics(c) for c in CAPS},"control_all":control_summary(rs),"control_cap600":control_summary(cap600),"hybrid_all":hybrid_summary(rs),"hybrid_cap600":hybrid_summary(cap600),"details":details},sort_keys=True))
