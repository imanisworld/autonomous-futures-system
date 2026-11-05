# Shadow tracker for FROZEN_RULES_R3 (trend_daily_v1, sha256 5b00285c...). Logic copied from ../test_r3.py cont()/run()
# unchanged, except: (1) no window end, (2) positions open at the shadow start are taken over at the shadow start open
# (fresh shadow entry price, rule's own trailing-stop anchor kept), (3) only events on/after SHADOW_START are reported.
import pandas as pd,numpy as np,json,sys
SPEC={'MNQ':(2,.25),'MES':(5,.25),'M2K':(5,.1),'MGC':(10,.1),'MCL':(100,.01)}
MC='FGHJKMNQUVXZ'; COM=1.48
SHADOW_START=pd.Timestamp('2026-10-12')  # Polygon daily bar label = session open date (6 PM ET); bar 2026-10-12 = session opening Mon Oct 12 18:00 ET
def order(tk): return (int(tk[-1]),MC.index(tk[-2]))
old=pd.read_csv('../daily_r3.csv'); new=pd.read_csv('data/daily_shadow.csv')
for X in (old,new): X['date']=pd.to_datetime(X.ts,unit='ns').dt.normalize()
old=old[old.date<'2025-11-01']; new=new[new.date>='2025-11-01']
D=pd.concat([old,new]).drop_duplicates(['tk','date'],keep='last')
import datetime,zoneinfo
RUN_DATE=pd.Timestamp(datetime.datetime.now(zoneinfo.ZoneInfo('America/New_York')).date())
D=D[D.date<RUN_DATE]  # drop the session that opens tonight (incomplete)
def cont(r):
    g=D[D.root==r]; P=g.pivot_table(index='date',columns='tk',values=['o','h','l','c','v'])
    dates=P.index; cur=None; rows=[]
    for i,dt in enumerate(dates):
        if i==0: continue
        pv=P['v'].iloc[i-1].dropna(); avail=P['c'].iloc[i].dropna().index
        cand=[t for t in pv.sort_values(ascending=False).index if t in avail]
        if not cand: continue
        best=cand[0]
        if cur is None or (order(best)>order(cur)): new_=best
        else: new_=cur if cur in avail else best
        rolled = cur is not None and new_!=cur
        diff = (P['c'][new_].iloc[i-1]-P['c'][cur].iloc[i-1]) if rolled else 0.0
        if rolled and np.isnan(diff): diff=P['o'][new_].iloc[i]-P['c'][cur].iloc[i-1]
        cur=new_
        rows.append(dict(date=dt,tk=cur,o=P['o'][cur].iloc[i],h=P['h'][cur].iloc[i],l=P['l'][cur].iloc[i],c=P['c'][cur].iloc[i],roll=rolled,diff=diff,
                         ro=P['o'][cur].iloc[i],rc=P['c'][cur].iloc[i]))
    S=pd.DataFrame(rows).set_index('date')
    adj=S['diff'][::-1].cumsum()[::-1].shift(-1).fillna(0)
    for k in 'ohlc': S[k]=S[k]+adj
    S['adj']=adj
    return S
def run(S,r):
    pv,tk=SPEC[r]; sl=2*tk
    tr=np.maximum(S.h-S.l,np.maximum(abs(S.h-S.c.shift()),abs(S.l-S.c.shift())))
    atr=tr.rolling(20).mean().shift(1); sig=np.sign(S.c-S.c.shift(100))
    idx=list(S.index); pos=0; ent=None; trades=[]; ev=[]; stopped_sig=0; ext=None
    def close(i,px,why):
        nonlocal pos
        pnl=(pos*(px-pos*sl-ent['px'])*pv-COM*2) - ent['rolls']*(COM*2+2*sl*pv)
        t=dict(root=r,entry=str(ent['d'].date()),exit=str(idx[i].date()),side=pos,net=round(pnl,2),why=why,inherited=ent.get('inh',False),
               entry_raw=ent['raw'],entry_tk=ent['tk'],exit_raw=round(px-S.adj.iloc[i],4),exit_tk=S.tk.iloc[i],rolls=ent['rolls'])
        trades.append(t); ev.append(dict(kind='exit',date=idx[i],**t)); pos=0
    for i in range(1,len(idx)):
        d=idx[i]; row=S.iloc[i]
        s=sig.iloc[i-1]; a=atr.iloc[i-1]
        if np.isnan(s) or np.isnan(a): continue
        if pos and d>=SHADOW_START and ent['d']<SHADOW_START and s!=-pos:   # take over open rule position at shadow start open
            ent=dict(d=d,i=i,px=row.o+pos*sl,risk=3*a*pv,rolls=0,inh=True,raw=round(row.ro+pos*sl,4),tk=row.tk)
            ev.append(dict(kind='entry',date=d,root=r,side=pos,inherited=True,entry_raw=ent['raw'],entry_tk=row.tk))
        if pos and s==-pos: close(i,row.o,'flip')
        if not pos and s!=0 and s!=stopped_sig:
            pos=int(s); ent=dict(d=d,i=i,px=row.o+pos*sl,risk=3*a*pv,rolls=0,raw=round(row.ro+pos*sl,4),tk=row.tk); ext=row.o; stopped_sig=0
            ev.append(dict(kind='entry',date=d,root=r,side=pos,inherited=False,entry_raw=ent['raw'],entry_tk=row.tk))
        if pos:
            if row.roll and i!=ent['i']: ent['rolls']+=1; ev.append(dict(kind='roll',date=d,root=r,to_tk=row.tk))
            st=ext-pos*3*a
            if (pos>0 and row.l<=st) or (pos<0 and row.h>=st):
                px=min(row.o,st) if pos>0 else max(row.o,st)
                stopped_sig=pos; close(i,px,'stop'); continue
            ext=max(ext,row.c) if pos>0 else min(ext,row.c)
        if s!=stopped_sig and stopped_sig and s!=0: stopped_sig=0 if s==-stopped_sig else stopped_sig
    L=len(idx)-1; last=S.iloc[L]; sl_=sl
    st=dict(root=r,last_bar=str(idx[L].date()),tk=last.tk,close_raw=float(last.rc),signal_today=int(sig.iloc[L]) if not np.isnan(sig.iloc[L]) else None,
            pos=pos,stopped_sig=int(stopped_sig))
    if pos:
        mark=(pos*(last.c-pos*sl_-ent['px'])*pv-COM*2) - ent['rolls']*(COM*2+2*sl_*pv)
        stop_next=ext-pos*3*atr.iloc[L]
        st.update(entry=str(ent['d'].date()),entry_raw=ent['raw'],entry_tk=ent['tk'],inherited=ent.get('inh',False),mark_net=round(float(mark),2),
                  stop_next_raw=round(float(stop_next-S.adj.iloc[L]),4),trail_anchor_raw=round(float(ext-S.adj.iloc[L]),4))
    s=st['signal_today']
    if pos and s==-pos: st['pending_next_open']='flip to %+d'%s
    elif pos and idx[L]<SHADOW_START: st['pending_next_open']='take over %+d at shadow start open'%pos
    elif not pos and s not in (0,None) and s!=stopped_sig: st['pending_next_open']='enter %+d'%s
    else: st['pending_next_open']=None
    return trades,ev,st
if __name__=='__main__':
    out={}
    for r in SPEC:
        S=cont(r); T,E,st=run(S,r)
        out[r]=dict(state=st,shadow_trades=[t for t in T if pd.Timestamp(t['entry'])>=SHADOW_START or t['inherited']],
                    shadow_events=[{**e,'date':str(e['date'].date())} for e in E if e['date']>=SHADOW_START])  # pre-start replay is used only to set rule state; no pre-start results kept (MNQ data seal)
    json.dump(out,open('data/trend_state_latest.json','w'),indent=1,default=str)
    print(json.dumps({r:v['state'] for r,v in out.items()},indent=1,default=str))
