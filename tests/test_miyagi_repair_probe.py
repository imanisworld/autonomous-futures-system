"""Temporary evidence-only Miyagi MNQ repair probe. Intentionally fails CI."""
from __future__ import annotations
import gzip, json
from pathlib import Path
import pytest

CANDIDATES = Path("scripts/edge_decomposition_audit_results_candidates.jsonl.gz")
TICK=0.25
CAPS=(300.0,400.0,500.0,600.0,700.0,800.0,900.0)

def rows():
    out=[]
    with gzip.open(CANDIDATES,"rt") as f:
        for line in f:
            r=json.loads(line)
            if r.get("lane") == "miyagi_mnq":
                st=abs(float(r["entry"])-float(r["stop"]))/TICK
                out.append({**r,"stop_ticks_recalc":st})
    return sorted(out,key=lambda r:(r.get("date",""),r.get("bar_ts","")))

def metrics(cap):
    rs=[r for r in rows() if r["stop_ticks_recalc"]<=cap]
    resolved=[r for r in rs if (r.get("bracket") or {}).get("status")=="RESOLVED"]
    nets=[float(r["bracket"]["net"]) for r in resolved]
    gp=sum(x for x in nets if x>0); gl=-sum(x for x in nets if x<0)
    mid=len(resolved)//2
    return {"cap":cap,"n":len(rs),"resolved":len(resolved),"net":round(sum(nets),2),"pf":round(gp/gl,6) if gl else None,"wins":sum(x>0 for x in nets),"losses":sum(x<0 for x in nets),"h1":round(sum(float(r["bracket"]["net"]) for r in resolved[:mid]),2),"h2":round(sum(float(r["bracket"]["net"]) for r in resolved[mid:]),2),"max_risk_1c":cap*0.50,"dates":[r.get("date") for r in rs]}

def test_emit_miyagi_repair_probe():
    rs=rows()
    details=[{"date":r.get("date"),"stop_ticks":r["stop_ticks_recalc"],"rr":r.get("rr"),"condition":(r.get("gates") or {}).get("market_condition"),"bracket_status":(r.get("bracket") or {}).get("status"),"bracket_net":(r.get("bracket") or {}).get("net"),"keys":sorted(r.keys())} for r in rs]
    pytest.fail("MIYAGI_PROBE="+json.dumps({"caps":{str(int(c)):metrics(c) for c in CAPS},"details":details},sort_keys=True))
