"""POST-HOC descriptive splits (NOT prereg tests; no verdict attached)."""
import numpy as np
from common import load, OOS_FIRST_DAY
OOS0 = OOS_FIRST_DAY.toordinal()
def s(p):
    p = np.asarray(p); gl = -p[p < 0].sum()
    return f"n={len(p)} net=${p.sum():.0f} PF={p[p>0].sum()/gl if gl>0 else float('inf'):.2f} win={100*(p>0).mean() if len(p) else 0:.0f}%"
for inst in ("MNQ", "MES"):
    b = load(inst)
    for name in ("L_FULL", "L_FULL_NY", "L_EDGE"):
        z = np.load(f"../results/ledger_{inst}_{name}.npz")
        for m in ("H2", "T2"):
            tk = z[f"{m}_taken"]; pnl = z[f"{m}_pnl"]; bar = z[f"{m}_bar"]; d = b.tday[np.maximum(bar, 0)]
            em = b.et_min[np.maximum(bar, 0)]
            for per, pm in (("2yr", tk), ("IS", tk & (d < OOS0)), ("OOS", tk & (d >= OOS0))):
                print(f"{inst} {name} {m} {per}: ALL {s(pnl[pm])} | demand {s(pnl[pm & (z['kind']==1)])} | supply {s(pnl[pm & (z['kind']==-1)])}")
            pm = tk
            print(f"   {inst} {name} {m} 2yr entry 09:30-10:55: {s(pnl[pm & (em<=655)])} | 11:00-15:50: {s(pnl[pm & (em>655)])}")
            # half-years
            yrs = []
            for lo, hi in (("2024-07-01","2025-01-01"),("2025-01-01","2025-07-01"),("2025-07-01","2026-01-01"),("2026-01-01","2026-07-01")):
                from datetime import date
                a = date.fromisoformat(lo).toordinal(); c = date.fromisoformat(hi).toordinal()
                yrs.append(f"{lo[:7]}: {pnl[pm & (d>=a) & (d<c)].sum():.0f} ({(pm & (d>=a) & (d<c)).sum()})")
            print("   half-years:", " | ".join(yrs))
    # buy-and-hold drift over the sample, for context
    print(f"{inst} first close {b.c[0]:.2f} last close {b.c[-1]:.2f}")
