# Test #432 (descriptive prediction for V-A). Pre-seal data only (<2026-06-27).
import pandas as pd,numpy as np
src=open('test_r3.py').read(); head=src.split("out={}")[0]; exec(head)
SPEC.pop('MBT'); SS={r:cont(r) for r in SPEC}
base_run=run
def confirm(S):
    s=np.sign(S.c-S.c.shift(100)); c=s.copy()
    for i in range(1,len(s)):
        c.iloc[i]= s.iloc[i] if s.iloc[i]==s.iloc[i-1] else c.iloc[i-1]
    return c
import types
code=src.split("def run(")[1].split("def bh(")[0]
code="def runA(S,r,lo,hi,scale=False):"+code.split(":",1)[1].replace("sig=np.sign(S.c-S.c.shift(100))","sig=confirm(S)")
exec(code)
res={}
for nm,f in [('frozen',base_run),('VA',runA)]:
    T=pd.concat([f(SS[r],r,pd.Timestamp('2000-01-01'),pd.Timestamp('2026-06-27')) for r in SPEC])
    T=T[~((T.root=='MNQ')&(T.exit>='2026-06-29'))]
    res[nm]=T
    print(nm,'n',len(T),'net',round(T.pnl.sum()),'mean',round(T.pnl.mean()),'median_bars',T.bars.median(),'mean_bars',round(T.bars.mean(),1),'<=2bar',(T.bars<=2).sum(),'worst',round(T.pnl.min()))
    print('  per-inst n',T.groupby('root').size().to_dict(),'net',T.groupby('root').pnl.sum().round().to_dict())
