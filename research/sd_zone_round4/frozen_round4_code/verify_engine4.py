"""Slow bar-by-bar vs vectorised engine on ALL real zones + one random draw (PREREG §11)."""
import numpy as np
from common import load
from engine import Market
from zones4 import detect, life_end, DEFS
from engine4 import run_zones, random_zones, TimeGroups
from slow4 import slow_zone

tot = 0
for inst in ("MNQ", "MES"):
    b = load(inst); mk = Market(b); life = life_end(b); tg = TimeGroups(b)
    rng = np.random.default_rng(12345)
    for name in DEFS:
        Z, nw = detect(b, name, life)
        for lab, ZZ in (("real", Z), ("rand", random_zones(b, Z, tg, life, rng))):
            r = run_zones(mk, ZZ); bad = 0
            for k in range(len(ZZ["i"])):
                s = slow_zone(b, int(ZZ["i"][k]), int(ZZ["kind"][k]), ZZ["top"][k], ZZ["bot"][k], ZZ["cc"][k], int(ZZ["life"][k]))
                ok = s["status"] == int(r["status"][k])
                for key in ("H2", "H15", "T2"):
                    v = r[key]["pnl"][k]
                    ok &= (np.isnan(v) if s[key] is None else (np.isfinite(v) and abs(v - s[key]) < 1e-6))
                if not ok:
                    bad += 1
                    if bad <= 3: print("MISMATCH", inst, name, lab, k, s, int(r["status"][k]), [r[x]["pnl"][k] for x in ("H2", "H15", "T2")])
            tot += bad
            print(f"{inst} {name} {lab}: zones {len(ZZ['i'])} checked {len(ZZ['i'])} mismatches {bad}", flush=True)
print("TOTAL MISMATCHES", tot)
