#!/usr/bin/env python3
"""Read-only controlled target-geometry study for the frozen V1-EPOCH-2 shadow cohort.

One-variable test: target only. Entry, underlying stop, premium stop, selected
contract, ASK entry, BID exits, observation cadence, and same-session horizon
are held fixed. Consumed-at-entry rows are excluded before comparison.
"""
from __future__ import annotations
import argparse, hashlib, json, sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path
from alert_ranker.paper_v1 import entry_geometry_state

START='2026-09-16T16:47:46'
END='2026-09-18'
VARIANTS=('RECORDED_TARGET_1','FIXED_1_5R','FIXED_2R')

def f(v):
    try:return float(v) if v is not None else None
    except:return None

def canon(x): return json.dumps(x,sort_keys=True,separators=(',',':'),allow_nan=False)
def sha(x): return hashlib.sha256(canon(x).encode()).hexdigest()
def dt(s): return datetime.fromisoformat(s.replace('Z','+00:00'))

def geometry(direction, inp, con):
    stored=con.get('paper_entry_geometry') or inp.get('paper_entry_geometry')
    if stored:return str(stored)
    price=f(inp.get('price'))
    stop=f(con.get('stop') if con.get('stop') is not None else inp.get('underlying_invalidation') or inp.get('stop'))
    target=f(con.get('target') if con.get('target') is not None else inp.get('target_1') or inp.get('target'))
    return entry_geometry_state(direction,price,stop,target)

def target_for(name,direction,entry,stop,source):
    risk=abs(entry-stop)
    if name=='RECORDED_TARGET_1': return source
    mult=1.5 if name=='FIXED_1_5R' else 2.0
    return entry+mult*risk if direction=='LONG' else entry-mult*risk

def hit(direction,price,level,kind):
    if direction=='LONG': return price>=level if kind=='target' else price<=level
    return price<=level if kind=='target' else price>=level

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--db',required=True)
    ap.add_argument('--output',required=True)
    a=ap.parse_args()
    c=sqlite3.connect(f'file:{Path(a.db).resolve()}?mode=ro',uri=True)
    raw=c.execute("""select id,timestamp,ticker,direction,status,setup_inputs_json,
      selected_contract_json,outcome_json from options_shadow_journal
      where timestamp>=? and timestamp<? order by timestamp,id""",(START,END)).fetchall()
    cohort=[]; consumed=Counter()
    for r in raw:
        inp=json.loads(r[5]); con=json.loads(r[6])
        g=geometry(r[3],inp,con); consumed[g]+=1
        if g!='AHEAD': continue
        cohort.append((r,inp,con))
    if len(raw)!=117 or len(cohort)!=47 or consumed['TARGET_CONSUMED_AT_ENTRY']!=58 or consumed['STOP_CONSUMED_AT_ENTRY']!=12:
        raise SystemExit(f'frozen cohort mismatch rows={len(raw)} ahead={len(cohort)} geometry={dict(consumed)}')

    # Cache all scans by ticker. Multiple candidate rows can share a scan timestamp;
    # collapse identical ticker/timestamp prices and fail closed on disagreement.
    scan_cache={}
    for ticker in sorted({r[0][2] for r in cohort}):
        vals=c.execute("select timestamp,raw_json from scans where ticker=? and timestamp>=? and timestamp<? order by timestamp,id",
                       (ticker,START,'2026-09-19')).fetchall()
        by={}
        for ts,rawj in vals:
            price=f(json.loads(rawj).get('price'))
            if price is None: continue
            key=dt(ts)
            if key in by and abs(by[key]-price)>1e-9: raise SystemExit(f'conflicting scan price {ticker} {ts}')
            by[key]=price
        scan_cache[ticker]=sorted(by.items())

    frozen_inputs=[]; rows_out=[]
    for (r,inp,con) in cohort:
        sid,entry_ts,ticker,direction=r[:4]
        entry=f(inp.get('price')); stop=f(con.get('stop') if con.get('stop') is not None else inp.get('underlying_invalidation') or inp.get('stop'))
        source=f(con.get('target') if con.get('target') is not None else inp.get('target_1') or inp.get('target'))
        ask=f(con.get('entry_ask') or inp.get('option_ask')); pstop=f(con.get('premium_stop') or inp.get('premium_stop'))
        qty=int(con.get('contracts') or inp.get('contracts') or 1)
        session_close=dt(inp['session_close']); ets=dt(entry_ts)
        marks=c.execute("select timestamp,bid,ask,error from options_contract_marks where shadow_id=? order by timestamp,id",(sid,)).fetchall()
        obs=[]
        scans=scan_cache[ticker]
        for mts,bid,askm,err in marks:
            mt=dt(mts)
            if mt<ets or mt>session_close or err or bid is None: continue
            nearest=min(scans,key=lambda z:abs((z[0]-mt).total_seconds()),default=None)
            if not nearest or abs((nearest[0]-mt).total_seconds())>2: continue
            obs.append({'timestamp':mts,'underlying':nearest[1],'bid':f(bid)})
        if not obs: continue
        frozen_inputs.append({'shadow_id':sid,'entry_ts':entry_ts,'ticker':ticker,'direction':direction,'entry':entry,'stop':stop,'source_target':source,'entry_ask':ask,'premium_stop':pstop,'qty':qty,'session_close':inp['session_close'],'observations':obs})
        per={}
        for name in VARIANTS:
            target=target_for(name,direction,entry,stop,source)
            resolution='CENSORED_AT_HORIZON'; exit_bid=obs[-1]['bid']; exit_ts=obs[-1]['timestamp']
            for o in obs[1:]:
                stop_hit=hit(direction,o['underlying'],stop,'stop') or (pstop is not None and o['bid']<=pstop)
                target_hit=hit(direction,o['underlying'],target,'target')
                if stop_hit and target_hit:
                    resolution='STOP_AMBIGUOUS'; exit_bid=o['bid']; exit_ts=o['timestamp']; break
                if stop_hit:
                    resolution='STOP'; exit_bid=o['bid']; exit_ts=o['timestamp']; break
                if target_hit:
                    resolution='TARGET'; exit_bid=o['bid']; exit_ts=o['timestamp']; break
            pnl=(exit_bid-ask)*100*qty
            per[name]={'target':round(target,8),'resolution':resolution,'exit_ts':exit_ts,'exit_bid':exit_bid,'pnl_dollars':round(pnl,2)}
        rows_out.append({'shadow_id':sid,'ticker':ticker,'direction':direction,'setup_type':inp.get('setup_type'),'timeframe':inp.get('setup_timeframe') or inp.get('timeframe'),'variants':per})

    if len(rows_out)!=47: raise SystemExit(f'missing observed paths: {len(rows_out)}/47')
    summaries={}
    for name in VARIANTS:
        rr=[x['variants'][name] for x in rows_out]; rc=Counter(x['resolution'] for x in rr)
        pnl=[x['pnl_dollars'] for x in rr]
        resolved=[x for x in rr if x['resolution']!='CENSORED_AT_HORIZON']
        summaries[name]={
          'n':len(rr),'resolution_counts':dict(sorted(rc.items())),
          'censored_n':rc['CENSORED_AT_HORIZON'],
          'forced_horizon_pnl_all_rows':round(sum(pnl),2),
          'forced_horizon_avg_pnl':round(sum(pnl)/len(pnl),2),
          'resolved_event_pnl_only':round(sum(x['pnl_dollars'] for x in resolved),2),
          'resolved_event_n':len(resolved),
        }
    result={
      'study':'OPTIONS_SHADOW_TARGET_GEOMETRY_CONTROLLED_V0_1',
      'verdict_scope':'research_only_no_v1_tuning',
      'frozen_cohort':{'start':START,'end_exclusive':END,'raw_rows':len(raw),'ahead_rows':len(cohort),'entry_geometry_counts':dict(sorted(consumed.items()))},
      'controls':{'entry':'unchanged first-sight underlying price','stop':'unchanged underlying invalidation plus unchanged premium stop','contract':'unchanged selected contract','costs':'unchanged ASK entry / BID exit, no added fee','horizon':'unchanged same-session session_close','changed_variable':'underlying target only','censoring':'reported explicitly; all-row comparison also forces unresolved rows out at last executable BID by the same fixed horizon'},
      'input_sha256':sha(frozen_inputs),
      'summaries':summaries,
      'rows':rows_out,
      'limitations':['observed 5-minute-ish scanner/contract snapshots do not reveal intra-snapshot path','same-session horizon is intentionally held fixed and is not a Daily/4H horizon test','population is the 47 clean AHEAD counterfactual shadow rows from V1-EPOCH-2 snapshot boundary, not 212R-specific','research result does not authorize V1 tuning or strategy promotion']
    }
    out=Path(a.output); out.parent.mkdir(parents=True,exist_ok=True); out.write_text(json.dumps(result,indent=2,sort_keys=True)+'\n')
    print(json.dumps({'input_sha256':result['input_sha256'],'cohort':result['frozen_cohort'],'summaries':summaries},indent=2))
if __name__=='__main__': main()
