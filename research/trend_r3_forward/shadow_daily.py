# AFS shadow tracker daily driver. LOG ONLY: no orders, no VPS, no repo. Run from this folder after fetch_incremental.py.
import hashlib,json,os,sys,datetime,zoneinfo,pandas as pd,numpy as np
BASE='/workspace/afs-shared/discovery_2026-10-09'
HASH={f'{BASE}/candidates/trend_daily_v1/FROZEN_RULES_R3.md':'5b00285c5d973c183a453092a3137b4a53229a20d2783d49ed5c1fb6b53332af',
      f'{BASE}/events/FROZEN_EVENTS.md':'a97f02407a6c2df2135d4170fb96c3d4ab94d101c0196a07479424616aee1ae4',
      f'{BASE}/nonprice/FROZEN_NONPRICE.md':'0829a8dfee63f3d7bdd2da33f5b460a3a7955cdaf7e0fa354800a9b6adfad2c0'}
bad=[p for p,h in HASH.items() if hashlib.sha256(open(p,'rb').read()).hexdigest()!=h]
if bad: print('HASH_MISMATCH',bad); sys.exit(2)
ET=zoneinfo.ZoneInfo('America/New_York'); NOW=datetime.datetime.now(ET); TS=NOW.isoformat(timespec='seconds')
import trend_shadow as TSH
SD=TSH.SHADOW_START; RUN_DATE=TSH.RUN_DATE; SPEC=TSH.SPEC; COM=1.48
LOG='shadow_log.jsonl'
seen=set()
if os.path.exists(LOG):
    for l in open(LOG): 
        try: seen.add(json.loads(l)['key'])
        except Exception: pass
new=[]; notify=[]; problems=[]
def add(key,**kw):
    if key in seen: return False
    rec=dict(ts_et=TS,key=key,**kw); new.append(rec); seen.add(key); return True
def sess(d): return f"session opening {pd.Timestamp(d).date()} 18:00 ET"
# ---------- 1) trend_daily_v1 R3 (baseline only; failed long-history test AFS-0218) ----------
SS={}; trend={}
for r in SPEC:
    S=TSH.cont(r); SS[r]=S; T,E,st=TSH.run(S,r); trend[r]=(T,E,st)
lastbars={r:SS[r].index[-1] for r in SPEC}
exp_last=max(lastbars.values())
for r,d in lastbars.items():
    if d<exp_last: problems.append(f"{r} daily bar missing: last {d.date()} vs {exp_last.date()}")
if (RUN_DATE-exp_last).days>4: problems.append(f"futures daily bars stale: newest {exp_last.date()}")
R='trend_daily_v1_R3'
for r,(T,E,st) in trend.items():
    for e in E:
        if e['date']<SD: continue
        d=str(pd.Timestamp(e['date']).date())
        if e['kind']=='entry':
            if add(f"{R}|entry|{r}|{d}",rule=R,type='entry',instrument=r,session_label=d,session=sess(d),side=e['side'],
                   inherited=e['inherited'],fill_raw=e['entry_raw'],contract=e['entry_tk']):
                notify.append(f"Trend baseline opened {'long' if e['side']>0 else 'short'} {r} at {e['entry_raw']} ({e['entry_tk']})"+(" (took over the rule's existing position)" if e['inherited'] else ""))
        elif e['kind']=='exit':
            if e['inherited'] is False and pd.Timestamp(e['entry'])<SD: continue
            if add(f"{R}|exit|{r}|{d}",rule=R,type='exit',instrument=r,session_label=d,session=sess(d),side=e['side'],why=e['why'],
                   entry_label=e['entry'],fill_raw=e['exit_raw'],contract=e['exit_tk'],net_after_costs=e['net'],rolls=e['rolls']):
                notify.append(f"Trend baseline closed {r} ({e['why']}): {'+' if e['net']>=0 else '-'}${abs(e['net']):,.2f} after costs")
        elif e['kind']=='roll':
            add(f"{R}|roll|{r}|{d}",rule=R,type='roll',instrument=r,session_label=d,to_contract=e['to_tk'])
    ld=st['last_bar']
    add(f"{R}|signal|{r}|{ld}",rule=R,type='signal',instrument=r,bar_label=ld,bar=f"session opened {ld} 18:00 ET (completed)",contract=st['tk'],close_raw=st['close_raw'],
        signal=st['signal_today'],rule_position=st['pos'],stopped_flat_until_flip=st['stopped_sig'] or None,pending_next_open=st['pending_next_open'],
        stop_next_session_raw=st.get('stop_next_raw'))
    if st['pos'] and pd.Timestamp(st['entry'])>=SD:
        add(f"{R}|mark|{r}|{ld}",rule=R,type='mark',instrument=r,bar_label=ld,side=st['pos'],entry_raw=st['entry_raw'],close_raw=st['close_raw'],
            mark_net_after_costs=st['mark_net'],stop_next_session_raw=st['stop_next_raw'])
# ---------- 2) E4 FOMC first-15m follow ----------
FOMC_2026_27=['2026-10-28','2026-12-09','2027-01-27','2027-03-17']  # decision days, federalreserve.gov fomccalendars (checked 2026-10-12)
today=str(NOW.date())
import e4_fomc
e4_fomc.run_day(today,FOMC_2026_27,add,notify,problems,now=NOW.replace(tzinfo=None))
add(f"E4_fomc_follow|status|{today}",rule='E4_fomc_follow',type='status',date=today,fomc_today=today in FOMC_2026_27,next_fomc=next(d for d in FOMC_2026_27 if d>=today))
# ---------- 3) H3 VIX > VIX3M inversion long MES/MNQ ----------
V=pd.read_csv('data/vix_recent.csv',index_col=0,parse_dates=True).dropna()
if V.index[-1].date()<NOW.date() and NOW.weekday()<5: problems.append(f"Yahoo VIX/VIX3M close for {today} missing (last {V.index[-1].date()})")
def inv(d):
    s=V[V.index<d]; return bool(s['^VIX'].iloc[-1]>s['^VIX3M'].iloc[-1]) if len(s) else None
H='H3_vix_inversion'; hstate={}
for r in ['MES','MNQ']:
    S=SS[r]; pv,tk=SPEC[r]; sl=2*tk; b=S[S.index>=SD]; pos=0; ent=None; held=0; trades=[]
    def xclose(d,row,why):
        global_net=(row.o-sl-ent['px'])*pv-2*COM-ent['rolls']*(2*COM+2*sl*pv); return round(float(global_net),2)
    for d,row in b.iterrows():
        sig=inv(d); dl=str(d.date()); mh=False
        if pos:
            if row.roll: ent['rolls']+=1
            if sig is False or held>=20:
                net=xclose(d,row,''); why='normalized' if sig is False else 'max_hold_20'
                if add(f"{H}|exit|{r}|{dl}",rule=H,type='exit',instrument=r,session_label=dl,session=sess(dl),why=why,entry_label=ent['d'],
                       fill_raw=round(float(row.ro-sl),4),contract=row.tk,net_after_costs=net):
                    notify.append(f"VIX inversion rule closed long {r} ({why}): {'+' if net>=0 else '-'}${abs(net):,.2f} after costs")
                trades.append(net); pos=0; mh=(why=='max_hold_20')
        if not pos and sig:
            pos=1; held=0; ent=dict(d=dl,px=row.o+sl,raw=round(float(row.ro+sl),4),rolls=0)
            if add(f"{H}|entry|{r}|{dl}",rule=H,type='entry',instrument=r,session_label=dl,session=sess(dl),side=1,fill_raw=ent['raw'],contract=row.tk,
                   reentry_after_max_hold=mh):
                notify.append(f"VIX inversion rule opened long {r} at {ent['raw']} ({row.tk})")
        if pos: held+=1
    lv=V.index[-1]; hstate[r]=dict(pos=pos,closed=trades)
    if pos:
        last=S.iloc[-1]; m=round(float((last.c-sl-ent['px'])*pv-2*COM-ent['rolls']*(2*COM+2*sl*pv)),2); hstate[r]['mark']=m
        add(f"{H}|mark|{r}|{S.index[-1].date()}",rule=H,type='mark',instrument=r,bar_label=str(S.index[-1].date()),entry_label=ent['d'],mark_net_after_costs=m,held_sessions=held)
add(f"{H}|signal|{V.index[-1].date()}",rule=H,type='signal',vix_close_date=str(V.index[-1].date()),vix=round(float(V['^VIX'].iloc[-1]),2),
    vix3m=round(float(V['^VIX3M'].iloc[-1]),2),inverted=bool(V['^VIX'].iloc[-1]>V['^VIX3M'].iloc[-1]),
    note="research convention: a futures session labeled d uses the last VIX close dated before d")
for p in problems: add(f"problem|{today}|{p}",type='problem',detail=p)
with open(LOG,'a') as f:
    for rec in new: f.write(json.dumps(rec,default=str)+'\n')
# ---------- SUMMARY ----------
L=[json.loads(l) for l in open(LOG)]
lines=[f"# AFS shadow tracker summary (forward evidence only, nothing here is proven)\n",f"Updated {TS} ET. Shadow start: futures session opening {SD.date()} 18:00 ET. Costs: $1.48/side + 2 ticks/side (trend, VIX); E4 uses 3 ticks/side.\n",
       "Polygon daily bars are labeled by session open date (a bar labeled Oct 11 is the Sunday-evening-to-Monday-17:00 session).\n",
       "\n| Rule | Instrument | Closed trades | Net closed (after costs) | Open position | Open mark (after costs) |\n|---|---|---|---|---|---|\n"]
tot=0
for r,(T,E,st) in trend.items():
    sh=[t for t in T if pd.Timestamp(t['entry'])>=SD or t['inherited']]; n=sum(t['net'] for t in sh); tot+=n
    op=f"{'long' if st['pos']>0 else 'short'} since {st['entry']}" if st['pos'] and pd.Timestamp(st['entry'])>=SD else (f"pending: {st['pending_next_open']}" if st['pending_next_open'] else 'flat')
    mk=f"${st['mark_net']:,.2f}" if st['pos'] and pd.Timestamp(st['entry'])>=SD else '-'
    lines.append(f"| trend_daily_v1 R3 (baseline; failed AFS-0218) | {r} | {len(sh)} | ${n:,.2f} | {op} | {mk} |\n")
for r,h in hstate.items():
    lines.append(f"| H3 VIX>VIX3M | {r} | {len(h['closed'])} | ${sum(h['closed']):,.2f} | {'long' if h['pos'] else 'flat'} | {('$%.2f'%h['mark']) if h['pos'] else '-'} |\n")
lines.append(f"| E4 FOMC follow | MNQ/MES/M2K | {sum(1 for x in L if x.get('rule')=='E4_fomc_follow' and x.get('type')=='exit')} | ${sum(x.get('net_after_costs',0) for x in L if x.get('rule')=='E4_fomc_follow' and x.get('type')=='exit'):,.2f} | flat | - |\n")
lines.append(f"\nNext FOMC decision day: {next(d for d in FOMC_2026_27 if d>=today)}.  E4 is evaluated by e4_fomc.py on that day's run (after 16:05 ET; 5m Polygon, Yahoo fallback).\n")
lines.append("\nInterpretations: positions the trend rule already held at shadow start are taken over at the shadow-start open at a fresh price, keeping the rule's own trailing-stop anchor. H3 exits at the open after the first VIX<VIX3M close; at 20 held sessions it exits at the next open and re-enters at that open if still inverted (AFS-0220). Research date convention kept (one-session-later use of VIX close).\n")
open('SUMMARY.md','w').writelines(lines)
print('NEW_RECORDS',len(new)); print('NOTIFY',json.dumps(notify)); print('PROBLEMS',json.dumps(problems))
