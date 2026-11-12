# Test #433: descriptive V-C prediction (f=0.5 fixed a priori). Pre-seal data only; run with cwd=discovery_2026-10-09.
import pandas as pd,numpy as np
src=open('test_r3.py').read(); exec(src.split("out={}")[0])
SPEC.pop('MBT'); SS={r:cont(r) for r in SPEC}
code=src.split("def run(")[1].split("def bh(")[0]
code="def runC(S,r,lo,hi,scale=False):"+code.split(":",1)[1].replace(
 "st=ext-pos*3*a\n",
 "st=ext-pos*3*a\n            if pos*(ext-ent['px'])>0:\n                gb=ent['px']+0.5*(ext-ent['px'])\n                st=max(st,gb) if pos>0 else min(st,gb)\n")
assert "gb=" in code; exec(code)
for nm,f in [('frozen',run),('VC',runC)]:
    T=pd.concat([f(SS[r],r,pd.Timestamp('2000-01-01'),pd.Timestamp('2026-06-27')) for r in SPEC])
    T=T[~((T.root=='MNQ')&(T.exit>='2026-06-29'))]
    print(nm,'n',len(T),'median_bars',T.bars.median(),'mean_bars',round(T.bars.mean(),1),'<=2bar',(T.bars<=2).sum(),'n_per_inst',T.groupby('root').size().to_dict())
